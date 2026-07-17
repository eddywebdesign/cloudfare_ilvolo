# Ri-passa i frammenti gia' generati ma mai (o solo parzialmente) classificati —
# tipicamente perche' un run precedente si e' fermato a meta' per budget giornaliero
# esaurito su entrambi i provider (vedi llm_multi.py, Groq+Cerebras). Riusa la stessa
# classifica_frammenti() della pipeline principale, nessuna logica duplicata.
#
# Uso:
#   python scripts/riclassifica_frammenti.py [data1 data2 ...]
#   senza argomenti: scansiona data/frammenti/*.json e processa quelli con almeno
#   un frammento senza titolo.

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from trascrivi_locale_episodi import classifica_frammenti  # noqa: E402
import llm_multi  # noqa: E402
from dati_root import dati_root  # noqa: E402

FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date YYYY-MM-DD da riclassificare (default: tutte quelle con frammenti non titolati)")
    args = parser.parse_args()

    if args.date:
        target = [FRAMMENTI_DIR / f"{d}.json" for d in args.date]
    else:
        target = sorted(FRAMMENTI_DIR.glob("*.json"))

    for path in target:
        if not path.exists():
            print(f"[SKIP] {path.name} non trovato")
            continue
        frammenti = json.loads(path.read_text(encoding="utf-8"))
        non_titolati_prima = sum(1 for f in frammenti if not f["titolo"])
        if non_titolati_prima == 0:
            continue
        if llm_multi.provider_disponibile() is None:
            print(f"STOP: budget Groq E Cerebras esauriti per oggi. Riprendera' domani da qui ({path.stem}).")
            break

        print(f"[{path.stem}] {non_titolati_prima} frammenti da classificare...")
        classifica_frammenti(frammenti)
        titolati_dopo = sum(1 for f in frammenti if f["titolo"])
        path.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> {titolati_dopo}/{len(frammenti)} titolati in totale ora\n")

    print("Fatto.")


if __name__ == "__main__":
    main()
