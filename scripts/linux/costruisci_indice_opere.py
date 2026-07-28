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
    return re.sub(r"[^\w\s]", " ", s.lower()).strip()


def categoria_di(nome_categoria: str) -> str:
    """A quale delle nostre tre categorie appartiene questa categoria di Wikipedia?"""
    n = nome_categoria.replace("_", " ").lower()
    if any(r in n for r in RIFIUTA):
        return ""
    for cat, prefissi in PREFISSI.items():
        if n.startswith(prefissi):
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", default=str(Path.home() / "dump_wikipedia"))
    parser.add_argument("--uscita", default=str(Path.home() / "dump_wikipedia" / "indice_opere.json"))
    args = parser.parse_args()

    cartella = Path(args.dump)
    fp_page = cartella / "itwiki-latest-page.sql.gz"
    fp_cat = cartella / "itwiki-latest-categorylinks.sql.gz"
    fp_lt = cartella / "itwiki-latest-linktarget.sql.gz"
    for fp in (fp_page, fp_cat, fp_lt):
        if not fp.exists():
            sys.exit(f"ERRORE: manca {fp}")

    inizio = time.time()
    print("leggo le pagine (namespace 0, cioe' le voci vere)...", flush=True)
    titolo_di = {}
    for n, campi in enumerate(leggi_tuple(fp_page)):
        # page: id, namespace, title, ...
        if len(campi) < 3 or campi[1].strip() != "0":
            continue
        try:
            titolo_di[int(campi[0])] = pulisci(campi[2]).replace("_", " ")
        except ValueError:
            continue
        if len(titolo_di) % 500000 == 0:
            print(f"  {len(titolo_di):,} voci...", flush=True)
    print(f"  voci trovate: {len(titolo_di):,}  ({time.time()-inizio:.0f}s)", flush=True)

    # ⚠️ Lo schema di MediaWiki e' cambiato: categorylinks non ha piu' la colonna
    # cl_to col nome della categoria, ma cl_target_id, un riferimento numerico alla
    # tabella linktarget. Scoperto il 2026-07-28 leggendo il CREATE TABLE del dump
    # dopo che l'indice era uscito con ZERO opere: un fallimento totale e uniforme,
    # che in questo progetto significa sempre un'assunzione sbagliata mia e mai un
    # dato reale. Il primo lettore prendeva cl_sortkey credendo fosse il nome.
    print("leggo i nomi delle categorie (linktarget, namespace 14)...", flush=True)
    nome_categoria = {}
    for campi in leggi_tuple(fp_lt):
        # linktarget: lt_id, lt_namespace, lt_title
        if len(campi) < 3 or campi[1].strip() != "14":
            continue
        try:
            nome_categoria[int(campi[0])] = pulisci(campi[2])
        except ValueError:
            continue
    print(f"  categorie trovate: {len(nome_categoria):,}", flush=True)
    if not nome_categoria:
        sys.exit("ERRORE: nessuna categoria letta da linktarget: schema cambiato di nuovo?")

    print("leggo i collegamenti alle categorie...", flush=True)
    indice = {}
    esaminate = 0
    for campi in leggi_tuple(fp_cat):
        # categorylinks: cl_from, cl_sortkey, cl_timestamp, cl_sortkey_prefix,
        #                cl_type, cl_collation_id, cl_target_id
        if len(campi) < 7:
            continue
        esaminate += 1
        try:
            pid = int(campi[0])
            target = int(campi[6])
        except ValueError:
            continue
        titolo = titolo_di.get(pid)
        if not titolo:
            continue
        nome_cat = nome_categoria.get(target)
        if not nome_cat:
            continue
        cat = categoria_di(nome_cat)
        if not cat:
            continue
        chiave = normalizza(titolo)
        if chiave and chiave not in indice:
            indice[chiave] = [titolo, cat, nome_cat.replace("_", " ")]
        if esaminate % 5000000 == 0:
            print(f"  {esaminate:,} collegamenti, {len(indice):,} opere...", flush=True)

    uscita = Path(args.uscita)
    uscita.write_text(json.dumps(indice, ensure_ascii=False), encoding="utf-8")
    per_cat = {}
    for _, (_, cat, _) in indice.items():
        per_cat[cat] = per_cat.get(cat, 0) + 1

    print("\n" + "=" * 60, flush=True)
    print(f"  opere indicizzate : {len(indice):,}", flush=True)
    for cat, n in sorted(per_cat.items(), key=lambda x: -x[1]):
        print(f"    {cat:8} {n:>9,}", flush=True)
    print(f"  file              : {uscita}  ({uscita.stat().st_size/1e6:.0f} MB)", flush=True)
    print(f"  tempo             : {time.time()-inizio:.0f}s", flush=True)


if __name__ == "__main__":
    main()
