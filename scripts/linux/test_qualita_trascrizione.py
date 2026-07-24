#!/usr/bin/env python3
"""Harness permanente per confrontare varianti di parametri WhisperX su un campione
fisso di episodi reali, PRIMA di applicare un cambio all'intera pipeline. Nato dalla
sessione del 2026-07-24 (bug initial_prompt: un singolo episodio di test aveva dato
falsa sicurezza, un campione di 6 episodi ha rivelato la scala reale del problema).

Uso:
    python3 scripts/linux/test_qualita_trascrizione.py --varianti no_prompt,con_prompt
    python3 scripts/linux/test_qualita_trascrizione.py --varianti min_max_speakers

Aggiungere nuove varianti in VARIANTI sotto (dict di extra-args CLI whisperx).
Il campione (CAMPIONE) e' fisso apposta: stessi episodi = confronti comparabili
nel tempo, non serve reinventarlo ogni volta.

Richiede: GPU libera (ferma prima batch/Ollama con kill_coordinado.py se serve),
gira SOLO episodi gia' trascritti in precedenza (per avere anche il confronto con
l'originale in produzione), mai in parallelo con altri processi whisperx.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CAMPIONE = [
    "/mnt/ilvolo-audio-backup/2016/gia_trascritti/2016-01-14_20160114_reloaded_volo.mp3",
    "/mnt/ilvolo-audio-backup/2016/gia_trascritti/2016-12-09_20161209.mp3",
    "/mnt/ilvolo-audio-backup/2019/gia_trascritti/2019-09-10_20190910.mp3",
    "/mnt/ilvolo-audio-backup/2019/gia_trascritti/2019-11-06_20191106_rapporto_con_i_nostri_genitori.mp3",
    "/mnt/ilvolo-audio-backup/2025/gia_trascritti/2025-11-10_il_volo_del_mattino-20251110.mp3",
    "/mnt/ilvolo-audio-backup/2025/gia_trascritti/2025-11-21_il_volo_del_mattino-20251121.mp3",
]

HF_TOKEN = Path.home().joinpath("hf_token.txt").read_text(encoding="utf-8").strip()

BASE_ARGS = [
    "--model", "large-v3", "--language", "it", "--device", "cuda",
    "--compute_type", "float16", "--batch_size", "16",
    "--diarize", "--diarize_model", "pyannote/speaker-diarization-3.1",
    "--hf_token", HF_TOKEN, "--output_format", "json",
    "--beam_size", "5", "--best_of", "5",
]

# Ogni variante e' extra-args CLI whisperx AGGIUNTIVI/sostitutivi rispetto a BASE_ARGS.
VARIANTI = {
    "no_prompt": [],
    "con_prompt": ["--initial_prompt", "Fabio Volo, Maurizio, Viola."],
    "min_max_speakers": ["--min_speakers", "2", "--max_speakers", "6"],
    "community1": ["--diarize_model", "pyannote/speaker-diarization-community-1"],
}

OUT_ROOT = Path("/tmp/test_qualita_harness")


def esegui_variante(nome: str, extra_args: list[str]) -> Path:
    out_dir = OUT_ROOT / nome
    out_dir.mkdir(parents=True, exist_ok=True)
    for audio in CAMPIONE:
        args = list(BASE_ARGS) + extra_args
        # extra_args con lo stesso flag di BASE_ARGS (es. --diarize_model) deve VINCERE:
        # whisperx/argparse usa l'ultimo valore passato per flag ripetuti, quindi va bene
        # metterlo dopo senza deduplicare a mano.
        cmd = [sys.executable, "-m", "whisperx", audio] + args + ["--output_dir", str(out_dir)]
        print(f"  [{nome}] {Path(audio).name} ...")
        t0 = time.time()
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"    fatto in {time.time()-t0:.0f}s")
    return out_dir


def metriche(json_path: Path) -> dict:
    d = json.loads(json_path.read_text(encoding="utf-8"))
    segs = d.get("segments", [])
    parole = sum(len(s.get("text", "").split()) for s in segs)
    eco = sum(1 for s in segs if "volo, maurizio, viola" in s.get("text", "").lower())
    speaker = set(s.get("speaker") for s in segs if s.get("speaker"))
    return {"segmenti": len(segs), "parole": parole, "eco_prompt": eco, "speaker_unici": len(speaker)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--varianti", required=True, help="nomi separati da virgola, vedi VARIANTI nel file")
    args = parser.parse_args()
    nomi = args.varianti.split(",")
    for n in nomi:
        if n not in VARIANTI:
            sys.exit(f"variante sconosciuta: {n} (disponibili: {', '.join(VARIANTI)})")

    risultati = {}
    for nome in nomi:
        print(f"=== Variante: {nome} ===")
        out_dir = esegui_variante(nome, VARIANTI[nome])
        risultati[nome] = {}
        for audio in CAMPIONE:
            json_path = out_dir / (Path(audio).stem + ".json")
            if json_path.exists():
                risultati[nome][Path(audio).stem] = metriche(json_path)

    print("\n=== RISULTATI ===")
    for episodio in [Path(a).stem for a in CAMPIONE]:
        print(f"\n{episodio}")
        for nome in nomi:
            m = risultati[nome].get(episodio)
            if m:
                print(f"  {nome:20} segmenti={m['segmenti']:4} parole={m['parole']:5} "
                      f"eco={m['eco_prompt']:2} speaker_unici={m['speaker_unici']:3}")


if __name__ == "__main__":
    main()
