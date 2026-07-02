#!/usr/bin/env python3
"""
Sincronizza gli episodi con archive.org: per ogni file .md in content/episodi/
senza `archivio_audio_url`, scarica l'audio originale (da media.deejay.it),
lo trascrive con WhisperX, lo carica su archive.org, aggiorna il front matter
del file con l'URL definitivo su archive.org — poi cancella la copia audio
locale temporanea. La trascrizione (JSON) resta salvata in data/trascrizioni/.

Idempotente: salta i file che hanno gia' `archivio_audio_url` impostato.

Uso:
    python scripts/sync_archive.py [--limit N] [--skip-transcribe] [--skip-upload]

Richiede due file locali (MAI committati in git):
    ~/hf_token.txt       token HuggingFace per la diarizzazione pyannote
    ~/archive_org.txt    due righe: access_key, secret_key per archive.org (S3-like)
"""
import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import yaml

UPLOAD_PAUSE_SEC = 12  # pausa tra un upload e il successivo, per non farci bloccare da archive.org
UPLOAD_MAX_RETRIES = 5

ROOT = Path(__file__).resolve().parent.parent
EPISODI_DIR = ROOT / "content" / "episodi"
TRANSCRIPT_DIR = ROOT / "data" / "trascrizioni"
HF_TOKEN_FILE = Path.home() / "hf_token.txt"
IA_KEYS_FILE = Path.home() / "archive_org.txt"


def load_lines(path, count=1):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return lines[0] if count == 1 else lines[:count]


def parse_front_matter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, None
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    return fm, body


def write_front_matter(path, fm, body):
    yaml_text = yaml.dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=None)
    path.write_text(f"---\n{yaml_text}---\n{body}", encoding="utf-8")


def download(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def transcribe(audio_path, hf_token):
    cmd = [
        sys.executable, "-m", "whisperx", str(audio_path),
        "--model", "large-v3", "--language", "it", "--compute_type", "int8",
        "--diarize", "--hf_token", hf_token,
        "--output_format", "json", "--output_dir", str(audio_path.parent),
    ]
    subprocess.run(cmd, check=True)
    return audio_path.parent / (audio_path.stem + ".json")


def wait_if_over_limit(identifier, access_key, max_wait_checks=10):
    """Consulta l'endpoint ufficiale prima di caricare, invece di tentare
    alla cieca e aspettare dopo un errore 503 SlowDown."""
    for _ in range(max_wait_checks):
        try:
            r = requests.get(
                "https://s3.us.archive.org/",
                params={"check_limit": 1, "accesskey": access_key, "bucket": identifier},
                timeout=15,
            )
            data = r.json()
        except Exception:
            return  # se il check stesso fallisce, si prosegue e si lascia gestire il retry sotto
        if not data.get("over_limit"):
            return
        print("  archive.org e' al limite in questo momento, aspetto 30s...")
        time.sleep(30)


def upload_to_archive(identifier, audio_path, metadata, access_key, secret_key):
    import internetarchive as ia
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        wait_if_over_limit(identifier, access_key)
        try:
            responses = ia.upload(
                identifier, files=[str(audio_path)], metadata=metadata,
                access_key=access_key, secret_key=secret_key,
            )
            for r in responses:
                r.raise_for_status()
            return f"https://archive.org/download/{identifier}/{audio_path.name}"
        except Exception as e:
            if "reduce your request rate" in str(e).lower() and attempt < UPLOAD_MAX_RETRIES:
                wait = 60 * attempt
                print(f"  rate limit di archive.org, aspetto {wait}s (tentativo {attempt}/{UPLOAD_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            raise


def apply_updates(md_path, updates):
    """Rilegge il front matter appena prima di scrivere, cosi' due processi
    concorrenti (es. trascrizione + upload) che lavorano sullo stesso file
    in momenti diversi non si sovrascrivono a vicenda."""
    fm, body = parse_front_matter(md_path)
    fm.update(updates)
    write_front_matter(md_path, fm, body)


def process_file(md_path, hf_token, ia_keys, do_transcribe, do_upload):
    fm, body = parse_front_matter(md_path)
    if not fm or not fm.get("audio"):
        return "skip (nessun campo audio)"

    # Se 'audio' punta gia' ad archive.org (es. episodi caricati da
    # upload_local_archive.py con un identifier diverso), non va mai
    # ri-scaricato/ri-caricato: crea doppioni e archive.org lo segnala come spam.
    already_hosted = str(fm.get("audio", "")).startswith("https://archive.org/")
    updates = {}
    if already_hosted and not fm.get("archivio_audio_url"):
        updates["archivio_audio_url"] = fm["audio"]

    need_transcribe = do_transcribe and not fm.get("trascrizione")
    need_upload = do_upload and not fm.get("archivio_audio_url") and not already_hosted
    if not need_transcribe and not need_upload:
        if updates:
            apply_updates(md_path, updates)
            return "ok (backfill archivio_audio_url, gia' su archive.org)"
        return "skip (gia' fatto)"

    audio_url = fm["audio"]
    date_str = str(fm.get("date"))
    identifier = f"ilvolodellasera-{date_str}-{md_path.stem}" if md_path.parent.name != "episodi" else f"ilvolodellasera-{date_str}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        local_audio = tmp / Path(audio_url).name
        print(f"  scarico {audio_url}")
        download(audio_url, local_audio)

        if need_transcribe:
            print("  trascrivo con WhisperX (puo' richiedere piu' di un'ora su CPU)...")
            json_path = transcribe(local_audio, hf_token)
            TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
            dest = TRANSCRIPT_DIR / f"{md_path.stem}.json"
            dest.write_bytes(json_path.read_bytes())
            updates["trascrizione"] = str(dest.relative_to(ROOT)).replace("\\", "/")

        if need_upload:
            print(f"  carico su archive.org come '{identifier}'...")
            metadata = {
                "title": fm.get("title", identifier),
                "mediatype": "audio",
                "collection": "opensource_audio",
                "date": date_str,
                "description": fm.get("resumen", ""),
                "subject": "; ".join(fm.get("temi", []) or []),
                "source": fm.get("fonte", ""),
            }
            archive_url = upload_to_archive(identifier, local_audio, metadata, ia_keys[0], ia_keys[1])
            updates["archivio_audio_url"] = archive_url
            updates["audio"] = archive_url

    apply_updates(md_path, updates)
    return "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--reverse", action="store_true", help="parti dai piu' recenti (utile per correre in parallelo con un'altra istanza)")
    args = parser.parse_args()

    hf_token = None if args.skip_transcribe else load_lines(HF_TOKEN_FILE)
    ia_keys = None if args.skip_upload else load_lines(IA_KEYS_FILE, count=2)

    md_files = sorted(EPISODI_DIR.rglob("*.md"), reverse=args.reverse)
    if args.limit:
        md_files = md_files[:args.limit]

    for md_path in md_files:
        print(f"{md_path.relative_to(ROOT)}:")
        try:
            result = process_file(md_path, hf_token, ia_keys, not args.skip_transcribe, not args.skip_upload)
        except Exception as e:
            result = f"ERRORE: {e}"
        print(f"  -> {result}")
        if not args.skip_upload and result == "ok":
            time.sleep(UPLOAD_PAUSE_SEC)


if __name__ == "__main__":
    main()
