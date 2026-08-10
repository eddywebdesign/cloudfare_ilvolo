#!/usr/bin/env python3
"""Spezza il testo integrale delle 21 puntate del campione in file da max 49 righe.

Serve a preparare l'input per Gemini, da confrontare con le letture Opus
gia' fatte sullo stesso campione. Le 21 date vengono da insieme_riferimento.json,
la stessa fonte usata dal banco Opus-Opus, per garantire che i due esperimenti
lavorino sugli stessi episodi.
"""
import json
from pathlib import Path

RIFERIMENTO = Path("/home/eddy/ilvolodelmattino/scripts/linux/insieme_riferimento.json")
TESTO_DIR = Path("/mnt/ilvolodellasera-logs/banco_verifica/lettura_integrale/testo")
OUT_DIR = Path("/home/eddy/Claude_folder/Trascrizioni_Gemini_campione_21")
MAX_RIGHE = 49


def main():
    date = sorted(json.loads(RIFERIMENTO.read_text())["episodi"].keys())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mancanti = []
    tot_blocchi = 0

    for data in date:
        sorgente = TESTO_DIR / f"{data}.txt"
        if not sorgente.exists():
            mancanti.append(data)
            continue

        righe = sorgente.read_text().splitlines()
        blocchi = [righe[i:i + MAX_RIGHE] for i in range(0, len(righe), MAX_RIGHE)]
        n = len(blocchi)

        for i, blocco in enumerate(blocchi, start=1):
            out_path = OUT_DIR / f"{data}_{i}-{n}.txt"
            out_path.write_text("\n".join(blocco) + "\n")

        tot_blocchi += n
        print(f"{data}: {len(righe)} righe -> {n} blocchi")

    print(f"\nPuntate processate: {len(date) - len(mancanti)}/{len(date)}")
    print(f"Blocchi totali scritti: {tot_blocchi}")
    if mancanti:
        print(f"ATTENZIONE, testo sorgente mancante per: {mancanti}")


if __name__ == "__main__":
    main()
