#!/usr/bin/env python3
"""Compone le 21 puntate del campione in 4 file di testo, ognuno con piu' episodi
interi (non piu' spezzati in blocchi da 49 righe), in ordine cronologico, pronti
per essere convertiti in PDF e dati a Gemini (che accetta PDF grandi ma non .txt).

Ogni episodio e' delimitato da intestazioni chiare per non far sovrapporre a Gemini
i riferimenti culturali di una puntata con quelli della successiva.
"""
import json
from pathlib import Path

RIFERIMENTO = Path("/home/eddy/ilvolodelmattino/scripts/linux/insieme_riferimento.json")
TESTO_DIR = Path("/mnt/ilvolodellasera-logs/banco_verifica/lettura_integrale/testo")
OUT_DIR = Path("/home/eddy/Claude_folder/Trascrizioni_Gemini_campione_21/composti")
N_FILE = 4


def gruppi(date, n):
    base = len(date) // n
    resto = len(date) % n
    i = 0
    for g in range(n):
        dim = base + (1 if g < resto else 0)
        yield date[i:i + dim]
        i += dim


def main():
    date = sorted(json.loads(RIFERIMENTO.read_text())["episodi"].keys())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, gruppo in enumerate(gruppi(date, N_FILE), start=1):
        parti = []
        for data in gruppo:
            testo = (TESTO_DIR / f"{data}.txt").read_text()
            parti.append(f"--- INIZIO EPISODIO {data} ---\n{testo}\n--- FINE EPISODIO {data} ---\n")

        out_path = OUT_DIR / f"campione_gemini_{i}-{N_FILE}_{gruppo[0]}_a_{gruppo[-1]}.txt"
        out_path.write_text("\n".join(parti))
        print(f"{out_path.name}: episodi {gruppo}")


if __name__ == "__main__":
    main()
