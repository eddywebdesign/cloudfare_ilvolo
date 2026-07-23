# Pulizia dei riferimenti/frammenti marcati "probabile_falso_positivo" da
# verifica_riferimenti_esterna.py (confronto contro Open Library/TMDB/MusicBrainz,
# non solo ancoraggio al testo). Creato 2026-07-23 su richiesta esplicita
# dell'utente dopo il primo run reale sul dataset "riferimenti" (1698 voci: 752
# confermate/44%, 450 probabili falsi positivi/27%, 492 dubbie/29%).
#
# Agisce SOLO sulla fascia "probabile_falso_positivo" (punteggio < SOGLIA_BASSA in
# verifica_riferimenti_esterna.py, oggi 0.45) — quella con rischio di falso
# positivo piu' basso. La fascia "dubbio" NON viene toccata: resta nel report per
# revisione umana (nessuna UI admin la mostra ancora, vedi nota nel commit).
#
# Due trattamenti diversi in base al dataset, stesso principio di
# pulisci_frammenti_non_ancorati.py:
# - "riferimenti": la voce viene RIMOSSA dall'array (non ha un stato "vuoto" verso
#   cui tornare, e' un'estrazione one-shot — un oggetto con titolo svuotato
#   sarebbe solo spazzatura nel JSON).
# - "frammenti": la voce viene RESETTATA (tipo/titolo/autore/tema svuotati, testo e
#   id restano) cosi' torna in coda "da classificare" per riclassifica_frammenti.py,
#   stesso comportamento di pulisci_frammenti_non_ancorati.py.
#
# Sicurezza: agisce SOLO se la voce nel file dati corrente ha ANCORA
# confermato_esterno=false E lo stesso titolo del report (evita di toccare una
# voce gia' stata corretta/ri-verificata dopo che il report e' stato generato).
#
# Uso: python scripts/pulisci_riferimenti_non_confermati.py --dataset riferimenti [--dry-run]

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verifica_riferimenti_esterna import DATASET_CONFIG  # noqa: E402
from dati_root import dati_root, logs_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=list(DATASET_CONFIG), default="riferimenti")
    parser.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe senza scrivere nulla")
    args = parser.parse_args()

    cfg = DATASET_CONFIG[args.dataset]
    data_dir = dati_root(ROOT) / cfg["dir"]
    report_path = logs_root(ROOT) / cfg["report"]

    if not report_path.exists():
        print(f"Nessun report trovato in {report_path}, niente da fare.")
        return

    report = json.loads(report_path.read_text(encoding="utf-8"))
    da_pulire = [v for v in report if v.get("esito") == "probabile_falso_positivo"]
    print(f"{len(da_pulire)}/{len(report)} voci nel report sono 'probabile_falso_positivo' "
          f"(le altre restano intatte per revisione umana).")

    per_file: dict[str, list[dict]] = {}
    for v in da_pulire:
        per_file.setdefault(v["file"], []).append(v)

    rimossi = resettati = saltati_gia_cambiati = 0
    ids_processati = []

    for filename, voci in per_file.items():
        fp = data_dir / filename
        if not fp.exists():
            continue
        dati = json.loads(fp.read_text(encoding="utf-8"))
        by_id = {r.get("id"): (i, r) for i, r in enumerate(dati)}
        da_rimuovere_idx = []
        for v in voci:
            trovato = by_id.get(v["id"])
            if not trovato:
                continue
            idx, r = trovato
            titolo_attuale = r.get("titolo", "")
            if r.get("confermato_esterno") is not False or titolo_attuale != v.get("titolo"):
                # Gia' modificata/riverificata dopo la generazione del report: non toccare.
                saltati_gia_cambiati += 1
                continue
            ids_processati.append(v["id"])
            if args.dataset == "riferimenti":
                da_rimuovere_idx.append(idx)
                rimossi += 1
            else:  # frammenti: reset, non rimozione (torna in coda "da classificare")
                if not args.dry_run:
                    r["tipo"] = ""
                    r["titolo"] = ""
                    r["autore"] = ""
                    r["tema"] = []
                    r.pop("confermato_esterno", None)
                    r.pop("copertina", None)
                resettati += 1
        if da_rimuovere_idx and not args.dry_run:
            for idx in sorted(da_rimuovere_idx, reverse=True):
                del dati[idx]
        if (da_rimuovere_idx or args.dataset == "frammenti") and not args.dry_run:
            fp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    prefisso = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefisso}Rimossi: {rimossi}. Resettati: {resettati}. "
          f"Saltati (gia' cambiati dopo il report): {saltati_gia_cambiati}.")

    if not args.dry_run and ids_processati:
        residuo = [v for v in report if v["id"] not in ids_processati]
        report_path.write_text(json.dumps(residuo, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report aggiornato: {len(residuo)} voci residue (dubbie, per revisione umana).")


if __name__ == "__main__":
    main()
