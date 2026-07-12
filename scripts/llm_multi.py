# Tracker + selezione multi-provider per la classificazione/estrazione via LLM: Groq
# (500K token/giorno free) + Cerebras (1.000.000 token/giorno free, no carta) usati in
# parallelo per triplicare circa il budget giornaliero combinato. Sostituisce
# groq_budget.py (stessa logica di tracking, generalizzata per piu' provider).
#
# Nessun costo nascosto su nessuno dei due: entrambi i piani free rispondono 429/errore
# quando il tetto e' superato, non addebitano nulla. Questo modulo si ferma PRIMA di
# sprecare una chiamata che fallirebbe comunque.

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
from groq import Groq

STATO_PATH = Path(__file__).resolve().parent.parent / "logs" / "llm_budget_state.json"

# Margine di sicurezza sotto il tetto reale, per non rischiare un 429 a meta' chiamata.
PROVIDER_CONFIG = {
    "groq": {"tpd": 500_000, "margine": 450_000},
    "cerebras": {"tpd": 1_000_000, "margine": 900_000},
}
ORDINE_PROVIDER = ["groq", "cerebras"]  # alternati per bilanciare il carico

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_KEY_FILE = Path.home() / "API GROQ IA.txt"
CEREBRAS_KEY_FILE = Path.home() / "API Cerebras.txt"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
# Preferenza modelli Cerebras: il catalogo cambia nel tempo, si sceglie il primo
# disponibile in questo ordine. I modelli "reasoning" (gpt-oss/glm) hanno bisogno di
# reasoning_effort=low per non sprecare token in ragionamento nascosto (verificato).
CEREBRAS_MODELLI_PREFERITI = [
    "llama-3.3-70b", "llama3.1-70b", "gpt-oss-120b", "zai-glm-4.7", "gemma-4-31b",
]
CEREBRAS_MODELLI_REASONING = {"gpt-oss-120b", "zai-glm-4.7"}


def _leggi_stato() -> dict:
    if not STATO_PATH.exists():
        return {"data": str(date.today()), "provider": {}}
    stato = json.loads(STATO_PATH.read_text(encoding="utf-8"))
    if stato.get("data") != str(date.today()):
        return {"data": str(date.today()), "provider": {}}
    return stato


def registra_uso(provider: str, token_consumati: int) -> None:
    """Da chiamare con resp.usage.total_tokens dopo OGNI chiamata riuscita."""
    stato = _leggi_stato()
    stato["provider"][provider] = stato["provider"].get(provider, 0) + token_consumati
    STATO_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATO_PATH.write_text(json.dumps(stato), encoding="utf-8")


def budget_disponibile(provider: str) -> bool:
    stato = _leggi_stato()
    usati = stato["provider"].get(provider, 0)
    return usati < PROVIDER_CONFIG[provider]["margine"]


def token_usati_oggi(provider: str) -> int:
    return _leggi_stato()["provider"].get(provider, 0)


def provider_disponibile() -> str | None:
    """Alterna tra i provider in ORDINE_PROVIDER, scegliendo quello con meno token
    usati oggi TRA quelli che hanno ancora budget. None se entrambi esauriti."""
    disponibili = [p for p in ORDINE_PROVIDER if budget_disponibile(p)]
    if not disponibili:
        return None
    return min(disponibili, key=token_usati_oggi)


def _load_key(env_var: str, file_path: Path, nome: str) -> str:
    key = os.environ.get(env_var, "")
    if not key and file_path.exists():
        key = file_path.read_text(encoding="utf-8").strip()
    if not key:
        print(f"Errore: chiave {nome} non trovata. Imposta {env_var} oppure salva la chiave in:\n  {file_path}")
        sys.exit(1)
    return key


