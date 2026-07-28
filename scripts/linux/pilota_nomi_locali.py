#!/usr/bin/env python3
"""Pilota: quanto varrebbe estrarre i NOMI in locale, prima di interrogare i database?

Perche' esiste (2026-07-28, idea dell'utente). Oggi la catena e' modello-primo: il
modello cloud legge il testo e gli archivi intervengono DOPO, per approvare o bocciare.
L'idea opposta e' fare una scrematura a monte: riconoscere in locale i nomi contenuti
nella puntata, passarli dai database, e dare ai modelli solo cio' che e' gia' filtrato.

Questo script NON costruisce quella pipeline: misura se ha senso costruirla. Prima di
cambiare l'impianto serve sapere quanto se ne recupererebbe davvero, ed e' una domanda
a cui si risponde con un numero, non con un'opinione.

Gira sul K16, dove c'e' la GPU (RTX 5070, verificata libera il 2026-07-28) e dove
`torch` con CUDA e `transformers` sono gia' installati: nessun pacchetto nuovo, solo i
pesi del modello da scaricare. Costo in budget cloud: ZERO.

Le tre misure, da leggere insieme:
  1. RECUPERO AUTORE  - per ciascuna opera nota, il suo autore e' fra le entita'
     riconosciute? E' il caso che la pipeline sa gia' sfruttare: una voce di solo
     autore viene conservata col link alle sue opere (misurato 6/6 sul banco verifica).
  2. RECUPERO TITOLO  - il titolo dell'opera e' fra le entita'? Atteso raro e va
     misurato, non dato per scontato: i riconoscitori italiani etichettano persone,
     luoghi e organizzazioni, non opere.
  3. VOLUME           - quante entita' distinte per episodio, cioe' quante
     interrogazioni ai database costerebbe. Una regola grezza sulle maiuscole ne dava
     86 per episodio, ma dentro ci sono "Allora", "Ciao", "Ci": un riconoscitore vero
     dovrebbe tagliare parecchio, ed e' questo il numero che decide la fattibilita'.

Uso (sul K16, dentro ~/ilvolo-env):
    export ILVOLO_DATA_DIR=/percorso/dati ILVOLO_LOGS_DIR=/percorso/logs
    python3 scripts/linux/pilota_nomi_locali.py
    python3 scripts/linux/pilota_nomi_locali.py --modello osiria/bert-italian-cased-ner

⚠️ Esportare SEMPRE le variabili d'ambiente: senza, dati_root() cade sulla copia locale
del repo invece che sullo share, errore gia' fatto due volte in una notte.

NIENTE caratteri fuori ASCII nell'output: la console Windows (cp1252) va in
UnicodeEncodeError e la misura muore a meta'.
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root, logs_root  # noqa: E402
import test_qualita_identificazione as banco  # noqa: E402

# Modello predefinito: multilingua, copre l'italiano, etichette PER/ORG/LOC. Si cambia
# con --modello senza toccare il codice, cosi' confrontare due riconoscitori costa una
# riga di comando e non una modifica.
MODELLO_DEFAULT = "Davlan/xlm-roberta-base-ner-hrl"


def ascii_sicuro(s: str) -> str:
    return (str(s) or "").encode("ascii", "replace").decode("ascii")


def carica_riconoscitore(nome_modello: str):
    """Carica il modello e dice DOVE gira. Se cade sulla CPU senza accorgersene, la
    misura di velocita' non vale nulla: va detto a schermo, non lasciato indovinare."""
    import torch
    from transformers import pipeline

    su_gpu = torch.cuda.is_available()
    print(f"modello: {nome_modello}", flush=True)
    print(f"CUDA disponibile: {su_gpu}"
          + (f" ({torch.cuda.get_device_name(0)})" if su_gpu else " -- gira su CPU"),
          flush=True)
    return pipeline("token-classification", model=nome_modello,
                    aggregation_strategy="simple", device=0 if su_gpu else -1)


def entita_di(riconoscitore, testo: str) -> list[dict]:
    """Entita' riconosciute nel testo intero.

    Il taglio a 6.000 caratteri della pipeline esiste solo per i limiti del modello
    cloud: qui il testo si da' intero e il riconoscitore lo finestra da solo. Si tiene
    una finestra esplicita solo perche' i modelli BERT hanno un tetto di token, ma e'
    un dettaglio dell'architettura, non una scelta di prodotto."""
    pezzi = [testo[i:i + 2000] for i in range(0, len(testo), 2000)]
    fuori = []
    for pezzo in pezzi:
        try:
            fuori.extend(riconoscitore(pezzo))
        except Exception as e:
            print(f"  (finestra saltata: {ascii_sicuro(e)})", flush=True)
    return fuori


# Parole che non identificano niente da sole. Senza escluderle il confronto regala
# match falsi: misurato il 2026-07-28, "The Greatest" combaciava con l'entita' "The
# Voice" per la sola parola "the", e "New York New York" con un generico "New York".
# Due falsi su diciassette bastavano a gonfiare la misura di 12 punti.
PAROLE_VUOTE = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "del",
                "della", "dei", "delle", "da", "in", "con", "su", "per", "tra", "fra",
                "e", "o", "a", "al", "alla", "the", "of", "and", "in", "to", "my",
                "you", "me", "is", "it", "new", "york"}


