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
# Testato 2026-07-22 su GPU (RTX 5070) contro il default puro: beam_size/best_of
# da soli non cambiano la qualita' in modo percepibile (confidenza pressoche'
# identica al default) - il guadagno reale viene dal prompt di dominio, che
# recupera contenuto (sigle/nome del programma) che il default perde del tutto
# e migliora la punteggiatura nel parlato sostanziale. Costo: ~20s/episodio in
# piu', e un rischio residuo di eco del prompt SOLO nei passaggi musicali/jingle
# ambigui (non nel parlato vero) - confermato che quell'eco esiste anche senza
# prompt in quello stesso punto, quindi e' ambiguita' audio intrinseca, non
# un artefatto puro del prompt. Prompt tenuto corto apposta (senza il nome del
# programma) per non "auto-innescare" l'eco sulla propria stessa sigla.


def transcribe(audio_path, hf_token, device="cpu", compute_type="int8", batch_size=8, threads=None,
                cpu_affinity=None, beam_size=None, best_of=None, initial_prompt=None):
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