class _CerebrasResponse:
    """Adatta la risposta HTTP di Cerebras alla stessa forma usata dal client Groq
    (resp.choices[0].message.content, resp.usage.total_tokens) per non dover
    riscrivere il codice chiamante in base al provider."""

    class _Usage:
        def __init__(self, total_tokens):
            self.total_tokens = total_tokens

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _CerebrasResponse._Message(content)

    def __init__(self, data: dict):
        content = data["choices"][0]["message"].get("content") or "{}"
        self.choices = [_CerebrasResponse._Choice(content)]
        self.usage = _CerebrasResponse._Usage(data.get("usage", {}).get("total_tokens", 0))


CEREBRAS_RETRY_429 = (5, 10, 20)  # secondi di attesa crescente, solo su 429 (limite reale ~5-6 RPM,
# verificato con test empirico il 2026-07-12 — il ritmo dello script ci sta sotto ma senza margine,
# un burst occasionale non deve far perdere l'intero batch)


class _CerebrasCompletions:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self, model: str, messages: list, max_tokens: int, temperature: float, response_format: dict):
        payload = {
            "model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "response_format": response_format,
        }
        if model in CEREBRAS_MODELLI_REASONING:
            payload["reasoning_effort"] = "low"

        tentativi = (0,) + CEREBRAS_RETRY_429
        for i, attesa in enumerate(tentativi):
            if attesa:
                print(f"      Cerebras 429 (troppe richieste/minuto), riprovo tra {attesa}s "
                      f"(tentativo {i+1}/{len(tentativi)})...")
                time.sleep(attesa)
            r = requests.post(
                f"{CEREBRAS_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload, timeout=60,
            )
            if r.status_code != 429:
                r.raise_for_status()
                return _CerebrasResponse(r.json())
        r.raise_for_status()  # esauriti i tentativi, propaga il 429 come errore normale


class _CerebrasChat:
    def __init__(self, api_key: str):
        self.completions = _CerebrasCompletions(api_key)


class CerebrasClient:
    """Client minimale, stessa forma di Groq(): client.chat.completions.create(...)."""

    def __init__(self, api_key: str):
        self.chat = _CerebrasChat(api_key)


def modello_cerebras_migliore(api_key: str) -> str:
    """Interroga il catalogo modelli Cerebras e sceglie il primo disponibile in
    ordine di preferenza. Il catalogo cambia nel tempo, non va hardcodato un solo nome."""
    try:
        r = requests.get(f"{CEREBRAS_BASE_URL}/models",
                          headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        disponibili = {m["id"] for m in r.json().get("data", [])}
    except Exception as e:
        print(f"  attenzione: impossibile leggere il catalogo Cerebras ({e}), uso fallback gpt-oss-120b")
        return "gpt-oss-120b"

    for preferito in CEREBRAS_MODELLI_PREFERITI:
        if preferito in disponibili:
            return preferito
    if disponibili:
        scelto = sorted(disponibili)[0]
        print(f"  attenzione: nessun modello preferito disponibile su Cerebras, uso '{scelto}' "
              f"(catalogo attuale: {sorted(disponibili)})")
        return scelto
    raise RuntimeError("Nessun modello disponibile su Cerebras (catalogo vuoto)")


_client_cache: dict[str, object] = {}
_model_cache: dict[str, str] = {}


def client_e_modello(provider: str):
    """Ritorna (client, model) per il provider richiesto, con caching (un solo
    client/una sola chiamata a /models per provider per l'intera esecuzione)."""
    if provider not in _client_cache:
        if provider == "groq":
            key = _load_key("GROQ_API_KEY", GROQ_KEY_FILE, "Groq")
            _client_cache["groq"] = Groq(api_key=key)
            _model_cache["groq"] = GROQ_MODEL
        elif provider == "cerebras":
            key = _load_key("CEREBRAS_API_KEY", CEREBRAS_KEY_FILE, "Cerebras")
            _client_cache["cerebras"] = CerebrasClient(api_key=key)
            _model_cache["cerebras"] = modello_cerebras_migliore(key)
            print(f"  Cerebras: modello selezionato '{_model_cache['cerebras']}'")
        else:
            raise ValueError(f"provider sconosciuto: {provider}")
    return _client_cache[provider], _model_cache[provider]
