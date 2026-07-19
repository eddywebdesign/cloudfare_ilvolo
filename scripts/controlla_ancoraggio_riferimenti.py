# Controllo locale, gratuito (nessuna chiamata Groq/Cerebras), del bug storico
# trovato il 2026-07-18: prima del fix del 17/07, tutte le voci di un episodio
# condividevano lo stesso blocco di testo dell'intero episodio invece del
# contesto reale del singolo riferimento — quindi titolo/autore spesso non
# compare affatto nel campo "testo" salvato. Diverso da verifica_riferimenti.py
# (che verifica se il titolo e' un'opera reale, non se e' ancorato al testo).
#
# NON cancella nulla: scrive un report in logs/riferimenti_non_ancorati.json
# con le voci il cui titolo/autore non compare nel proprio campo "testo".
#
# Uso: python scripts/controlla_ancoraggio_riferimenti.py [data1 data2 ...]

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dati_root import dati_root, logs_root  # noqa: E402

RIF_DIR = dati_root(ROOT.parent) / "riferimenti"
REPORT_PATH = logs_root(ROOT.parent) / "riferimenti_non_ancorati.json"


def _normalizza(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def ancorato(titolo: str, autore: str, testo: str) -> bool:
    testo_norm = _normalizza(testo)
    for candidato in (titolo, autore):
        norm = _normalizza(candidato or "")
        if not norm:
            continue
        parole = [p for p in norm.split() if len(p) >= 4]
        if not parole:
            if norm in testo_norm:
                return True
            continue
        if any(p in testo_norm for p in parole):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*")
    args = parser.parse_args()

    target = [RIF_DIR / f"{d}.json" for d in args.date] if args.date else sorted(RIF_DIR.glob("*.json"))

    non_ancorati = []
    tot = 0
    for path in target:
        if not path.exists():
            continue
        voci = json.loads(path.read_text(encoding="utf-8"))
        for v in voci:
            if not v.get("titolo"):
                continue
            tot += 1
            if not ancorato(v.get("titolo", ""), v.get("autore", ""), v.get("testo", "")):
                non_ancorati.append({
                    "id": v["id"], "file": path.name, "categoria": v.get("categoria"),
                    "titolo": v.get("titolo"), "autore": v.get("autore"),
                })

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(non_ancorati, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(non_ancorati)}/{tot} voci con titolo NON ancorate al proprio testo -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
