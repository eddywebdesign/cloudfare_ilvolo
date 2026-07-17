"""Cartella dati centrale del progetto (frammenti/pillole/riferimenti/playlist/trascrizioni).

Storicamente ogni checkout (HP14, K16) aveva la propria copia di data/ dentro
al repo, sincronizzata solo via git — creando doppioni e nessun punto unico di
accesso. Da 2026-07-17 i dati vivono in un'unica cartella condivisa sul server
OMV (\\\\192.168.8.80\\Media\\ilvolodellasera\\data\\, montata su K16 via CIFS).

Imposta la variabile d'ambiente ILVOLO_DATA_DIR con il path montato sulla
macchina corrente per usare la cartella condivisa; se non impostata, si
ricade sul vecchio data/ locale del repo (utile per test/sviluppo offline).
"""
import os
from pathlib import Path


def dati_root(root_repo: Path) -> Path:
    env = os.environ.get("ILVOLO_DATA_DIR")
    return Path(env) if env else root_repo / "data"


def logs_root(root_repo: Path) -> Path:
    """logs/ e' sempre sorella di data/ (sia in locale che nella cartella condivisa)."""
    return dati_root(root_repo).parent / "logs"
