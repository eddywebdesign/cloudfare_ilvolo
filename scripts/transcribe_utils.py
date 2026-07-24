#!/usr/bin/env python3
"""Utility di trascrizione condivise (estratte da sync_archive.py il 2026-07-17
quando la pipeline di upload su archive.org e' stata chiusa e archiviata in
scripts/archivio_chiuso/ — queste funzioni restano perche' trascrivi_locale_episodi.py
le usa ancora per la trascrizione attiva sul K16)."""
import subprocess
import sys
from pathlib import Path

import psutil

HF_TOKEN_FILE = Path.home() / "hf_token.txt"


def load_lines(path, count=1):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return lines[0] if count == 1 else lines[:count]


PROMPT_DOMINIO = "Fabio Volo, Maurizio, Viola."
# RIMOSSO dall'uso 2026-07-24 (vedi trascrivi_locale_episodi.py): whisperx re-inietta
# initial_prompt ad ogni finestra di decodifica, non solo alla prima - causava
# un'allucinazione a loop del prompt durante i passaggi musicali (confermate 1420
# occorrenze in 200 episodi, poi 0/6 in un campione dedicato senza prompt contro
# 17 occorrenze totali con prompt). La costante resta qui solo per compatibilita'
# con chiamate storiche, MAI passarla di default a initial_prompt.
# MIN/MAX_SPEAKERS_DEFAULT: testato 2026-07-24 su campione (harness
# scripts/linux/test_qualita_trascrizione.py) - riduce drasticamente la
# sovra-segmentazione degli speaker (es. 18->5, 16->6 speaker unici) senza
# alcun effetto su segmenti/parole/testo trascritto (solo raggruppamento
# diarizzazione). Il programma ha 3 conduttori fissi + rari ospiti/chiamate.
MIN_SPEAKERS_DEFAULT = 2
MAX_SPEAKERS_DEFAULT = 6


def transcribe(audio_path, hf_token, device="cpu", compute_type="int8", batch_size=8, threads=None,
                cpu_affinity=None, beam_size=None, best_of=None, initial_prompt=None,
                min_speakers=None, max_speakers=None):
    """cpu_affinity: lista di indici di core logici a cui vincolare il processo
    (garanzia a livello di sistema operativo — --threads di whisperx da solo
    non basta, CTranslate2/OpenMP possono comunque usare piu' core di quelli
    dichiarati durante la fase di trascrizione)."""
    cmd = [
        sys.executable, "-m", "whisperx", str(audio_path),
        "--model", "large-v3", "--language", "it",
        "--device", device, "--compute_type", compute_type, "--batch_size", str(batch_size),
        "--diarize", "--diarize_model", "pyannote/speaker-diarization-3.1", "--hf_token", hf_token,
        "--output_format", "json", "--output_dir", str(audio_path.parent),
    ]
    if threads:
        cmd += ["--threads", str(threads)]
    if beam_size:
        cmd += ["--beam_size", str(beam_size)]
    if best_of:
        cmd += ["--best_of", str(best_of)]
    if initial_prompt:
        cmd += ["--initial_prompt", initial_prompt]
    if min_speakers:
        cmd += ["--min_speakers", str(min_speakers)]
    if max_speakers:
        cmd += ["--max_speakers", str(max_speakers)]

    proc = subprocess.Popen(cmd)
    if cpu_affinity:
        try:
            psutil.Process(proc.pid).cpu_affinity(cpu_affinity)
        except Exception as e:
            print(f"  attenzione: impossibile impostare cpu_affinity: {e}")
    ret = proc.wait()
    if ret != 0:
        raise subprocess.CalledProcessError(ret, cmd)
    return audio_path.parent / (audio_path.stem + ".json")
