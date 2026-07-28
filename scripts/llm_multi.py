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
from groq import Groq, RateLimitError as GroqRateLimitError

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
    # Mistral: il tier gratuito "Experiment" darebbe ~1 miliardo di token/mese
    # (~33M/giorno) secondo fonti terze, ma Mistral NON pubblica i limiti esatti.
    # Margine volutamente prudente finche' non si legge il numero vero nella
    # console: meglio lasciare capacita' inutilizzata che sbattere sui 429.
    "mistral": {"tpd": 2_500_000, "margine": 2_000_000},
}
# ⚠️ "mistral" NON e' in questa lista di proposito (2026-07-26): il codice c'e' ed e'
# pronto, ma un provider entra nella rotazione di produzione SOLO dopo aver superato
# il banco di prova sui 14 episodi di riferimento
# (scripts/linux/test_qualita_identificazione.py --provider mistral --campione storico).
# Motivo: un modello scadente non fallisce in modo visibile — nessun errore nei log, il
# processo gira, i file si scrivono — semplicemente trova meno opere e inventa gli autori.
# Misurato il 26/07 con Ollama: recall 35% contro 88%, e nessun log lo segnalava.
ORDINE_PROVIDER = ["groq", "cerebras", "gemini"]  # alternati per bilanciare il carico

