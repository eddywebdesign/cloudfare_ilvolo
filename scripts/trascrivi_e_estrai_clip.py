# Trascrizione clip MP3 da frammenti_trascr_CPU/ + estrazione riferimenti culturali.
#
# Per ogni MP3:
#   1. Parsing filename → episodio_data (anno=2016, DDMM dal nome)
#   2. Trascrizione con faster-whisper (CPU, modello small, lingua it)
#      Audio decodificato via ffmpeg CLI per evitare conflitti con PyAV/WDAC.
#   3. Estrazione riferimenti con Groq (film, libro, musica; multi-riferimento)
#   4. Merge idempotente in data/riferimenti/2016-MM-DD.json
#
# Uso: python scripts/trascrivi_e_estrai_clip.py [clip1.mp3 clip2.mp3 ...]
#      senza argomenti: processa tutti gli MP3 in frammenti_trascr_CPU/
#
# Richiede: GROQ_API_KEY nell'ambiente

import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

# Mock av e sottomoduli: faster_whisper li importa a livello di modulo ma noi
# passiamo l'audio come numpy array (via ffmpeg), quindi av non viene mai usato.
# Senza mock, il DLL di av viene bloccato da Windows Application Control (WDAC).
for _av_mod in [
    'av', 'av.audio', 'av.audio.codeccontext', 'av.audio.fifo', 'av.audio.format',
    'av.audio.frame', 'av.audio.layout', 'av.audio.plane', 'av.audio.resampler',
    'av.audio.stream', 'av.codec', 'av.codec.codec', 'av.codec.context',
    'av.container', 'av.container.core', 'av.container.input', 'av.container.output',
    'av.data', 'av.data.packet', 'av.descriptor', 'av.error', 'av.filter',
    'av.filter.context', 'av.filter.filter', 'av.filter.graph', 'av.filter.link',
    'av.frame', 'av.option', 'av.packet', 'av.plane', 'av.sidedata',
    'av.stream', 'av.subtitles', 'av.video',
]:
    sys.modules[_av_mod] = types.ModuleType(_av_mod)

import numpy as np
from groq import Groq

ROOT = Path(__file__).resolve().parent.parent
CLIP_DIR = ROOT.parent / "frammenti_trascr_CPU"
RIF_DIR = ROOT / "data" / "riferimenti"

SAMPLE_RATE = 16000

SYSTEM = (
    "Sei un assistente che analizza trascrizioni del programma radiofonico italiano "
    "'Il Volo del Mattino' (Radio DeeJay), condotto da Fabio Volo. "
    "Rispondi SEMPRE e SOLO con un array JSON valido, nessun testo aggiuntivo."
)

PROMPT_TPL = """\
Dal seguente testo estratto da una puntata de "Il Volo del Mattino", \
estrai TUTTI i riferimenti culturali specifici presenti.

TESTO:
\"\"\"{testo}\"\"\"

Restituisci un array JSON (vuoto [] se non ci sono riferimenti chiari):
[
  {{"categoria": "film", "titolo": "...", "autore": "...", "anno": "...", "note": "..."}},
  ...
]

Regole:
- categoria: solo "film", "libro" o "musica"
- "libro" include poesie, saggi, romanzi (autore = poeta/scrittore)
- Se un titolo è sia poesia sia film (es. Invictus), crea DUE entry separate
- anno: anno di uscita/pubblicazione (stringa vuota se sconosciuto)
- autore: regista / scrittore / artista (vuoto se sconosciuto)
- note: max 12 parole su perché Fabio lo cita/legge/suona
- Non includere riferimenti vaghi o non identificabili
"""


def parse_data(filename: str) -> str | None:
    """Estrae episodio_data dal nome del file.
    Supporta: YYYYMMDD (es. 'Audio 20160107 - Radio Deejay.mp3')
              e legacy volo_DDMM_ (es. 'volo_0701_...').
    """
    m = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'volo_(\d{2})(\d{2})_', filename)
    if m:
        dd, mm = m.group(1), m.group(2)
        return f"2016-{mm}-{dd}"
    return None


def decode_audio_ffmpeg(path: Path) -> np.ndarray:
    """Decodifica MP3 → float32 mono 16kHz via ffmpeg CLI (evita PyAV/WDAC)."""
    cmd = [
        "ffmpeg", "-i", str(path),
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr.decode(errors='replace')[-300:]}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    return audio


def trascrivi(path: Path, model) -> tuple[str, float]:
    """Restituisce (testo_completo, durata_secondi)."""
    audio = decode_audio_ffmpeg(path)
    durata = len(audio) / SAMPLE_RATE
    segments, _ = model.transcribe(
        audio, language="it", beam_size=5,
        vad_filter=True,
        initial_prompt="Il Volo del Mattino, Fabio Volo, Radio DeeJay, film, libro, canzone.",
    )
    testo = " ".join(s.text.strip() for s in segments)
    return testo, round(durata, 2)


CHUNK_SIZE = 6000  # caratteri per chunk (~1500 token input, lascia spazio al prompt)
CHUNK_SLEEP = 13   # secondi tra chunk (max ~4-5 chunk/min entro 6000 TPM)


def _groq_chunk(client: Groq, testo: str) -> list[dict]:
    """Singola chiamata Groq per un chunk di testo."""
    prompt = PROMPT_TPL.format(testo=testo)
    resp = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=600,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content.strip()
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return []
    return parsed if isinstance(parsed, list) else []


