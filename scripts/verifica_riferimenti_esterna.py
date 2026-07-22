# Verifica a posteriori le voci di data/riferimenti/*.json (categoria libro/film/musica)
# contro un database ESTERNO reale (Open Library per i libri, TMDB per i film,
# MusicBrainz per la musica) invece che con un altro LLM che giudica se stesso -
# nessuno dei tre servizi richiede pagamento (vedi project_costo_zero), verificato
# il 2026-07-22 con ricerche reali sui limiti free-tier attuali:
#   - Open Library Search API: nessuna chiave, ~3 richieste/secondo se ci si
#     identifica con uno User-Agent descrittivo (fatto qui).
#   - TMDB: chiave gratuita gia' presente in ~/'TMDB API.txt', ~40 richieste/secondo.
#   - MusicBrainz: nessuna chiave, ma va rispettato RIGOROSAMENTE 1 richiesta/secondo
#     con uno User-Agent descrittivo, altrimenti risponde 503.
#
# Il confronto e' per SIMILARITA' (difflib, come gia' fatto altrove nel progetto),
# non per uguaglianza esatta: un titolo trovato dalla trascrizione puo' avere rumore
# aggiunto (es. "Divina Commedia" seguito da altro testo di chiacchiera attaccato per
# errore) che romperebbe un confronto esatto pur essendo un riferimento vero.
#
# Automatico per il grosso, come richiesto: sopra una soglia alta la voce viene
# marcata "confermato_esterno": true (nessuna azione ulteriore necessaria); sotto una
# soglia bassa "confermato_esterno": false E aggiunta al report per revisione umana;
# in mezzo, stessa cosa ma con "esito": "dubbio" nel report per distinguerla da un
# probabile falso positivo netto. NON cancella MAI nulla da solo.
#
# Uso: python scripts/verifica_riferimenti_esterna.py [data1 data2 ...]
#      senza argomenti: controlla tutte le voci non ancora verificate in data/riferimenti/

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dati_root import dati_root, logs_root  # noqa: E402

RIF_DIR = dati_root(ROOT) / "riferimenti"
REPORT_PATH = logs_root(ROOT) / "riferimenti_non_confermati.json"

TMDB_KEY_FILE = Path.home() / "TMDB API.txt"
USER_AGENT = "IlVoloDelMattinoArchivio/1.0 (uso non commerciale, archivio fan Radio Deejay)"

SOGLIA_ALTA = 0.72   # sopra: confermato automaticamente
SOGLIA_BASSA = 0.45  # sotto: quasi certamente falso positivo, segnalato come tale
MUSICBRAINZ_SLEEP = 1.05  # poco sopra 1 richiesta/secondo per margine di sicurezza


def _normalizza(s: str) -> float:
    return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()


