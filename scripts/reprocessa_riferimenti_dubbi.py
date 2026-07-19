# Fase C del reprocessamento riferimenti (2026-07-18): libera gli slot delle
# voci NON ancorate (trovate da controlla_ancoraggio_riferimenti.py) e le
# ri-estrae dal testo REALE della trascrizione (data/trascrizioni/<data>.json),
# questa volta con l'ancoraggio per-chunk gia' corretto (_titolo_e_ancorato_al_testo
# in trascrivi_e_estrai_clip.py). Le voci NON in elenco (gia' ancorate, comprese
# eventuali correzioni manuali via approva.py) restano intatte.
#
# Uso: python scripts/reprocessa_riferimenti_dubbi.py
#      (richiede logs/riferimenti_non_ancorati.json gia' generato)

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dati_root import dati_root, logs_root  # noqa: E402
from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402
import llm_multi  # noqa: E402

RIF_DIR = dati_root(ROOT) / "riferimenti"
TRASCRIZIONI_DIR = dati_root(ROOT) / "trascrizioni"
REPORT_PATH = logs_root(ROOT) / "riferimenti_non_ancorati.json"


def testo_e_durata(data_str: str) -> tuple[str, float] | None:
    path = TRASCRIZIONI_DIR / f"{data_str}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    segs = d.get("segments", [])
    if not segs:
        return None
    testo = " ".join(s.get("text", "") for s in segs)
    durata = segs[-1].get("end", 0.0)
    return testo, durata


def main() -> None:
    if not REPORT_PATH.exists():
        print(f"Manca {REPORT_PATH}, lancia prima controlla_ancoraggio_riferimenti.py")
        return

    voci_dubbie = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    per_data: dict[str, set[str]] = {}
    for v in voci_dubbie:
        data_str = v["file"].removesuffix(".json")
        per_data.setdefault(data_str, set()).add(v["id"])

    print(f"{len(voci_dubbie)} voci da liberare su {len(per_data)} date")

    for data_str, ids_da_liberare in sorted(per_data.items()):
        if llm_multi.provider_disponibile() is None:
            print(f"STOP: budget Groq E Cerebras esauriti per oggi. "
                  f"Riprendera' domani da {data_str}.")
            break

        path = RIF_DIR / f"{data_str}.json"
        if not path.exists():
            continue
        voci = json.loads(path.read_text(encoding="utf-8"))
        if not any(v["id"] in ids_da_liberare for v in voci):
            continue  # gia' sistemata in una run precedente interrotta, salta
        prima = len(voci)
        voci_pulite = [v for v in voci if v["id"] not in ids_da_liberare]
        rimossi = prima - len(voci_pulite)
        path.write_text(json.dumps(voci_pulite, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{data_str}] liberati {rimossi} slot ({prima} -> {len(voci_pulite)} voci)")

        td = testo_e_durata(data_str)
        if td is None:
            print(f"    manca la trascrizione reale, slot liberati ma non ri-estratti")
            continue
        testo, durata = td

        refs = estrai_riferimenti(testo)
        print(f"    {len(refs)} riferimenti ri-estratti (ancorati)")
        merge_riferimenti(data_str, refs, testo, durata)

    print("Fatto.")


if __name__ == "__main__":
    main()