def estrai_riferimenti(client: Groq, testo: str) -> list[dict]:
    """Divide il testo in chunk e aggrega i riferimenti trovati da Groq."""
    chunks = [testo[i:i + CHUNK_SIZE] for i in range(0, len(testo), CHUNK_SIZE)]
    print(f"    Invio {len(chunks)} chunk a Groq…")
    tutti: list[dict] = []
    for idx, chunk in enumerate(chunks):
        try:
            risultati = _groq_chunk(client, chunk)
            print(f"      chunk {idx+1}/{len(chunks)}: {len(risultati)} riferimenti")
            tutti.extend(risultati)
        except Exception as e:
            print(f"      chunk {idx+1}/{len(chunks)} ERRORE: {e}")
        if idx < len(chunks) - 1:
            time.sleep(CHUNK_SLEEP)
    return tutti


def merge_riferimenti(data_str: str, nuovi: list[dict], testo: str, durata: float) -> None:
    """Aggiunge i nuovi riferimenti al file JSON, senza sovrascrivere campi già compilati."""
    dest = RIF_DIR / f"{data_str}.json"
    esistenti: dict[str, dict] = {}
    if dest.exists():
        for r in json.loads(dest.read_text(encoding="utf-8")):
            esistenti[r["id"]] = r

    # Conta per generare id progressivi per questa data+categoria
    contatori: dict[str, int] = {}
    for eid in esistenti:
        m = re.match(r'.+-(film|libro|musica)-clip-(\d+)', eid)
        if m:
            cat = m.group(1)
            n = int(m.group(2))
            contatori[cat] = max(contatori.get(cat, -1), n)

    aggiunti = 0
    for ref in nuovi:
        cat = ref.get("categoria", "").lower()
        if cat not in ("film", "libro", "musica"):
            continue
        n = contatori.get(cat, -1) + 1
        contatori[cat] = n
        rid = f"{data_str}-{cat}-clip-{n:04d}"

        if rid in esistenti:
            continue  # già presente, rispetta il merge

        esistenti[rid] = {
            "id": rid,
            "categoria": cat,
            "titolo": ref.get("titolo", ""),
            "anno": ref.get("anno", ""),
            "autore": ref.get("autore", ""),
            "note": ref.get("note", ""),
            "testo": testo,
            "start": 0.0,
            "end": durata,
            "episodio_data": data_str,
        }
        aggiunti += 1

    voci = sorted(esistenti.values(), key=lambda x: x["start"])
    RIF_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(voci, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    -> {dest.relative_to(ROOT)} ({aggiunti} nuovi, {len(voci)} totali)")


GROQ_KEY_FILE = Path.home() / "API GROQ IA.txt"


def load_groq_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key and GROQ_KEY_FILE.exists():
        key = GROQ_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        print(f"Errore: chiave Groq non trovata. Imposta GROQ_API_KEY oppure salva la chiave in:\n  {GROQ_KEY_FILE}")
        sys.exit(1)
    return key


def main() -> None:
    client = Groq(api_key=load_groq_key())

    # Carica modello faster-whisper una sola volta
    print("Carico modello faster-whisper medium (CPU)…")
    from faster_whisper import WhisperModel  # import ritardato: fallisce solo se mancante
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    print("Modello pronto.\n")

    if len(sys.argv) > 1:
        clip_paths = [Path(p) for p in sys.argv[1:]]
    else:
        clip_paths = sorted(CLIP_DIR.glob("*.mp3"))

    if not clip_paths:
        print(f"Nessun MP3 trovato in {CLIP_DIR}")
        return

    print(f"Processo {len(clip_paths)} clip...\n")
    for mp3 in clip_paths:
        data_str = parse_data(mp3.name)
        if not data_str:
            print(f"[SKIP] {mp3.name} — non riesco a estrarre la data dal nome")
            continue

        print(f"[{data_str}] {mp3.name}")

        # Trascrizione
        try:
            testo, durata = trascrivi(mp3, model)
        except Exception as e:
            print(f"    ERRORE trascrizione: {e}")
            continue
        print(f"    {durata:.0f}s — {len(testo)} caratteri trascritti")

        if not testo.strip():
            print("    testo vuoto, salto")
            continue

        # Estrazione riferimenti
        refs = estrai_riferimenti(client, testo)
        print(f"    Groq: {len(refs)} riferimenti trovati")
        for r in refs:
            print(f"      [{r.get('categoria','?')}] {r.get('titolo','?')} ({r.get('autore','?')} {r.get('anno','')})")

        # Deduplicazione per (categoria, titolo) — Groq a volte ripete lo stesso
        seen_keys: set[tuple] = set()
        refs_uniq = []
        for r in refs:
            key = (r.get("categoria", "").lower(), r.get("titolo", "").lower().strip())
            if key not in seen_keys and key[1]:
                seen_keys.add(key)
                refs_uniq.append(r)
        if len(refs_uniq) < len(refs):
            print(f"    (deduplicati: {len(refs)} -> {len(refs_uniq)})")

        # Salvataggio
        merge_riferimenti(data_str, refs_uniq, testo, durata)

        time.sleep(0.5)  # throttle Groq

    print("\nFatto.")


if __name__ == "__main__":
    main()