def _similarita(a: str, b: str) -> float:
    a, b = _normalizza(a), _normalizza(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _tmdb_key() -> str:
    if not TMDB_KEY_FILE.exists():
        print(f"Errore: chiave TMDB non trovata in {TMDB_KEY_FILE}")
        sys.exit(1)
    return TMDB_KEY_FILE.read_text(encoding="utf-8").strip()


def verifica_libro(titolo: str, autore: str) -> tuple[float, str]:
    """Cerca su Open Library, ritorna (similarita' massima, descrizione del match)."""
    try:
        resp = requests.get(
            "https://openlibrary.org/search.json",
            params={"title": titolo, "limit": 5},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except Exception as e:
        return -1.0, f"errore rete: {e}"
    migliore = (0.0, "")
    for d in docs:
        titolo_trovato = d.get("title", "")
        sim_titolo = _similarita(titolo, titolo_trovato)
        autori_trovati = d.get("author_name", []) or []
        sim_autore = max((_similarita(autore, a) for a in autori_trovati), default=0.0)
        # Il titolo pesa piu' dell'autore: un titolo giusto con autore diverso e'
        # comunque un indizio forte di opera reale (es. traduzioni/edizioni diverse).
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3
        if punteggio > migliore[0]:
            migliore = (punteggio, f"{titolo_trovato} — {', '.join(autori_trovati[:2])}")
    return migliore


def verifica_film(titolo: str, autore: str, tmdb_key: str) -> tuple[float, str]:
    """Cerca su TMDB, ritorna (similarita' massima, descrizione del match)."""
    try:
        resp = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params={"api_key": tmdb_key, "query": titolo, "language": "it-IT"},
            timeout=10,
        )
        resp.raise_for_status()
        risultati = resp.json().get("results", [])
    except Exception as e:
        return -1.0, f"errore rete: {e}"
    migliore = (0.0, "")
    for r in risultati[:5]:
        for campo in ("title", "original_title"):
            sim = _similarita(titolo, r.get(campo, ""))
            if sim > migliore[0]:
                anno = (r.get("release_date") or "")[:4]
                migliore = (sim, f"{r.get('title')} ({anno})")
    return migliore


def verifica_musica(titolo: str, autore: str) -> tuple[float, str]:
    """Cerca su MusicBrainz, ritorna (similarita' massima, descrizione del match).
    Il chiamante deve rispettare MUSICBRAINZ_SLEEP tra una chiamata e l'altra.

    IMPORTANTE (trovato con un test reale il 2026-07-22): il titolo va passato SENZA
    virgolette (query per token, non frase esatta) - un titolo trascritto con un
    errore whisper (es. "Cray baby" invece di "Cry Baby") con una frase esatta tra
    virgolette dava 0 risultati, azzerando il punteggio anche se la canzone reale
    esiste. Senza virgolette Lucene usa la sua relevance ranking sui singoli token e
    trova comunque il titolo giusto tra i primi risultati. L'autore invece resta tra
    virgolette (nome proprio, meno soggetto a rumore di trascrizione)."""
    try:
        titolo_pulito = re.sub(r'["\']', "", titolo)
        query = f'recording:({titolo_pulito})' + (f' AND artist:"{autore}"' if autore else "")
        resp = requests.get(
            "https://musicbrainz.org/ws/2/recording",
            params={"query": query, "fmt": "json", "limit": 5},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])
    except Exception as e:
        return -1.0, f"errore rete: {e}"
    migliore = (0.0, "")
    for r in recordings:
        titolo_trovato = r.get("title", "")
        sim_titolo = _similarita(titolo, titolo_trovato)
        artisti = [ac.get("name", "") for ac in r.get("artist-credit", []) if isinstance(ac, dict)]
        sim_autore = max((_similarita(autore, a) for a in artisti), default=0.0)
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3
        if punteggio > migliore[0]:
            migliore = (punteggio, f"{titolo_trovato} — {', '.join(artisti[:2])}")
    return migliore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date YYYY-MM-DD da verificare (default: tutte)")
    parser.add_argument("--limit", type=int, default=0, help="numero massimo di voci da processare in questo run (0 = tutte)")
    args = parser.parse_args()

    tmdb_key = _tmdb_key()

    files = [RIF_DIR / f"{d}.json" for d in args.date] if args.date else sorted(RIF_DIR.glob("*.json"))

    tutte_le_voci = []
    for fp in files:
        if not fp.exists():
            continue
        dati = json.loads(fp.read_text(encoding="utf-8"))
        for r in dati:
            if r.get("titolo") and r.get("categoria") in ("libro", "film", "musica") and "confermato_esterno" not in r:
                tutte_le_voci.append((fp, r))

    if args.limit:
        tutte_le_voci = tutte_le_voci[:args.limit]

    print(f"Verifico {len(tutte_le_voci)} voci contro database esterni reali (Open Library/TMDB/MusicBrainz)...")

    dubbi = []
    confermati = 0
    scartati = 0
    per_file: dict[Path, list[dict]] = {}

    for i, (fp, r) in enumerate(tutte_le_voci):
        categoria = r["categoria"]
        titolo = r["titolo"]
        autore = r.get("autore", "")
        try:
            if categoria == "libro":
                punteggio, match = verifica_libro(titolo, autore)
                time.sleep(0.35)  # margine sotto ~3 richieste/secondo
            elif categoria == "film":
                punteggio, match = verifica_film(titolo, autore, tmdb_key)
                time.sleep(0.05)
            else:  # musica
                punteggio, match = verifica_musica(titolo, autore)
                time.sleep(MUSICBRAINZ_SLEEP)
        except Exception as e:
            print(f"  [{i+1}/{len(tutte_le_voci)}] ERRORE imprevisto su {titolo!r}: {e}, salto")
            continue

        if punteggio < 0:
            # Errore di rete: non scrivere nulla, riprovare in un run futuro.
            continue

        r["confermato_esterno"] = punteggio >= SOGLIA_ALTA
        per_file.setdefault(fp, []).append(r)

        if punteggio >= SOGLIA_ALTA:
            confermati += 1
        else:
            esito = "dubbio" if punteggio >= SOGLIA_BASSA else "probabile_falso_positivo"
            if punteggio < SOGLIA_BASSA:
                scartati += 1
            dubbi.append({
                "id": r.get("id"), "file": fp.name, "categoria": categoria,
                "titolo": titolo, "autore": autore, "punteggio": round(punteggio, 3),
                "match_trovato": match, "esito": esito,
            })

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(tutte_le_voci)}] confermati finora: {confermati}, dubbi/scartati: {len(dubbi)}")

    for fp, voci_modificate in per_file.items():
        dati = json.loads(fp.read_text(encoding="utf-8"))
        by_id = {r.get("id"): r for r in voci_modificate}
        for r in dati:
            if r.get("id") in by_id:
                r["confermato_esterno"] = by_id[r["id"]]["confermato_esterno"]
        fp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    esistenti = {}
    if REPORT_PATH.exists():
        try:
            for v in json.loads(REPORT_PATH.read_text(encoding="utf-8")):
                esistenti[v["id"]] = v
        except (json.JSONDecodeError, OSError):
            pass
    for v in dubbi:
        esistenti[v["id"]] = v
    fuso = list(esistenti.values())

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(fuso, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nFatto. {confermati} confermate automaticamente, {scartati} probabili falsi positivi, "
          f"{len(dubbi) - scartati} dubbie — report completo in {REPORT_PATH} ({len(fuso)} voci totali). "
          "NON cancellato nulla, solo segnalato/marcato.")


if __name__ == "__main__":
    main()
