#!/usr/bin/env python3
"""Costruisce un indice LOCALE delle opere, dai dump di Wikipedia italiana.

Perche' esiste (2026-07-28). La scrematura a monte - classificare i nomi trovati
nell'episodio prima di darli al modello - funziona come idea ma non con chiamate
remote: misurata, costava 400 secondi per episodio, cioe' 234 ore sul corpus, perche'
Wikidata limita le richieste e i nomi distinti sono ~46.800. Con l'archivio in casa la
stessa domanda si risponde in microsecondi e si puo' essere molto piu' severi sul
rumore, perche' il tempo non e' piu' la risorsa scarsa.

Perche' Wikipedia italiana e non il dump di Wikidata: 560 MB contro ~130 GB compressi,
e le categorie italiane classificano gia' le opere nel modo che ci serve ("Film del
1979", "Romanzi di autori italiani", "Singoli del 2005", "Opere liriche"). Il corpus e'
italiano, quindi anche i titoli lo sono.

Cosa produce: un file compatto titolo normalizzato -> (titolo vero, categoria,
categoria di Wikipedia che l'ha deciso). La categoria di Wikipedia viene conservata
perche' quando un giorno un titolo risultera' classificato male, si deve poter vedere
DA COSA e' stato deciso, invece di indovinare.

Uso (sul K16):
    python3 scripts/linux/costruisci_indice_opere.py --dump ~/dump_wikipedia

NIENTE caratteri fuori ASCII nell'output.
"""
import argparse
import gzip
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

# Prefissi di categoria di Wikipedia italiana che identificano un'opera, per macro
# categoria. Sono prefissi e non parole sparse: "Film del 1979" va preso, "Attori di
# film" no - la seconda parla di persone, e in questo progetto una persona confusa con
# un'opera e' l'errore piu' frequente di tutti.
PREFISSI = {
    "film": ("film ", "film_", "cortometraggi ", "serie televisive ", "serie tv ",
             "miniserie televisive ", "film d", "film a", "sitcom "),
    "libro": ("romanzi ", "opere letterarie ", "saggi ", "raccolte poetiche ",
              "raccolte di racconti ", "poemi ", "racconti ", "libri ", "fumetti ",
              "opere teatrali ", "tragedie ", "commedie teatrali ", "romanzi_"),
    "musica": ("singoli ", "album ", "brani musicali ", "canzoni ", "opere liriche ",
               "composizioni ", "colonne sonore ", "ep ", "sinfonie ", "concerti "),
}

# Categorie da rifiutare SEMPRE, anche se il prefisso combacia: parlano di persone o di
# insiemi, non di opere singole.
RIFIUTA = ("attori", "registi", "sceneggiatori", "produttori", "musicisti", "cantanti",
           "gruppi musicali", "scrittori", "poeti", "personaggi", "premi", "festival",
           "case editrici", "case di produzione", "etichette")


def normalizza(s: str) -> str:
    """Stessa normalizzazione del resto della pipeline: senza accenti, minuscolo, senza
    punteggiatura. Deve combaciare con quella usata a valle, altrimenti l'indice trova
    cose che la verifica poi non riconosce."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    # La punteggiatura diventa spazio (non sparisce): cosi' "l'aereo" e "l ' aereo" -
    # la seconda e' come il riconoscitore restituisce le entita' - finiscono nella
    # stessa forma. Poi gli spazi si comprimono, altrimenti "Mamma, ho perso l'aereo"
    # produce due spazi dopo la virgola e non combacia con niente: e' il motivo per cui
    # meta' dei titoli noti risultava assente dall'indice il 2026-07-28.
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


# Le categorie INGLESI mettono l'anno davanti ("1984 films", "1965 songs", "1949
# British novels"), quindi il match per prefisso che funziona sull'italiano li' non
# serve a niente: si cerca la parola chiave ovunque nel nome.
PAROLE_EN = {
    "film": ("films", "television series", "tv series", "miniseries", "sitcoms"),
    "libro": ("novels", "books", "short story collections", "plays", "poems",
              "poetry collections", "essays", "comics", "graphic novels"),
    "musica": ("songs", "singles", "albums", "operas", "symphonies", "compositions",
               "soundtracks", "eps"),
}

# Rifiuti in inglese: le stesse categorie di persone e insiemi, che in inglese
# contengono comunque la parola chiave ("American film directors" contiene "film",
# "Songwriters" contiene "song"). Senza questi, meta' dell'indice inglese sarebbero
# persone.
RIFIUTA_EN = ("directors", "actors", "actresses", "writers", "novelists", "musicians",
              "singers", "songwriters", "composers", "producers", "screenwriters",
              "characters", "awards", "festivals", "publishers", "record labels",
              "studios", "bands", "musical groups", "critics", "editors", "artists")


def categoria_di(nome_categoria: str, lingua: str = "it") -> str:
    """A quale delle nostre tre categorie appartiene questa categoria di Wikipedia?"""
    n = nome_categoria.replace("_", " ").lower()
    if lingua == "it":
        if any(r in n for r in RIFIUTA):
            return ""
        for cat, prefissi in PREFISSI.items():
            if n.startswith(prefissi):
                return cat
        return ""
    if any(r in n for r in RIFIUTA_EN):
        return ""
    for cat, parole in PAROLE_EN.items():
        if any(p in n for p in parole):
            return cat
    return ""


def leggi_tuple(percorso: Path):
    """Legge le righe INSERT di un dump MySQL e restituisce le tuple, una a una.

    Scritto a mano invece di caricare tutto in memoria: categorylinks ha decine di
    milioni di righe e leggerlo tutto insieme non serve a niente. Gestisce gli apici
    protetti dentro le stringhe, che sono la ragione per cui una regex ingenua su
    questi dump da' risultati sbagliati in silenzio."""
    with gzip.open(percorso, "rt", encoding="utf-8", errors="replace") as f:
        for riga in f:
            if not riga.startswith("INSERT INTO"):
                continue
            i = riga.find(" VALUES ")
            if i < 0:
                continue
            resto = riga[i + 8:]
            campo, campi, dentro_stringa, protetto, dentro_tupla = "", [], False, False, False
            for ch in resto:
                if protetto:
                    campo += ch
                    protetto = False
                elif ch == "\\":
                    campo += ch
                    protetto = True
                elif ch == "'":
                    dentro_stringa = not dentro_stringa
                    campo += ch
                elif dentro_stringa:
                    campo += ch
                elif ch == "(":
                    dentro_tupla, campi, campo = True, [], ""
                elif ch == ",":
                    if dentro_tupla:
                        campi.append(campo.strip())
                        campo = ""
                elif ch == ")":
                    if dentro_tupla:
                        campi.append(campo.strip())
                        yield campi
                        dentro_tupla, campo = False, ""
                else:
                    campo += ch


