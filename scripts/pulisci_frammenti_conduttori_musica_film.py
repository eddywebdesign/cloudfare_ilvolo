# Pulizia UNA TANTUM di data/frammenti/*.json gia' classificati riferimento_musica/
# riferimento_film con autore = uno o piu' conduttori del programma (Fabio Volo/
# Maurizio/Viola, anche elencati insieme, es. "Volo, Maurizio, Viola"). Creato
# 2026-07-23: il guardarraglio _autore_e_solo_conduttori() e' stato corretto
# (commit e1f32d8e) DOPO che una finestra di run (13:45-17:51) aveva gia' classificato
# frammenti con questo bug — e classifica_frammenti() non riprocessa mai un frammento
# che ha gia' un titolo, quindi quelle voci sarebbero rimaste sbagliate per sempre
# senza questo script.
#
# Reset (non rimozione, a differenza di pulisci_riferimenti_non_confermati.py):
# tipo/titolo/autore/tema svuotati, il frammento torna in coda "da classificare" e
# verra' ripreso al prossimo giro con la logica corretta.
#
# Uso: python scripts/pulisci_frammenti_conduttori_musica_film.py [--dry-run]

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from trascrivi_locale_episodi import _autore_e_solo_conduttori  # noqa: E402
from dati_root import dati_root  # noqa: E402

FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    reset_ids = []
    for fp in sorted(FRAMMENTI_DIR.glob("*.json")):
        frammenti = json.loads(fp.read_text(encoding="utf-8"))
        modificato = False
        for f in frammenti:
            if f.get("tipo") not in ("riferimento_musica", "riferimento_film"):
                continue
            if _autore_e_solo_conduttori(f.get("autore", "")):
                reset_ids.append(f["id"])
                if not args.dry_run:
                    f["tipo"] = ""
                    f["titolo"] = ""
                    f["autore"] = ""
                    f["tema"] = []
                    modificato = True
        if modificato and not args.dry_run:
            fp.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Reset {len(reset_ids)} riferimento_musica/film con autore=conduttore.")


if __name__ == "__main__":
    main()
