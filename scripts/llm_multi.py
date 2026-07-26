# Tracker + selezione multi-provider per la classificazione/estrazione via LLM: Groq
# (500K token/giorno free) + Cerebras (1.000.000 token/giorno free, no carta) + Gemini
# (gemini-flash-lite-latest, free, no carta - aggiunto 2026-07-18 in vista della perdita
# del tier gratuito Cerebras ad agosto) usati in parallelo per aumentare il budget
# giornaliero combinato. Sostituisce groq_budget.py (stessa logica di tracking,
# generalizzata per piu' provider).
#
# Nessun costo nascosto su nessuno dei tre: tutti i piani free rispondono 429/errore
# quando il tetto e' superato, non addebitano nulla (verificato: i modelli "gemini-2.0-*"
# hanno limite 0 su questo account/progetto, serve billing abilitato - EVITATI apposta;
# gemini-flash-lite-latest invece ha quota free reale, verificata con chiamate vere il
# 2026-07-18, ~15 richieste/minuto misurate empiricamente prima del primo 429). Questo
# modulo si ferma PRIMA di sprecare una chiamata che fallirebbe comunque.

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from groq import Groq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import logs_root  # noqa: E402

STATO_PATH = logs_root(Path(__file__).resolve().parent.parent) / "llm_budget_state.json"

# Margine di sicurezza sotto il tetto reale, per non rischiare un 429 a meta' chiamata.
# Gemini: tetto giornaliero (TPD) NON verificato empiricamente (bruciare quota solo per
# scoprirlo non valeva la pena, dato che il piano free non addebita mai nulla) - il numero
# qui e' un placeholder alto apposta, la vera rete di sicurezza e' il retry-on-429 nel
# client (_GeminiCompletions), stesso principio gia' usato per Cerebras.
PROVIDER_CONFIG = {
    "groq": {"tpd": 500_000, "margine": 450_000},
    "cerebras": {"tpd": 1_000_000, "margine": 900_000},
    "gemini": {"tpd": 2_000_000, "margine": 1_800_000},
}
ORDINE_PROVIDER = ["groq", "cerebras", "gemini"]  # alternati per bilanciare il carico

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_KEY_FILE = Path.home() / "API GROQ IA.txt"
CEREBRAS_KEY_FILE = Path.home() / "API Cerebras.txt"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_KEY_FILE = Path.home() / "API_google_AI.txt"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Ollama locale (K16, RTX 5070) - installato 2026-07-23 come ripiego quando Groq/Cerebras/
# Gemini sono esauriti, per smaltire l'arretrato di classificazione senza aspettare il
# giorno dopo. Nessun tetto giornaliero (gira in locale), ma va usato con giudizio: la GPU
# e' la stessa della trascrizione, quindi resta l'ULTIMA scelta, mai la prima.
# NON passare "format": "json" all'API - testato empiricamente 2026-07-23: con quel vincolo
# qwen2.5:14b collassa su un singolo oggetto invece dell'array richiesto dal prompt: senza,
# segue correttamente lo schema (il prompt stesso chiede gia' un array JSON esplicito).
# keep_alive breve: il modello (9GB) si scarica dalla VRAM poco dopo l'ultimo uso invece di
# restare fisso, lasciando piu' margine alla trascrizione quando la coda di classificazione
# e' ferma.
OLLAMA_BASE_URL = "http://192.168.8.130:11434"  # K16 (GPU), non localhost -- la classificazione
# gira su OMV via cron, "localhost" punterebbe a OMV stesso che non ha Ollama installato.
# Ollama ascolta su 0.0.0.0:11434 (OLLAMA_HOST nel service override), raggiungibile in LAN.
OLLAMA_MODEL = "qwen2.5:14b-instruct-q4_K_M"
OLLAMA_KEEP_ALIVE = "30s"
OLLAMA_TIMEOUT_S = 120  # generoso: in coda dietro whisperx puo' volerci piu' di una chiamata cloud
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


# Cooldown in memoria (per l'intera esecuzione dello script, non persistito su disco
# come il budget giornaliero) per i limiti PER MINUTO (429) — trovato il 2026-07-24 che
# mancava del tutto: un 429 veniva assorbito dal retry interno del client e poi
# dimenticato, cosi' il chunk successivo tornava a scegliere lo stesso provider saturo,
# ripetendo l'attesa da capo per centinaia di chunk di fila (5335 occorrenze di "429" in
# un solo run reale). 65s: leggermente sopra la finestra tipica di rate-limit di 60s.
COOLDOWN_429_S = 65
_cooldown_hasta: dict[str, float] = {}