# Modello Groq: sovrascrivibile con ILVOLO_GROQ_MODEL per confrontare modelli
# diversi col banco di prova (scripts/linux/test_qualita_identificazione.py) senza
# toccare il codice. I limiti Groq sono PER MODELLO, non per account (verificato
# nella console il 2026-07-26): llama-3.1-8b-instant 500K token/giorno,
# openai/gpt-oss-120b 200K, qwen/qwen3.6-27b 200K, openai/gpt-oss-20b 200K —
# usarne piu' d'uno somma le quote invece di dividerle.
GROQ_MODEL = os.environ.get("ILVOLO_GROQ_MODEL", "llama-3.1-8b-instant")
# I modelli "reasoning" sprecano token in ragionamento nascosto senza questo
# vincolo (gia' verificato su Cerebras, vedi CEREBRAS_MODELLI_REASONING).
GROQ_MODELLI_REASONING = {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b"}
GROQ_KEY_FILE = Path.home() / "API GROQ IA.txt"
CEREBRAS_KEY_FILE = Path.home() / "API Cerebras.txt"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_KEY_FILE = Path.home() / "API_google_AI.txt"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Mistral (chiave recuperata dall'utente il 2026-07-26). Modelli europei,
# storicamente piu' solidi sull'italiano — che qui conta, il corpus e' tutto in
# italiano e qwen sbagliava proprio sui nomi italiani. Sovrascrivibile per
# confrontare mistral-small e mistral-large col banco di prova.
MISTRAL_MODEL = os.environ.get("ILVOLO_MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_KEY_FILE = Path.home() / "API_Mistral.txt"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"

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
# Finestra di contesto. Deve contenere prompt + testo + risposta: misurati 15.480
# caratteri di prompt (~4.000 token) piu' 2.000 di risposta, quindi 8192 lascia
# margine senza sprecare VRAM (la cache cresce con la finestra). Regolabile senza
# toccare il codice, perche' cambiando modello o CHUNK_SIZE cambia anche questa.
OLLAMA_NUM_CTX = int(os.environ.get("ILVOLO_OLLAMA_NUM_CTX", "8192"))
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


# Mistral, Cerebras e Groq parlano tutti lo schema OpenAI: la risposta ha la stessa
# identica forma, quindi si riusa l'adattatore invece di scriverne un terzo uguale
# (la logica duplicata a mano tra file/classi e' una causa nota di disallineamento
# in questo progetto). Alias con un nome onesto su cosa rappresenta davvero.
_RispostaOpenAICompat = _CerebrasResponse


class _MistralCompletions:
    """Client Mistral. API compatibile OpenAI, quindi stessa forma degli altri:
    client.chat.completions.create(...)."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self, model: str, **kwargs):
        payload = {"model": model, **kwargs}
        r = requests.post(
            f"{MISTRAL_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        if r.status_code == 429:
            _marca_cooldown("mistral")
        r.raise_for_status()
        return _RispostaOpenAICompat(r.json())


class _MistralChat:
    def __init__(self, api_key: str):
        self.completions = _MistralCompletions(api_key)


class MistralClient:
    def __init__(self, api_key: str):
        self.chat = _MistralChat(api_key)


class _CerebrasChat:
    def __init__(self, api_key: str):
        self.completions = _CerebrasCompletions(api_key)


class CerebrasClient:
    """Client minimale, stessa forma di Groq(): client.chat.completions.create(...)."""

    def __init__(self, api_key: str):
        self.chat = _CerebrasChat(api_key)


def modello_cerebras_migliore(api_key: str) -> str:
    """Interroga il catalogo modelli Cerebras e sceglie il primo disponibile in
    ordine di preferenza. Il catalogo cambia nel tempo, non va hardcodato un solo nome.

    ILVOLO_CEREBRAS_MODEL forza un modello preciso, come gia' fanno ILVOLO_GROQ_MODEL
    e ILVOLO_MISTRAL_MODEL: senza questa leva il banco di prova non puo' misurare un
    modello Cerebras specifico (la scelta automatica renderebbe il confronto una
    lotteria del catalogo). Il nome forzato NON viene validato contro il catalogo:
    un nome sbagliato si manifesta come errore alla prima chiamata, non qui."""
    forzato = os.environ.get("ILVOLO_CEREBRAS_MODEL")
    if forzato:
        return forzato
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

        # ⚠️ La chiave va nell'HEADER, mai nella query string (corretto 2026-07-27):
        # con `?key=...` nell'URL, ogni errore HTTP finiva nei log in chiaro, perche'
        # raise_for_status() include l'URL completo nel messaggio dell'eccezione —
        # misurate 1295 occorrenze della chiave reale in logs/clasificacion_omv.log.
        # 'x-goog-api-key' e' la forma supportata da Google; provata dal vivo contro
        # l'API reale (200 OK) e verificato che il messaggio d'errore non contenga
        # piu' alcuna chiave.
        r = requests.post(
            f"{GEMINI_BASE_URL}/models/{model}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
            json=payload, timeout=60,
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
        # Due trappole DIVERSE della stessa API, e vanno trattate in modo opposto.
        #
        # 1) response_format si ignora di proposito: NON passare "format": "json" a
        #    ollama, vedi il commento su OLLAMA_BASE_URL sopra (testato empiricamente,
        #    con quel vincolo il modello collassa su un oggetto invece dell'array).
        #
        # 2) ⚠️ num_ctx VA IMPOSTATO, ed e' l'opposto: se non glielo si dice, ollama usa
        #    una finestra predefinita di 2048 token e TRONCA il prompt in silenzio.
        #    Misurato il 2026-07-29: il prompt reale dell'estrazione e' 15.480 caratteri,
        #    cioe' ~4.000 token, piu' 2.000 di risposta attesa. Il modello vedeva meno
        #    della meta' di quello che gli mandavamo, rispondeva vuoto, e ogni chunk
        #    falliva con "Expecting value: line 1 column 1".
        #    Conseguenza da non dimenticare: la recall del 35% misurata il 2026-07-26,
        #    che fece declassare il modello locale a opt-in, e' stata misurata cosi' -
        #    su un prompt mutilato, quindi non dice nulla sulla qualita' del modello.
        #    qwen2.5:14b regge 32.768 token, il limite era solo nostro.
        payload = {
            "model": model, "messages": messages, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": OLLAMA_NUM_CTX},
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


class _GroqCompletions:
    """Involucro sottile sul client Groq ufficiale: inoltra tutto tale e quale,
    aggiungendo `reasoning_effort="low"` per i modelli gpt-oss. Senza quel
    parametro il modello consuma gran parte di max_tokens in ragionamento
    nascosto e la risposta JSON arriva troncata (stesso comportamento gia'
    verificato su Cerebras, vedi CEREBRAS_MODELLI_REASONING)."""

    def __init__(self, reale):
        self._reale = reale

    def create(self, model: str, **kwargs):
        if model in GROQ_MODELLI_REASONING:
            kwargs["reasoning_effort"] = "low"
            # Niente response_format per questa famiglia: la modalita' json_object di
            # Groq pretende un OGGETTO al primo livello, mentre i nostri prompt
            # chiedono un ARRAY. llama-3.1-8b-instant se la cava, gpt-oss-120b
            # obbedisce al prompt e restituisce '[]' — che Groq respinge con
            # 400 json_validate_failed, perdendo il chunk (verificato dal vivo il
            # 2026-07-26). Cambiare il prompt renderebbe i risultati non piu'
            # confrontabili con le misure gia' fatte, quindi si toglie il vincolo:
            # llm_multi.estrai_json() gestisce gia' array, oggetti e fence markdown.
            kwargs.pop("response_format", None)
        try:
            return self._reale.chat.completions.create(model=model, **kwargs)
        except GroqRateLimitError as e:
            # ⚠️ BUG CORRETTO 2026-07-27: a differenza di Cerebras/Gemini/Mistral,
            # questo wrapper non chiamava MAI _marca_cooldown su un 429 — l'eccezione
            # del client ufficiale Groq passava attraverso senza lasciare alcun
            # segnale. Risultato osservato dal vivo: provider_disponibile() rivota
            # Groq identico al chunk successivo (il tracker locale di budget non era
            # ancora aggiornato/sincronizzato con l'uso reale), producendo 151 errori
            # 429 consecutivi in un log reale prima che qualcuno se ne accorgesse.
            # Struttura dell'eccezione verificata leggendo il sorgente installato
            # (groq/_exceptions.py): .status_code e .response (httpx.Response) sono
            # attributi reali, non assunti.
            msg = str(e)
            # Un limite "per day" (TPD/RPD) non si libera in pochi secondi: usare il
            # cooldown breve pensato per RPM/TPM (COOLDOWN_429_S=65) qui sarebbe
            # inutile quanto non avere alcun cooldown. Formato osservato due volte
            # dal vivo nei log di stanotte: "Please try again in 10m23.6352s" /
            # "5m28.4928s" — sempre almeno minuti+secondi, mai solo secondi bruti.
            m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", msg)
            if m:
                h, mnt, s = m.groups()
                secondi = int(h or 0) * 3600 + int(mnt or 0) * 60 + float(s)
                secondi = min(secondi + 5, 3600)  # +5s di margine, tetto 1h di buon senso
            elif "per day" in msg.lower():
                secondi = 1800  # limite giornaliero ma tempo non parsabile: prudente
            else:
                secondi = COOLDOWN_429_S  # RPM/TPM breve, comportamento gia' noto
            _marca_cooldown("groq", secondi)
            print(f"  Groq: 429 ricevuto, cooldown {secondi:.0f}s (era senza cooldown prima del 2026-07-27)")
            raise


class _GroqChat:
    def __init__(self, reale):
        self.completions = _GroqCompletions(reale)


class GroqClient:
    """Stessa forma degli altri client (client.chat.completions.create(...))."""

    def __init__(self, api_key: str):
        self.chat = _GroqChat(Groq(api_key=api_key))


def client_e_modello(provider: str):
    """Ritorna (client, model) per il provider richiesto, con caching (un solo
    client/una sola chiamata a /models per provider per l'intera esecuzione)."""
    if provider not in _client_cache:
        if provider == "groq":
            key = _load_key("GROQ_API_KEY", GROQ_KEY_FILE, "Groq")
            _client_cache["groq"] = GroqClient(api_key=key)
            _model_cache["groq"] = GROQ_MODEL
            if GROQ_MODEL != "llama-3.1-8b-instant":
                print(f"  Groq: modello sovrascritto da ILVOLO_GROQ_MODEL -> '{GROQ_MODEL}'")
        elif provider == "cerebras":
            key = _load_key("CEREBRAS_API_KEY", CEREBRAS_KEY_FILE, "Cerebras")
            _client_cache["cerebras"] = CerebrasClient(api_key=key)
            _model_cache["cerebras"] = modello_cerebras_migliore(key)
            print(f"  Cerebras: modello selezionato '{_model_cache['cerebras']}'")
        elif provider == "mistral":
            key = _load_key("MISTRAL_API_KEY", MISTRAL_KEY_FILE, "Mistral")
            _client_cache["mistral"] = MistralClient(api_key=key)
            _model_cache["mistral"] = MISTRAL_MODEL
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
