#!/usr/bin/env python3
"""
Carica su archive.org l'archivio audio locale storico (2012-2016), quello
con cui e' nato il progetto, in D:\\Docs\\il_volo_del_mattino\\Volo del
mattino\\audio — che il pipeline online (sync_archive.py / backfill-*)
non tocca perche' non ha URL remoti.

Per ogni mp3 cerca di dedurre la data dal nome file o dalla cartella anno.
Se in content/episodi/ esiste gia' un file per quella data, lo SALTA (non
sovrascrive mai una data gia' presente — l'archivio locale serve solo a
riempire i buchi).

Uso:
    python scripts/upload_local_archive.py [--dry-run]

Richiede:
    ~/archive_org.txt    due righe: access_key, secret_key (S3-like)
"""
import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EPISODI_DIR = ROOT / "content" / "episodi"
IA_KEYS_FILE = Path.home() / "archive_org.txt"
LOCAL_AUDIO_DIR = Path(r"D:\Docs\il_volo_del_mattino\Volo del mattino\audio")


def load_lines(path, count=2):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return lines[:count]


def existing_dates():
    return {p.stem for p in EPISODI_DIR.rglob("*.md")}


def guess_date(mp3_path):
    """Ritorna (iso, certo:bool) o (None, False) se non deducibile."""
    name = mp3_path.stem

    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if m:
        yyyy, mm, dd = m.groups()
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"{yyyy}-{mm}-{dd}", True

    m = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", name)
    if m:
        dd, mm, yy = m.groups()
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"20{yy}-{mm}-{dd}", True

    year_folder = next((p.name for p in mp3_path.parents if re.fullmatch(r"20\d{2}", p.name)), None)
    m = re.search(r"(?<!\d)(\d{2})(\d{2})(?!\d)", name)
    if year_folder and m:
        dd, mm = m.groups()
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            return f"{year_folder}-{mm}-{dd}", True

    if m:
        dd, mm = m.groups()
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            year_guess = mp3_path.stat().st_mtime
            import datetime
            yyyy = datetime.datetime.fromtimestamp(year_guess).year
            return f"{yyyy}-{mm}-{dd}", False  # anno incerto, dedotto dalla data di modifica del file

    return None, False


def slug_title(mp3_path):
    name = mp3_path.stem
    name = re.sub(r"^(00 - |__-_|-)", "", name)
    name = re.sub(r"^(volo|reloaded_volo|reloaded)[_ ]?", "", name, flags=re.I)
    name = re.sub(r"[_-]+", " ", name).strip()
    return name.capitalize() or mp3_path.stem


def build_front_matter(iso, mp3_path, is_reloaded, incerto, archive_url):
    title = slug_title(mp3_path)
    lines = [
        "---",
        f"title: \"{title}\"",
        f"date: {iso}",
        "draft: false",
        f"resumen: \"Recuperato dall'archivio audio locale storico (2012-2016).{' Data dedotta, potrebbe non essere esatta.' if incerto else ''}\"",
        f"audio: \"{archive_url}\"",
        f"fonte: \"archivio locale: {mp3_path.name}\"",
    ]
    if is_reloaded:
        lines.append("is_reloaded: true")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def upload_local(identifier, mp3_path, metadata, access_key, secret_key, dry_run):
    if dry_run:
        return f"https://archive.org/download/{identifier}/{mp3_path.name} (DRY RUN, non caricato)"
    import internetarchive as ia
    responses = ia.upload(
        identifier, files=[str(mp3_path)], metadata=metadata,
        access_key=access_key, secret_key=secret_key,
    )
    for r in responses:
        r.raise_for_status()
    return f"https://archive.org/download/{identifier}/{mp3_path.name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ia_keys = None if args.dry_run else load_lines(IA_KEYS_FILE)
    known_dates = existing_dates()

    mp3_files = sorted(LOCAL_AUDIO_DIR.rglob("*.mp3"))
    print(f"Trovati {len(mp3_files)} file audio locali.")

    created, skipped_existing, undated = 0, 0, 0
    for mp3_path in mp3_files:
        iso, certo = guess_date(mp3_path)
        if not iso:
            print(f"⚠️  {mp3_path.name}: impossibile dedurre una data, salto.")
            undated += 1
            continue
        if iso in known_dates:
            print(f"  {iso} ({mp3_path.name}): gia' presente, salto (l'archivio locale non sovrascrive).")
            skipped_existing += 1
            continue

        is_reloaded = "reload" in mp3_path.name.lower()
        identifier = f"ilvolodellasera-{iso}-locale"
        print(f"{mp3_path.relative_to(LOCAL_AUDIO_DIR)} -> {iso}{'' if certo else ' (incerta)'}")

        metadata = {
            "title": slug_title(mp3_path),
            "mediatype": "audio",
            "collection": "opensource_audio",
            "date": iso,
            "description": f"Recuperato dall'archivio audio locale storico. File originale: {mp3_path.name}",
        }
        try:
            archive_url = upload_local(identifier, mp3_path, metadata, *(ia_keys or [None, None]), args.dry_run)
        except Exception as e:
            print(f"  ERRORE: {e}")
            continue

        frontmatter = build_front_matter(iso, mp3_path, is_reloaded, not certo, archive_url)
        if not args.dry_run:
            (EPISODI_DIR / f"{iso}.md").write_text(frontmatter, encoding="utf-8")
        known_dates.add(iso)
        print(f"  -> ok ({archive_url})")
        created += 1

    print(f"\nFatto. Creati: {created} · Gia' presenti (saltati): {skipped_existing} · Senza data: {undated}")


if __name__ == "__main__":
    main()
