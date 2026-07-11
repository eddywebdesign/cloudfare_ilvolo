# Tracker locale del consumo giornaliero di token Groq (piano free: 500K TPD per
# llama-3.1-8b-instant, condiviso a livello di organizzazione tra tutte le chiamate
# — classificazione frammenti E estrazione riferimenti culturali usano lo stesso modello).
# Il piano free NON addebita nulla senza carta di credito registrata: se si supera il
# limite, l'API risponde 429 e basta, nessun costo nascosto. Questo tracker serve solo
# a fermarsi PRIMA di sprecare una chiamata che fallirebbe comunque, per uscire pulito
# a meta' batch invece di un errore a caso.

import json
from datetime import date
from pathlib import Path

STATO_PATH = Path(__file__).resolve().parent.parent / "logs" / "groq_budget_state.json"
LIMITE_GIORNALIERO_TPD = 500_000
MARGINE_SICUREZZA_TPD = 450_000  # ci fermiamo prima del tetto reale per non rischiare 429 a meta' chiamata


def _leggi_stato() -> dict:
    if not STATO_PATH.exists():
        return {"data": str(date.today()), "token_usati": 0}
    stato = json.loads(STATO_PATH.read_text(encoding="utf-8"))
    if stato.get("data") != str(date.today()):
        return {"data": str(date.today()), "token_usati": 0}
    return stato


def registra_uso(token_consumati: int) -> None:
    """Da chiamare con resp.usage.total_tokens dopo OGNI chiamata Groq riuscita."""
    stato = _leggi_stato()
    stato["token_usati"] += token_consumati
    STATO_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATO_PATH.write_text(json.dumps(stato), encoding="utf-8")


def budget_disponibile() -> bool:
    """True se c'e' ancora margine per una nuova chiamata oggi."""
    stato = _leggi_stato()
    return stato["token_usati"] < MARGINE_SICUREZZA_TPD


def token_usati_oggi() -> int:
    return _leggi_stato()["token_usati"]
