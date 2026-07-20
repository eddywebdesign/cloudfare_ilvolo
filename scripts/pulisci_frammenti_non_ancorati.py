# Pulizia UNA TANTUM del backlog di frammenti classificati prima dei guardarraili
# deterministici aggiunti il 2026-07-20 a classifica_frammenti() (vedi
# trascrivi_locale_episodi.py: _titolo_ancorato, MIN_PAROLE_NARRATIVO). Le nuove
# classificazioni li rispettano gia' da sole - questo script serve solo a ripulire
# quelle FATTE PRIMA, che quei guardarraili non hanno mai visto.
#
# Due trattamenti diversi, scelti in base al rischio di falso positivo:
# - riferimento_libro/film/musica NON ancorati al testo: reset diretto (tipo/titolo/
#   tema svuotati) - rischio di falso positivo bassissimo, il fix richiede l'assenza
#   TOTALE di qualunque parola significativa del titolo nel testo. Tornano in coda
#   "da classificare" e verranno ripresi al prossimo giro (gia' con le regole nuove).
# - aneddoto/riflessione sotto le 25 parole: NON reset automatico (falsi positivi
#   reali esistono, es. frasi brevi ma compiute) - solo segnalati in
#   logs/frammenti_dubbi.json (stessa coda letta dal pannello admin di
#   /frammenti-recenti/?admin=1) per una revisione umana.
#
# NON tocca nulla che sia gia' stato confermato a mano (nessun campo apposito lo
# distingue oggi - stesso limite di verifica_frammenti.py, accettato).
#
# Uso: python scripts/pulisci_frammenti_non_ancorati.py [--dry-run]

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from trascrivi_locale_episodi import _titolo_ancorato, RIF_TIPI, NARR_TIPI, MIN_PAROLE_NARRATIVO  # noqa: E402
from dati_root import dati_root, logs_root  # noqa: E402

FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"
DUBBI_PATH = logs_root(ROOT) / "frammenti_dubbi.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe senza scrivere nulla")
    args = parser.parse_args()

    reset_ids = []
    dubbi_nuovi = []

    for fp in sorted(FRAMMENTI_DIR.glob("*.json")):
        frammenti = json.loads(fp.read_text(encoding="utf-8"))
        modificato = False
        for f in frammenti:
            tipo = f.get("tipo")
            if not tipo:
                continue
            if tipo in RIF_TIPI and not _titolo_ancorato(f.get("titolo", ""), f.get("testo", "")):
                reset_ids.append(f["id"])
                if not args.dry_run:
                    f["titolo"] = ""
                    f["tipo"] = ""
                    f["tema"] = []
                    modificato = True
            elif tipo in NARR_TIPI and len(f.get("testo", "").split()) < MIN_PAROLE_NARRATIVO:
                dubbi_nuovi.append({
                    "id": f["id"], "file": fp.name, "tipo": tipo,
                    "titolo": f.get("titolo"), "tema": f.get("tema"),
                    "testo": f.get("testo", "")[:200],
                    "motivo": f"sotto le {MIN_PAROLE_NARRATIVO} parole, nessuna svolta narrativa/insegnamento autonomo verificabile",
                })
        if modificato and not args.dry_run:
            fp.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Reset {len(reset_ids)} riferimento_libro/film/musica non ancorati.")
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Segnalati {len(dubbi_nuovi)} aneddoto/riflessione troppo corti per revisione.")

    if not args.dry_run and dubbi_nuovi:
        esistenti = {}
        if DUBBI_PATH.exists():
            try:
                for v in json.loads(DUBBI_PATH.read_text(encoding="utf-8")):
                    esistenti[v["id"]] = v
            except (json.JSONDecodeError, OSError):
                pass
        for v in dubbi_nuovi:
            esistenti[v["id"]] = v
        DUBBI_PATH.parent.mkdir(parents=True, exist_ok=True)
        DUBBI_PATH.write_text(json.dumps(list(esistenti.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Scritto in {DUBBI_PATH} ({len(esistenti)} voci totali).")


if __name__ == "__main__":
    main()
