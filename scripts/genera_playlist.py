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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EPISODI_DIR = ROOT / "content" / "episodi"
PLAYLIST_DIR = dati_root(ROOT) / "playlist"
URL_TEMPLATE = "https://www.deejay.it/programmi/il-volo-del-mattino/playlist/dettaglio/{data}/"
REQUEST_PAUSE_SEC = 3  # non martellare deejay.it

SECTION_RE = re.compile(
    r'<section class="playlist-list list">(.*?)</section>', re.S
)
ARTICLE_RE = re.compile(r'<article>(.*?)</article>', re.S)
IMG_RE = re.compile(r'<img src="([^"]*)"')
SONG_RE = re.compile(
    r'<span class="title[^"]*\bsong\b[^"]*">(.*?)</span>\s*'
    r'<span class="title[^"]*\bauthor\b[^"]*">(.*?)</span>',
    re.S,
)


def estrai_canzoni(html_page):
    """Estrae titolo/artista/cover da una pagina 'playlist del giorno' di deejay.it.

    Scarta le cover generiche (senza /covers_sorted/, es. slot pubblicita')
    che risultano rotte (404) anche sul sito originale.
    """
    m = SECTION_RE.search(html_page)
    if not m:
        return []
    sezione = m.group(1)
    canzoni = []
    for articolo in ARTICLE_RE.findall(sezione):
        song_m = SONG_RE.search(articolo)
        if not song_m:
            continue
        titolo = html.unescape(song_m.group(1)).strip()
        autore = html.unescape(song_m.group(2)).strip()
        if not titolo or not autore:
            continue
        img_m = IMG_RE.search(articolo)
        cover = html.unescape(img_m.group(1)).strip() if img_m else ""
        if "covers_sorted" not in cover:
            # url generica di deejay.it senza cover reale (es. slot pubblicita'),
            # torna 404 anche dal loro stesso sito: meglio non mostrare l'img.
            cover = ""
        canzoni.append({"titolo": titolo, "artista": autore, "cover": cover})
    return canzoni


def genera(data_str, force=False):
    """Scarica ed elabora la playlist di una singola data (YYYY-MM-DD).

    Idempotente: salta se data/playlist/<data_str>.json esiste gia',
    a meno di force=True. Non scrive nulla se la puntata non ha
    playlist (pagina 200 ma senza canzoni, es. puntate pre-ottobre 2019).

    Ritorna True se ha fatto una richiesta di rete (per decidere se
    aspettare REQUEST_PAUSE_SEC prima della prossima), False se ha
    solo saltato un file gia' presente (nessun bisogno di pausa).
    """
    dest = PLAYLIST_DIR / f"{data_str}.json"
    if dest.exists() and not force:
        print(f"  {data_str}: gia' presente, salto")
        return False

    url = URL_TEMPLATE.format(data=data_str)
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    except requests.exceptions.RequestException as e:
        # Un timeout/errore di rete occasionale non deve far crashare tutto
        # lo script: si salta la data (resta assente, recuperabile in un
        # rilancio futuro, idempotente) e si continua con la prossima.
        print(f"  {data_str}: errore rete ({e}), salto")
        return True
    if resp.status_code != 200:
        print(f"  {data_str}: HTTP {resp.status_code}, salto")
        return True

    canzoni = estrai_canzoni(resp.text)
    if not canzoni:
        print(f"  {data_str}: nessuna playlist trovata")
        return True

    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(canzoni, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {data_str}: {len(canzoni)} canzoni -> {dest.relative_to(ROOT)}")
    return True


def main():
    """CLI: processa le date passate come argomento, o tutti gli episodi
    in content/episodi/ se nessuna data e' specificata (vedi --help)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date (YYYY-MM-DD) da processare")
    parser.add_argument("--force", action="store_true", help="ri-scarica anche se gia' presente")
    args = parser.parse_args()

    date_list = args.date or sorted(p.stem for p in EPISODI_DIR.glob("*.md") if p.stem != "_index")

    print(f"Genero playlist per {len(date_list)} puntate...")
    for i, d in enumerate(date_list):
        fatta_richiesta = genera(d, force=args.force)
        if fatta_richiesta and i < len(date_list) - 1:
            time.sleep(REQUEST_PAUSE_SEC)


if __name__ == "__main__":
    main()