def _marca_cooldown(provider: str, secondi: float = COOLDOWN_429_S) -> None:
    _cooldown_hasta[provider] = time.time() + secondi


def _in_cooldown(provider: str) -> bool:
    return time.time() < _cooldown_hasta.get(provider, 0)


def provider_disponibile() -> str | None:
    """PRIMARIO: i provider cloud (Groq/Cerebras/Gemini), quello meno usato oggi.

    ⚠️ Ollama NON e' piu' il primario dal 2026-07-26, ed e' opt-in esplicito
    (ILVOLO_USA_OLLAMA=1). La promozione del 23/07 si basava su un confronto di
    qualita' mai fatto davvero: era stata misurata la VRAM libera, non la bonta'
    dei risultati. Misurata il 26/07 con un A/B vero (stessi 14 episodi, stesso
    prompt, stesso seed, harness scripts/linux/test_qualita_identificazione.py)
    contro un insieme di riferimento costruito a mano leggendo le puntate intere:

        recall Ollama (qwen2.5:14b)  35%  — autori corretti 2 su 6
        recall cloud (groq)          88%  — autori corretti 13 su 15

    Esempi reali del divario sullo stesso testo: "Smoke" -> Harvey Keitel (attore)
    invece di Wayne Wang; "I marciapiedi di New York" -> Woody Allen invece di
    Scorsese; "La meravigliosa vita delle api" -> "Maurizio" invece di Acinelli;
    un verso dei The Smiths ("good times for a change") trasformato in "Good
    Times" dei Chic. Il cloud, con lo STESSO prompt, produce Rob Reiner, Peter
    Chelsom, Garry Marshall, Claude Debussy e Ornella Vanoni corretti.

    Nessun fallback automatico su Ollama a budget cloud esaurito: e' una scelta
    deliberata di qualita' sopra throughput. Meglio fermarsi e riprendere domani
    (gli script gia' lo fanno, stampano "riprendera' domani") che riempire
    l'archivio di voci con l'autore sbagliato, che poi nessuno rivede piu'.
    Il backlog di 1103 episodi sta nel margine gratuito in ~5,3 giorni (misurato:
    15.025 token/episodio, margine combinato 3,15M/giorno = 210 episodi/giorno).

    Esclude i provider in COOLDOWN dopo un 429 recente (vedi _in_cooldown).
    Ritorna None se nessun provider e' utilizzabile ora."""
    disponibili = [p for p in ORDINE_PROVIDER if budget_disponibile(p) and not _in_cooldown(p)]
    if disponibili:
        return min(disponibili, key=token_usati_oggi)
    # Ultima risorsa SOLO se richiesto esplicitamente: qualita' nettamente
    # inferiore (vedi sopra), accettabile solo per esperimenti o per compiti
    # dove il divario non conta, mai per popolare l'archivio.
    if os.environ.get("ILVOLO_USA_OLLAMA") == "1" and _ollama_raggiungibile():
        return "ollama"
    return None


def _ollama_raggiungibile() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


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

        r = requests.post(
            f"{CEREBRAS_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        # Niente piu' retry-con-attesa-crescente sullo stesso provider (rimosso il
        # 2026-07-24, vedi COOLDOWN_429_S): un 429 e' un limite per minuto che non cede
        # in pochi secondi, insistere sprecava tempo. Si marca il cooldown e si fallisce
        # subito, il chiamante (classifica_frammenti) passera' al prossimo chunk, che
        # scegliera' un altro provider tramite provider_disponibile().
        if r.status_code == 429:
            _marca_cooldown("cerebras")
        r.raise_for_status()
        return _CerebrasResponse(r.json())


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


class _GeminiResponse:
    """Stessa forma di _CerebrasResponse: resp.choices[0].message.content, resp.usage.total_tokens."""

    class _Usage:
        def __init__(self, total_tokens):
            self.total_tokens = total_tokens

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _GeminiResponse._Message(content)

    def __init__(self, data: dict):
        parti = data["candidates"][0]["content"]["parts"]
        # I modelli "thinking" (flash-lite-latest incluso) restituiscono anche parti con
        # "thought": true prima della risposta vera - va scartata, altrimenti il JSON
        # atteso dal chiamante si rompe (contiene il ragionamento, non la risposta).
        content = next((p["text"] for p in parti if not p.get("thought")), parti[-1].get("text", "{}"))
        self.choices = [_GeminiResponse._Choice(content)]
        tot = data.get("usageMetadata", {}).get("totalTokenCount", 0)
        self.usage = _GeminiResponse._Usage(tot)


class _GeminiCompletions:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self, model: str, messages: list, max_tokens: int, temperature: float, response_format: dict):
        # Adatta la forma "messages" (system/user, stile OpenAI/Groq) al formato Gemini
        # (system_instruction separata + contents/parts, niente ruolo "system" in contents).
        system_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user_txt = "\n".join(m["content"] for m in messages if m["role"] != "system")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_txt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system_txt:
            payload["systemInstruction"] = {"parts": [{"text": system_txt}]}
        if response_format.get("type") == "json_object":
            payload["generationConfig"]["responseMimeType"] = "application/json"

        r = requests.post(
            f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={self._api_key}",
            headers={"Content-Type": "application/json"}, json=payload, timeout=60,
        )
        # Niente piu' retry-con-attesa-crescente sullo stesso provider (rimosso il
        # 2026-07-24, vedi COOLDOWN_429_S): misurato dal vivo 5335 occorrenze di 429 in
        # un solo run perche' il chunk successivo tornava a scegliere Gemini appena
        # saturo. Si marca il cooldown e si fallisce subito, il prossimo chunk passera'
        # a Groq/Cerebras tramite provider_disponibile().
        if r.status_code == 429:
            _marca_cooldown("gemini")
        r.raise_for_status()
        return _GeminiResponse(r.json())


