# Ri-verifica dal vivo (chiamate reali a Open Library/TMDB/MusicBrainz) le voci gia'
# marcate esito=="dubbio" da verifica_riferimenti_esterna.py — necessario perche' quello
# script salta ogni voce che ha gia' "confermato_esterno" impostato (main():
# `"confermato_esterno" not in r`), quindi il fix di formula del 2026-07-23 (categorie
# A/B: autore mai estratto, giudicare SOLO sul titolo) non si applicherebbe MAI al
# backlog gia' segnato, solo alle voci nuove future.
#
# Aggiorna: campo "confermato_esterno"/"copertina" nel file dati originale, e il
# report (*_non_confermati.json): rimuove le voci ora risolte (confermate o
# probabile_falso_positivo), lascia intatte quelle ANCORA dubbie con la formula
# corretta (es. categoria C: autore reale, titolo non trovato — genuinamente
# ambigua, richiede sempre revisione umana).
#
# Uso: python scripts/rivaluta_dubbi_esterni.py --dataset riferimenti [--dry-run] [--limit N]

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verifica_riferimenti_esterna import (  # noqa: E402
    DATASET_CONFIG, SOGLIA_ALTA, SOGLIA_BASSA, MUSICBRAINZ_SLEEP,
    verifica_libro, verifica_film, verifica_musica, _tmdb_key,
)
from dati_root import dati_root, logs_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=list(DATASET_CONFIG), default="riferimenti")
    parser.add_argument("--limit", type=int, default=0, help="0 = tutte")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = DATASET_CONFIG[args.dataset]
    data_dir = dati_root(ROOT) / cfg["dir"]
    report_path = logs_root(ROOT) / cfg["report"]
    tmdb_key = _tmdb_key()

    if not report_path.exists():
        print(f"Nessun report in {report_path}, niente da fare.")
        return

    report = json.loads(report_path.read_text(encoding="utf-8"))
    dubbi = [v for v in report if v.get("esito") == "dubbio"]
    if args.limit:
        dubbi = dubbi[:args.limit]
    print(f"Ri-verifico {len(dubbi)} voci 'dubbio' con la formula corretta (2026-07-23)...")

    per_file: dict[str, list[dict]] = {}
    for v in dubbi:
        per_file.setdefault(v["file"], []).append(v)

    riclassificate = {"confermato": 0, "falso_positivo": 0, "ancora_dubbio": 0, "saltate": 0}
    ids_risolti = []

    for filename, voci in per_file.items():
        fp = data_dir / filename
        if not fp.exists():
            continue
        dati = json.loads(fp.read_text(encoding="utf-8"))
        by_id = {r.get("id"): r for r in dati}
        modificato = False
        for v in voci:
            r = by_id.get(v["id"])
            if not r or r.get("confermato_esterno") is not False or r.get("titolo") != v.get("titolo"):
                riclassificate["saltate"] += 1
                continue
            categoria = cfg["mappa"][r[cfg["campo"]]]
            titolo, autore = r["titolo"], r.get("autore", "")
            try:
                if categoria == "libro":
                    punteggio, match, copertina = verifica_libro(titolo, autore)
                    time.sleep(0.35)
                elif categoria == "film":
                    punteggio, match, copertina = verifica_film(titolo, autore, tmdb_key)
                    time.sleep(0.05)
                else:
                    punteggio, match, copertina = verifica_musica(titolo, autore)
                    time.sleep(MUSICBRAINZ_SLEEP)
            except Exception as e:
                print(f"  ERRORE su {titolo!r}: {e}, salto")
                riclassificate["saltate"] += 1
                continue
            if punteggio < 0:
                riclassificate["saltate"] += 1
                continue

            if punteggio >= SOGLIA_ALTA:
                esito = "confermato"
            elif punteggio < SOGLIA_BASSA:
                esito = "falso_positivo"
            else:
                esito = "ancora_dubbio"
            if esito == "confermato":
                riclassificate["confermato"] += 1
            elif esito == "falso_positivo":
                riclassificate["falso_positivo"] += 1
            else:
                riclassificate["ancora_dubbio"] += 1

            if esito != "ancora_dubbio":
                ids_risolti.append(v["id"])
                if not args.dry_run:
                    r["confermato_esterno"] = (esito == "confermato")
                    if esito == "confermato" and copertina:
                        r["copertina"] = copertina
                    modificato = True
            else:
                # Aggiorna il punteggio/match nel report (formula nuova) ma resta "dubbio".
                v["punteggio"] = round(punteggio, 3)
                v["match_trovato"] = match
        if modificato and not args.dry_run:
            fp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    prefisso = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{prefisso}Confermate: {riclassificate['confermato']}. "
          f"Falsi positivi: {riclassificate['falso_positivo']}. "
          f"Ancora dubbie: {riclassificate['ancora_dubbio']}. "
          f"Saltate (gia' cambiate): {riclassificate['saltate']}.")

    if not args.dry_run:
        residuo = [v for v in report if v["id"] not in ids_risolti]
        report_path.write_text(json.dumps(residuo, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report aggiornato: {len(residuo)} voci residue.")


if __name__ == "__main__":
    main()
