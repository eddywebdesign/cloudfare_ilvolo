#!/usr/bin/env python3
"""
Scarica la playlist musicale di ogni puntata da deejay.it e la salva in
data/playlist/<data>.json (stesso pattern di data/frammenti/, derivato
compatto e committato in git).

Fonte: https://www.deejay.it/programmi/il-volo-del-mattino/playlist/dettaglio/<data>/
La pagina ha una sola sezione <section class="playlist-list list"> con una
<span class="title ... song"> e una <span class="title small author"> per
ogni canzone, in ordine di trasmissione (nessun orario per canzone).

Idempotente: salta le puntate che hanno gia' data/playlist/<data>.json,
a meno di --force.

Uso:
    python scripts/genera_playlist.py [--force] [data1 data2 ...]
    senza date processa tutte le puntate in content/episodi/.
"""
import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
EPISODI_DIR = ROOT / "content" / "episodi"
PLAYLIST_DIR = ROOT / "data" / "playlist"
URL_TEMPLATE = "https://www.deejay.it/programmi/il-volo-del-mattino/playlist/dettaglio/{data}/"
REQUEST_PAUSE_SEC = 2  # non martellare deejay.it

SONG_RE = re.compile(
    r'<span class="title[^"]*\bsong\b[^"]*">(.*?)</span>\s*'
    r'<span class="title[^"]*\bauthor\b[^"]*">(.*?)</span>',
    re.S,
)
SECTION_RE = re.compile(
    r'<section class="playlist-list list">(.*?)</section>', re.S
)


def estrai_canzoni(html_page):
    m = SECTION_RE.search(html_page)
    if not m:
        return []
    sezione = m.group(1)
    canzoni = []
    for titolo, autore in SONG_RE.findall(sezione):
        titolo = html.unescape(titolo).strip()
        autore = html.unescape(autore).strip()
        if titolo and autore:
            canzoni.append({"titolo": titolo, "artista": autore})
    return canzoni


def genera(data_str, force=False):
    dest = PLAYLIST_DIR / f"{data_str}.json"
    if dest.exists() and not force:
        print(f"  {data_str}: gia' presente, salto")
        return

    url = URL_TEMPLATE.format(data=data_str)
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        print(f"  {data_str}: HTTP {resp.status_code}, salto")
        return

    canzoni = estrai_canzoni(resp.text)
    if not canzoni:
        print(f"  {data_str}: nessuna playlist trovata")
        return

    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(canzoni, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {data_str}: {len(canzoni)} canzoni -> {dest.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date (YYYY-MM-DD) da processare")
    parser.add_argument("--force", action="store_true", help="ri-scarica anche se gia' presente")
    args = parser.parse_args()

    date_list = args.date or sorted(p.stem for p in EPISODI_DIR.glob("*.md") if p.stem != "_index")

    print(f"Genero playlist per {len(date_list)} puntate...")
    for i, d in enumerate(date_list):
        genera(d, force=args.force)
        if i < len(date_list) - 1:
            time.sleep(REQUEST_PAUSE_SEC)


if __name__ == "__main__":
    main()