class _GeminiChat:
    def __init__(self, api_key: str):
        self.completions = _GeminiCompletions(api_key)


class GeminiClient:
    """Client minimale, stessa forma di Groq()/CerebrasClient: client.chat.completions.create(...)."""

    def __init__(self, api_key: str):
        self.chat = _GeminiChat(api_key)


class _OllamaResponse:
    """Stessa forma di _CerebrasResponse/_GeminiResponse."""

    class _Usage:
        def __init__(self, total_tokens):
            self.total_tokens = total_tokens

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _OllamaResponse._Message(content)

    def __init__(self, data: dict):
        content = data.get("message", {}).get("content") or "[]"
        self.choices = [_OllamaResponse._Choice(content)]
        tot = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
        self.usage = _OllamaResponse._Usage(tot)


class _OllamaCompletions:
    def create(self, model: str, messages: list, max_tokens: int, temperature: float, response_format: dict):
        # response_format ignorato di proposito: NON passare "format": "json" all'API
        # ollama, vedi commento su OLLAMA_BASE_URL sopra per il motivo (testato empiricamente).
        payload = {
            "model": model, "messages": messages, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        r = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_S)
        r.raise_for_status()
        return _OllamaResponse(r.json())


class _OllamaChat:
    def __init__(self):
        self.completions = _OllamaCompletions()


class OllamaClient:
    """Client minimale, stessa forma degli altri: client.chat.completions.create(...)."""

    def __init__(self):
        self.chat = _OllamaChat()


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
        elif provider == "gemini":
            key = _load_key("GEMINI_API_KEY", GEMINI_KEY_FILE, "Gemini")
            _client_cache["gemini"] = GeminiClient(api_key=key)
            _model_cache["gemini"] = GEMINI_MODEL
        elif provider == "ollama":
            _client_cache["ollama"] = OllamaClient()
            _model_cache["ollama"] = OLLAMA_MODEL
        else:
            raise ValueError(f"provider sconosciuto: {provider}")
    return _client_cache[provider], _model_cache[provider]


def estrai_json(raw: str):
    """Parsa la risposta di un LLM che dovrebbe contenere SOLO JSON, tollerando
    l'incapsulamento in un blocco markdown.

    Serve perche' Ollama IGNORA `response_format={"type": "json_object"}` (i
    provider cloud lo rispettano server-side, lui no): qwen2.5:14b avvolge il
    JSON in ```json ... ``` in modo intermittente, e quando l'array risultato e'
    vuoto aggiunge anche prosa esplicativa DOPO il fence chiuso. Un json.loads()
    diretto fallisce con "Expecting value: line 1 column 1" e il chunk viene perso
    in silenzio -- successo davvero il 2026-07-23, 784+ fallimenti consecutivi,
    un batch girato a vuoto per 2h45m prima che qualcuno se ne accorgesse.

    Il fix era stato applicato solo a classifica_frammenti() e MAI a
    _groq_chunk() di trascrivi_e_estrai_clip.py: vive qui, in un posto solo,
    perche' entrambe le pipeline lo importano gia' (la logica duplicata a mano
    tra due file e' una causa nota di disallineamento in questo progetto).

    Ritorna l'oggetto Python parsato. Solleva json.JSONDecodeError se il
    contenuto non e' JSON nemmeno dopo l'estrazione dal fence -- il chiamante
    gestisce gia' l'eccezione con il suo retry.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        raw = m.group(1).strip() if m else raw.strip("`").strip()
    return json.loads(raw)
