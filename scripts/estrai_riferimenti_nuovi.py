# Estrae i riferimenti culturali (libri/film/musica) per gli episodi che il
# K16 ha gia' trascritto (data/trascrizioni/<data>.json) ma per cui
# data/riferimenti/<data>.json non esiste ancora — perche' K16 gira con
# --skip-classify e non fa mai questo passo (vedi trascrivi_locale_episodi.py),
# nessun altro script della pipeline OMV lo faceva per gli episodi nuovi.
# Senza questo script, il buco cresce di un episodio ogni volta che K16 ne
# finisce uno.
#
# Riusa estrai_riferimenti()/merge_riferimenti() da trascrivi_e_estrai_clip.py
# (stesso ancoraggio per-chunk gia' corretto usato da reprocessa_riferimenti_dubbi.py).
#
# Uso: python scripts/estrai_riferimenti_nuovi.py

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dati_root import dati_root  # noqa: E402
from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402
import llm_multi  # noqa: E402

RIF_DIR = dati_root(ROOT) / "riferimenti"
TRASCRIZIONI_DIR = dati_root(ROOT) / "trascrizioni"


def main() -> None:
    trascritti = sorted(p.stem for p in TRASCRIZIONI_DIR.glob("*.json"))
    da_fare = [d for d in trascritti if not (RIF_DIR / f"{d}.json").exists()]

    print(f"{len(trascritti)} episodi trascritti, {len(da_fare)} senza riferimenti ancora estratti")
    if not da_fare:
        print("Fatto.")
        return

    for data_str in da_fare:
        if llm_multi.provider_disponibile() is None:
            print(f"STOP: budget Groq E Cerebras esauriti per oggi. Riprendera' domani da {data_str}.")
            break

        path = TRASCRIZIONI_DIR / f"{data_str}.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        segs = d.get("segments", [])
        if not segs:
            print(f"[{data_str}] trascrizione senza segmenti, salto")
            continue
        testo = " ".join(s.get("text", "") for s in segs)
        durata = segs[-1].get("end", 0.0)

        print(f"[{data_str}] estraggo riferimenti da {len(testo)} caratteri...")
        refs = estrai_riferimenti(testo)
        merge_riferimenti(data_str, refs, testo, durata)

    print("Fatto.")


if __name__ == "__main__":
    main()
