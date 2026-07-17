# Estrae riferimenti a film, libri e musica dalle trascrizioni WhisperX.
#
# Input:  data/trascrizioni/<data>.json
# Output: data/riferimenti/<data>.json  — lista di riferimenti, uno per hit.
#
# Ogni voce ha: id, categoria (film/libro/musica), titolo (vuoto → da compilare),
# testo (citazione estratta), start/end (secondi), episodio_data.
# Rieseguire lo script su un file già arricchito NON sovrascrive i campi
# già compilati a mano (merge per id).
#
# Uso: python scripts/genera_riferimenti.py [data1 data2 ...]
#      senza argomenti processa tutti i file in data/trascrizioni/.

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATI = dati_root(ROOT)
TRASCRIZIONI_DIR = DATI / "trascrizioni"
RIFERIMENTI_DIR = DATI / "riferimenti"

# ── Pattern di rilevamento ──────────────────────────────────────────────────
# Ogni pattern è (nome_categoria, regex_sul_testo_di_una_finestra)
# La finestra è il testo unito di 3 segmenti consecutivi (~30–40 secondi).

PATTERN_FILM = [
    r"\bfilm\b",
    r"\bcinema\b",
    r"\bregista\b",
    r"\bpellicola\b",
    r"\bprotagonista\b",
    r"\bsceneggiatura\b",
    # "ho visto un film" / "ho visto questo film" — richiede "film" vicino
    r"\bho visto\b.{0,40}\bfilm\b",
    r"\bfilm\b.{0,40}\bho visto\b",
    # "vi faccio vedere / voglio far vedere un film"
    r"\bfar?\s+vedere\b.{0,40}\bfilm\b",
    # "stasera vediamo" con "film" a portata
    r"\bstasera\b.{0,30}\bfilm\b",
    r"\bfilm\b.{0,30}\bstasera\b",
]

PATTERN_LIBRO = [
    r"\blibro\b",
    r"\blibri\b",
    r"\bromanzo\b",
    r"\bsaggio\b",
    # "leggo / ho letto / leggendo" con contesto letterario vicino
    r"\bho letto\b",
    r"\blegge(re|ndo)\b.{0,30}\b(di|un|questo|il)\b",
    r"\bpagine\b.{0,30}\b(di|del|della)\b",
    r"\bautore\b.{0,20}\b(di|del|della)\b",
    r"\bnarrat(ore|rice)\b",
]

PATTERN_MUSICA = [
    r"\bcanzone\b",
    r"\bcanzoni\b",
    r"\balbum\b",
    r"\bcantante\b",
    r"\bsinger\b",
    r"\bmusicista\b",
    # "disco di" / "song di"
    r"\bdisco\b.{0,20}\bdi\b",
    r"\bsong\b.{0,20}\bdi\b",
    # "musica di" con soggetto specifico
    r"\bmusica\b.{0,15}\bdi\b",
    # "suona" + contesto musicale
    r"\bsuona\b.{0,30}\b(chitarra|piano|basso|batteria|tromba|violino)\b",
    # "era una canzone del", "era il 197..."  - periodo musicale
    r"\bcanzone\b.{0,30}\b(del|degli|anni)\b",
]

CATEGORIE = [
    ("film", PATTERN_FILM),
    ("libro", PATTERN_LIBRO),
    ("musica", PATTERN_MUSICA),
]

# Compilazione una volta sola (flag IGNORECASE + UNICODE)
COMPILED = [
    (cat, [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns])
    for cat, patterns in CATEGORIE
]


def categorize(testo):
    """Restituisce la prima categoria il cui pattern corrisponde al testo.

    Ritorna None se nessun pattern corrisponde.
    """
    for cat, patterns in COMPILED:
        for rx in patterns:
            if rx.search(testo):
                return cat
    return None


COOLDOWN_SEC = 60  # secondi minimi tra due hit della stessa categoria


def cerca_riferimenti(segments):
    """Scansiona i segmenti e raccoglie riferimenti senza duplicati.

    Per ogni categoria tiene traccia dell'ultimo hit e applica un cooldown
    di COOLDOWN_SEC secondi: hit troppo vicini vengono ignorati.
    Restituisce lista di dict {categoria, testo, start, end, seg_idx}.
    """
    n = len(segments)
    last_hit = {}   # categoria -> timestamp ultimo hit

    risultati = []
    for i in range(n):
        seg = segments[i]
        # Finestra: segmento corrente + 2 successivi per contesto
        grp = segments[i: min(n, i + 3)]
        testo = " ".join(s.get("text", "").strip() for s in grp)
        start = seg["start"]
        end = grp[-1]["end"]

        cat = categorize(testo)
        if cat is None:
            continue

        if start - last_hit.get(cat, -COOLDOWN_SEC) < COOLDOWN_SEC:
            continue  # troppo vicino all'ultimo hit della stessa categoria

        last_hit[cat] = start
        risultati.append({
            "categoria": cat,
            "testo": testo.strip(),
            "start": round(start, 2),
            "end": round(end, 2),
            "seg_idx": i,
        })

    return risultati


def genera(data_str):
    """Processa una singola puntata ed aggiorna il file di output."""
    src = TRASCRIZIONI_DIR / f"{data_str}.json"
    if not src.exists():
        print(f"  manca {src}, salto")
        return

    trascrizione = json.loads(src.read_text(encoding="utf-8"))
    segments = trascrizione.get("segments", [])

    # Carica file esistente per il merge (preserva campi manuali)
    dest = RIFERIMENTI_DIR / f"{data_str}.json"
    esistenti = {}
    if dest.exists():
        for r in json.loads(dest.read_text(encoding="utf-8")):
            esistenti[r["id"]] = r

    grezzi = cerca_riferimenti(segments)

    trovati = []
    seen_ids = set()
    contatore = {"film": 0, "libro": 0, "musica": 0}

    for hit in grezzi:
        cat = hit["categoria"]
        # ID stabile basato su categoria e indice segmento
        rid = f"{data_str}-{cat}-{hit['seg_idx']:04d}"
        seen_ids.add(rid)
        contatore[cat] += 1

        prec = esistenti.get(rid, {})
        trovati.append({
            "id": rid,
            "categoria": cat,
            "titolo": prec.get("titolo", ""),       # da compilare a mano
            "anno": prec.get("anno", ""),
            "autore": prec.get("autore", ""),
            "note": prec.get("note", ""),
            "testo": hit["testo"],
            "start": hit["start"],
            "end": hit["end"],
            "episodio_data": data_str,
        })

    # Mantieni voci aggiunte a mano (id non rigenerati dallo script)
    for rid, voce in esistenti.items():
        if rid not in seen_ids:
            trovati.append(voce)

    trovati.sort(key=lambda x: x["start"])

    RIFERIMENTI_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(trovati, ensure_ascii=False, indent=2), encoding="utf-8")
    totale = sum(contatore.values())
    print(
        f"  {data_str}: {totale} hit "
        f"(film={contatore['film']}, libri={contatore['libro']}, musica={contatore['musica']}) "
        f"-> {dest.relative_to(ROOT)}"
    )


def main():
    """Punto di ingresso: processa le date passate come argomenti o tutte."""
    if len(sys.argv) > 1:
        date_list = sys.argv[1:]
    else:
        date_list = sorted(p.stem for p in TRASCRIZIONI_DIR.glob("*.json"))

    if not date_list:
        print("Nessuna trascrizione trovata in", TRASCRIZIONI_DIR)
        return

    print(f"Genero riferimenti per {len(date_list)} puntate...")
    for d in date_list:
        genera(d)


if __name__ == "__main__":
    main()