def pulisci(valore: str) -> str:
    v = valore.strip()
    if v.startswith("'") and v.endswith("'"):
        v = v[1:-1]
    return v.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def costruisci(cartella: Path, lingua: str, indice: dict) -> None:
    """Aggiunge al dizionario le opere di una Wikipedia. L'italiano si carica per
    primo e vince sulle chiavi in comune: il corpus e' italiano, quindi quando lo
    stesso titolo esiste in entrambe le lingue quella giusta e' l'italiana."""
    sigla = "itwiki" if lingua == "it" else "enwiki"
    fp_page = cartella / f"{sigla}-latest-page.sql.gz"
    fp_cat = cartella / f"{sigla}-latest-categorylinks.sql.gz"
    fp_lt = cartella / f"{sigla}-latest-linktarget.sql.gz"
    for fp in (fp_page, fp_cat, fp_lt):
        if not fp.exists():
            print(f"  ({lingua}: manca {fp.name}, salto questa lingua)", flush=True)
            return

    t0 = time.time()
    print("", flush=True)
    print(f"[{lingua}] leggo le pagine...", flush=True)
    titolo_di = {}
    for campi in leggi_tuple(fp_page):
        if len(campi) < 3 or campi[1].strip() != "0":
            continue
        try:
            titolo_di[int(campi[0])] = pulisci(campi[2]).replace("_", " ")
        except ValueError:
            continue
    print(f"  voci: {len(titolo_di):,}  ({time.time()-t0:.0f}s)", flush=True)

    print(f"[{lingua}] leggo i nomi delle categorie...", flush=True)
    nome_categoria = {}
    for campi in leggi_tuple(fp_lt):
        if len(campi) < 3 or campi[1].strip() != "14":
            continue
        try:
            nome_categoria[int(campi[0])] = pulisci(campi[2])
        except ValueError:
            continue
    print(f"  categorie: {len(nome_categoria):,}", flush=True)
    if not nome_categoria:
        sys.exit(f"ERRORE: nessuna categoria letta da {fp_lt.name}: schema cambiato?")

    print(f"[{lingua}] leggo i collegamenti...", flush=True)
    prima = len(indice)
    for campi in leggi_tuple(fp_cat):
        if len(campi) < 7:
            continue
        try:
            pid, target = int(campi[0]), int(campi[6])
        except ValueError:
            continue
        titolo = titolo_di.get(pid)
        if not titolo:
            continue
        nome_cat = nome_categoria.get(target)
        if not nome_cat:
            continue
        cat = categoria_di(nome_cat, lingua)
        if not cat:
            continue
        voce = [titolo, cat, nome_cat.replace("_", " "), lingua]
        chiavi = [normalizza(titolo)]
        if "(" in titolo:
            chiavi.append(normalizza(titolo.split("(")[0]))
        for chiave in chiavi:
            if chiave and chiave not in indice:
                indice[chiave] = voce
    print(f"  opere aggiunte: {len(indice)-prima:,}  ({time.time()-t0:.0f}s)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", default=str(Path.home() / "dump_wikipedia"))
    parser.add_argument("--lingue", default="it,en",
                        help="lingue da includere, in ordine di precedenza")
    parser.add_argument("--uscita", default=str(Path.home() / "dump_wikipedia" / "indice_opere.json"))
    args = parser.parse_args()

    inizio = time.time()
    indice = {}
    for lingua in args.lingue.split(","):
        costruisci(Path(args.dump), lingua.strip(), indice)

    uscita = Path(args.uscita)
    uscita.write_text(json.dumps(indice, ensure_ascii=False), encoding="utf-8")
    per_cat, per_lingua = {}, {}
    for voce in indice.values():
        per_cat[voce[1]] = per_cat.get(voce[1], 0) + 1
        per_lingua[voce[3]] = per_lingua.get(voce[3], 0) + 1

    print("", flush=True)
    print("=" * 60, flush=True)
    print(f"  opere indicizzate : {len(indice):,}", flush=True)
    for cat, n in sorted(per_cat.items(), key=lambda x: -x[1]):
        print(f"    {cat:8} {n:>9,}", flush=True)
    for lg, n in sorted(per_lingua.items()):
        print(f"    lingua {lg:3} {n:>9,}", flush=True)
    print(f"  file              : {uscita}  ({uscita.stat().st_size/1e6:.0f} MB)", flush=True)
    print(f"  tempo             : {time.time()-inizio:.0f}s", flush=True)


if __name__ == "__main__":
    main()