def piene_di(s: str) -> set:
    """Parole che identificano davvero qualcosa: senza articoli, preposizioni e simili.
    Serve sia al confronto sia alla regola di distintivita' della scrematura, e deve
    essere UNA sola definizione: due nozioni diverse di 'parola piena' nei due punti
    farebbero misurare una cosa e produrne un'altra."""
    return {w for w in banco._norm(s).split() if w not in PAROLE_VUOTE and len(w) > 2}


def contiene(entita: list[str], cercato: str) -> bool:
    """Il nome cercato compare fra le entita'?

    Confronto per PAROLE, non per caratteri: e' la lezione della verifica esterna, dove
    'Dante Alighieri' contro 'Antonino Pagliaro' dava 0.5 a caratteri e confermava
    un'attribuzione falsa. In piu' le parole vuote non contano, e serve almeno una
    parola PIENA in comune: altrimenti si misura la lingua italiana, non il recupero."""
    cercato_pieno = piene_di(cercato)
    if not cercato_pieno:
        # Titolo fatto di sole parole vuote: si esige la corrispondenza completa.
        atteso = banco._norm(cercato)
        return any(banco._norm(e) == atteso for e in entita)
    for e in entita:
        e_pieno = piene_di(e)
        if not e_pieno:
            continue
        comuni = cercato_pieno & e_pieno
        if comuni and len(comuni) >= min(len(cercato_pieno), len(e_pieno)) * 0.6:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modello", default=MODELLO_DEFAULT)
    parser.add_argument("--episodi", nargs="*", help="date da usare (default: quelle con ground truth)")
    args = parser.parse_args()

    trascrizioni = dati_root(ROOT) / "trascrizioni"
    ground_truth = banco.carica_insieme_riferimento()
    episodi = args.episodi or sorted(ground_truth)
    episodi = [d for d in episodi if (trascrizioni / f"{d}.json").exists()]
    if not episodi:
        sys.exit("ERRORE: nessun episodio del ground truth ha una trascrizione. "
                 "ILVOLO_DATA_DIR e' impostata?")

    riconoscitore = carica_riconoscitore(args.modello)
    print(f"episodi con ground truth e trascrizione: {len(episodi)}\n", flush=True)

    autori_trovati = autori_totali = titoli_trovati = titoli_totali = 0
    per_episodio = {}
    inizio = time.time()

    for data_str in episodi:
        d = json.loads((trascrizioni / f"{data_str}.json").read_text(encoding="utf-8"))
        testo = " ".join(s.get("text", "") for s in d.get("segments", []))
        t0 = time.time()
        ents = entita_di(riconoscitore, testo)
        durata = time.time() - t0
        nomi = sorted({e.get("word", "").strip() for e in ents if len(e.get("word", "").strip()) > 2})

        gt = ground_truth[data_str]
        ok_a = ok_t = 0
        mancati = []
        for opera in gt["opere"]:
            autore = (opera.get("autore") or "").strip()
            titolo = (opera.get("titolo") or "").strip()
            if autore:
                autori_totali += 1
                if contiene(nomi, autore):
                    autori_trovati += 1
                    ok_a += 1
                else:
                    mancati.append(f"autore:{autore}")
            if titolo:
                titoli_totali += 1
                if contiene(nomi, titolo):
                    titoli_trovati += 1
                    ok_t += 1
        per_episodio[data_str] = {
            "caratteri": len(testo), "entita_distinte": len(nomi),
            "opere": len(gt["opere"]), "autori_trovati": ok_a, "titoli_trovati": ok_t,
            "secondi": round(durata, 1), "mancati": mancati,
        }
        print(ascii_sicuro(
            f"  {data_str}: {len(testo):>6} car | {len(nomi):>4} entita' distinte | "
            f"autori {ok_a}/{len([o for o in gt['opere'] if (o.get('autore') or '').strip()])} | "
            f"titoli {ok_t}/{len(gt['opere'])} | {durata:.1f}s"), flush=True)

    medie = sum(v["entita_distinte"] for v in per_episodio.values()) / max(len(per_episodio), 1)
    print("\n" + "=" * 72, flush=True)
    print("MISURE DEL PILOTA", flush=True)
    print("=" * 72, flush=True)
    print(f"  RECUPERO AUTORE : {autori_trovati}/{autori_totali} "
          f"({100*autori_trovati/max(autori_totali,1):.0f}%)  <- decide se l'idea regge", flush=True)
    print(f"  RECUPERO TITOLO : {titoli_trovati}/{titoli_totali} "
          f"({100*titoli_trovati/max(titoli_totali,1):.0f}%)  <- atteso basso", flush=True)
    print(f"  VOLUME          : {medie:.0f} entita' distinte per episodio "
          f"(~{medie*2106:,.0f} interrogazioni sul corpus, prima della cache)", flush=True)
    print(f"  tempo totale    : {time.time()-inizio:.0f}s per {len(episodi)} episodi", flush=True)

    cartella = logs_root(ROOT) / "banco_pilota_nomi"
    cartella.mkdir(parents=True, exist_ok=True)
    fp = cartella / f"{date.today()}_{datetime.now().strftime('%H%M')}.json"
    fp.write_text(json.dumps({
        "modello": args.modello, "data": str(date.today()),
        "recupero_autore": [autori_trovati, autori_totali],
        "recupero_titolo": [titoli_trovati, titoli_totali],
        "entita_medie_per_episodio": round(medie, 1),
        "per_episodio": per_episodio,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nArchiviato in {fp}", flush=True)


if __name__ == "__main__":
    main()
