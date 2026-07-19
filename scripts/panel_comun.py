# Funzioni e costanti condivise tra i pannelli di controllo che leggono lo
# stesso stato pubblicato sullo share OMV: scripts/linux/panel_control.py
# (K16, locale) e scripts/panel_estado_hp14.py (HP14, remoto via SSH+share).
#
# Cio' che resta specifico per macchina NON e' qui: dimensioni/font finestra
# (RDP condiviso su K16 vs desktop Windows su HP14), come si rileva whisperx
# (processo locale su K16, SSH su HP14), i bottoni di controllo remoto.
#
# Nato il 2026-07-19 da un bug reale: formatear_fecha esisteva solo in
# panel_control.py, dimenticata nell'altro pannello per un mese (la card
# equivalente su HP14 mostrava l'ISO grezzo invece di DD/MM/AAAA).

import json
from datetime import datetime
from pathlib import Path

# Paleta sobria (fondo oscuro, acentos planos), identica in entrambi i pannelli.
COLOR_FONDO = "#1e2530"
COLOR_TARJETA = "#2a3342"
COLOR_TEXTO = "#e6e9ef"
COLOR_TEXTO_SUAVE = "#9aa5b1"
COLOR_VERDE = "#3ba776"
COLOR_ROJO = "#c0392b"
COLOR_NARANJA = "#c9822a"
COLOR_AZUL = "#3d7fd9"


def formatear_fecha(iso_str) -> str:
    """Convierte 'AAAA-MM-DDTHH:MM:SS[+HH:MM]' en 'DD/MM/AAAA HH:MM:SS' (con
    espacio, formato mas habitual que el ISO crudo)."""
    try:
        return datetime.fromisoformat(iso_str).strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return str(iso_str)


def contar_progreso_total(frammenti_dir: Path, audio_root: Path):
    """Devuelve (transcritos, total_audio) contando frammenti_dir/*.json
    contra todos los .mp3 de audio_root (recursivo), o (transcritos, None)
    si audio_root no existe/no es alcanzable en este momento."""
    try:
        transcritos = sum(1 for _ in frammenti_dir.glob("*.json"))
    except OSError:
        transcritos = None
    if not audio_root.exists():
        return transcritos, None
    try:
        total_audio = sum(1 for _ in audio_root.rglob("*.mp3"))
    except OSError:
        total_audio = None
    return transcritos, total_audio


def contar_estado_classificazione(frammenti_dir: Path):
    """Stessa logica della pagina /frammenti-recenti/ (renderStats()): quanti
    frammenti sono classificati, quanti in coda, quanti scartati perche'
    troppo corti (<6 parole, mai classificati per design)."""
    tot = classificati = brevi = 0
    try:
        for f in frammenti_dir.glob("*.json"):
            for x in json.loads(f.read_text(encoding="utf-8")):
                tot += 1
                if x.get("tipo"):
                    classificati += 1
                elif len((x.get("testo") or "").split()) < 6:
                    brevi += 1
    except OSError:
        return None
    return {"tot": tot, "classificati": classificati, "brevi": brevi, "da_fare": tot - classificati - brevi}


def leer_json_estado(path: Path, path_fallback: Path | None = None):
    """Legge un JSON di estado (estado_clasificacion.json, estado_push.json)
    scritto da un altro processo/macchina su uno share condiviso. Ritorna
    None se il file non esiste o non e' JSON valido -- mai un'eccezione, i
    chiamanti lo trattano come "senza dati" invece di bloccarsi.

    path_fallback: usato da panel_estado_hp14.py quando ILVOLO_LOGS_DIR non
    e' visibile nel processo corrente (setx non si propaga a sessioni gia'
    aperte finche' non c'e' un logout/login o un riavvio di explorer.exe --
    capitato davvero il 18/07/2026)."""
    p = path if path.exists() else path_fallback
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None
