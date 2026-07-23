# Trascrizione clip MP3 da frammenti_trascr_CPU/ + estrazione riferimenti culturali.
#
# Per ogni MP3:
#   1. Parsing filename → episodio_data (anno=2016, DDMM dal nome)
#   2. Trascrizione con faster-whisper (CPU, modello small, lingua it)
#      Audio decodificato via ffmpeg CLI per evitare conflitti con PyAV/WDAC.
#   3. Estrazione riferimenti con Groq (film, libro, musica; multi-riferimento)
#   4. Merge idempotente in data/riferimenti/2016-MM-DD.json
#
# Uso: python scripts/trascrivi_e_estrai_clip.py [clip1.mp3 clip2.mp3 ...]
#      senza argomenti: processa tutti gli MP3 in frammenti_trascr_CPU/
#
# Richiede: GROQ_API_KEY nell'ambiente

import difflib
import json
import re
import subprocess
import sys
import time
import types
from pathlib import Path

# Mock av e sottomoduli: faster_whisper li importa a livello di modulo ma noi
# passiamo l'audio come numpy array (via ffmpeg), quindi av non viene mai usato.
# Senza mock, il DLL di av viene bloccato da Windows Application Control (WDAC).
for _av_mod in [
    'av', 'av.audio', 'av.audio.codeccontext', 'av.audio.fifo', 'av.audio.format',
    'av.audio.frame', 'av.audio.layout', 'av.audio.plane', 'av.audio.resampler',
    'av.audio.stream', 'av.codec', 'av.codec.codec', 'av.codec.context',
    'av.container', 'av.container.core', 'av.container.input', 'av.container.output',
    'av.data', 'av.data.packet', 'av.descriptor', 'av.error', 'av.filter',
    'av.filter.context', 'av.filter.filter', 'av.filter.graph', 'av.filter.link',
    'av.frame', 'av.option', 'av.packet', 'av.plane', 'av.sidedata',
    'av.stream', 'av.subtitles', 'av.video',
]:
    sys.modules[_av_mod] = types.ModuleType(_av_mod)

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_multi  # noqa: E402
from dati_root import dati_root  # noqa: E402
CLIP_DIR = ROOT.parent / "frammenti_trascr_CPU"
RIF_DIR = dati_root(ROOT) / "riferimenti"

SAMPLE_RATE = 16000

SYSTEM = (
    "Sei un assistente che analizza trascrizioni del programma radiofonico italiano "
    "'Il Volo del Mattino' (Radio DeeJay), condotto da Fabio Volo. "
    "Rispondi SEMPRE e SOLO con un array JSON valido, nessun testo aggiuntivo."
)

PROMPT_TPL = """\
Dal seguente testo estratto da una puntata de "Il Volo del Mattino", \
estrai TUTTI i riferimenti culturali specifici presenti.

TESTO:
\"\"\"{testo}\"\"\"

Restituisci un array JSON (vuoto [] se non ci sono riferimenti chiari):
[
  {{"categoria": "film", "sottocategoria": "", "titolo": "...", "autore": "...", "anno": "...", "note": "..."}},
  ...
]

Regole:
- categoria: solo "film", "libro" o "musica"
- sottocategoria per "libro": "romanzo" | "poesia" | "saggio" | "citazione" | "lettura_volo" | "" (vuoto se generico)
  - "poesia": testi poetici, liriche, componimenti (es. Invictus, Divina Commedia)
  - "citazione": frasi o brani letti/citati da Fabio in trasmissione
  - "lettura_volo": Fabio legge ad alta voce un brano durante la puntata
  - "saggio": saggistica, filosofia, spiritualità, self-help
  - "romanzo": narrativa fiction o non-fiction
- sottocategoria per "film": "documentario" | "" (vuoto se fiction/generico)
- sottocategoria per "musica": sempre "" (vuoto)
- "libro" include poesie, saggi, romanzi (autore = poeta/scrittore)
- Se un titolo è sia poesia sia film (es. Invictus), crea DUE entry separate
- anno: anno di uscita/pubblicazione (stringa vuota se sconosciuto)
- autore: OBBLIGATORIO — regista/scrittore/artista che ha creato l'opera. Se non riesci
  a identificare un autore/regista/artista specifico e reale, NON includere il
  riferimento (meglio ometterlo che lasciare "autore" vuoto)
- note: max 12 parole su perché Fabio lo cita/legge/suona
- Non includere riferimenti vaghi o non identificabili
- "titolo" deve essere il titolo VERO e specifico di un'opera (film/libro/canzone) —
  MAI un argomento di conversazione generico, anche se Fabio ne parla a lungo
- ESCLUDI persone citate di passaggio (politici, giornalisti, personaggi pubblici,
  attori nominati SENZA il titolo di un'opera specifica) — includile SOLO come
  "autore" di un'opera nominata per titolo, mai come "titolo" loro stesse
- ESCLUDI testate giornalistiche/siti di notizie (New York Times, Repubblica, ecc.):
  non sono libri
- ESCLUDI marchi, prodotti, aziende, tecnologie (Volvo, Atari, Google, ecc.)
- ESCLUDI luoghi geografici (piazze, città, monumenti)
- ESCLUDI messaggi/post letti da social media (Facebook, Instagram, WhatsApp, SMS,
  email): il messaggio in sé NON è un libro/film/canzone, anche se racconta una
  storia — includi SOLO se DENTRO il messaggio viene citato il titolo di un'opera
  reale (in quel caso il riferimento è quell'opera, mai il messaggio stesso)
- ESCLUDI similitudini/paragoni di passaggio (es. "sembra un personaggio di [regista]",
  "mi ricorda [film/libro]", "è come in [opera]"): sono un paragone estemporaneo, non
  una citazione diretta — includi SOLO se si sta davvero parlando/discutendo di
  quell'opera specifica, non solo paragonando qualcuno/qualcosa ad essa
- Se un nome sembra una trascrizione fonetica imperfetta/deformata (es. errori di
  riconoscimento vocale su un nome noto), NON generarlo come titolo di un'opera:
  un titolo deve essere plausibile come opera reale, non un nome storpiato
- Nel dubbio se qualcosa è un'opera reale o solo un nome/argomento menzionato,
  ESCLUDI — meglio pochi riferimenti sicuri che tanti falsi positivi

ESEMPI REALI (da errori già fatti in passato — studiali prima di rispondere):
- CATTIVO (NON classificare così, trovato 2026-07-23): "Baracco Mava ha firmato un
  contratto da 60 milioni di dollari per il libro" -> è una trascrizione deformata di
  un nome noto (Barack Obama), nessun titolo di libro è nominato: ESCLUDI, non
  generare "Baracco Mava" né come titolo né come autore.
- CATTIVO (NON classificare così, trovato 2026-07-23): "il messaggio Facebook di DJ
  Francesco" -> è un post di social media, non un libro/film/canzone: ESCLUDI (a
  meno che il messaggio stesso citi il titolo di un'opera reale).
- CATTIVO (NON classificare così, trovato 2026-07-23): "sembra uno dei personaggi di
  Paolo Sorrentino, Gep Cabardella" -> è un paragone di passaggio, non si sta
  discutendo del film: ESCLUDI.
"""


def parse_data(filename: str) -> str | None:
    """Estrae episodio_data dal nome del file.
    Supporta: YYYYMMDD (es. 'Audio 20160107 - Radio Deejay.mp3')
              e legacy volo_DDMM_ (es. 'volo_0701_...').
    """
    m = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'volo_(\d{2})(\d{2})_', filename)
    if m:
        dd, mm = m.group(1), m.group(2)
        return f"2016-{mm}-{dd}"
    return None


def decode_audio_ffmpeg(path: Path) -> np.ndarray:
    """Decodifica MP3 → float32 mono 16kHz via ffmpeg CLI (evita PyAV/WDAC)."""
    cmd = [
        "ffmpeg", "-i", str(path),
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr.decode(errors='replace')[-300:]}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    return audio


def trascrivi(path: Path, model) -> tuple[str, float]:
    """Restituisce (testo_completo, durata_secondi)."""
    audio = decode_audio_ffmpeg(path)
    durata = len(audio) / SAMPLE_RATE
    segments, _ = model.transcribe(
        audio, language="it", beam_size=5,
        vad_filter=True,
        initial_prompt="Il Volo del Mattino, Fabio Volo, Radio DeeJay, film, libro, canzone.",
    )
    testo = " ".join(s.text.strip() for s in segments)
    return testo, round(durata, 2)


CHUNK_SIZE = 6000  # caratteri per chunk (~1500 token input, lascia spazio al prompt)
CHUNK_SLEEP = 13   # secondi tra chunk (max ~4-5 chunk/min entro 6000 TPM)


def _groq_chunk(testo: str) -> list[dict]:
    """Singola chiamata LLM (Groq o Cerebras, sceglie llm_multi) per un chunk di testo."""
    provider = llm_multi.provider_disponibile()
    if provider is None:
        raise RuntimeError("budget Groq E Cerebras esauriti per oggi")
    client, model = llm_multi.client_e_modello(provider)
    prompt = PROMPT_TPL.format(testo=testo)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=600,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    if resp.usage:
        llm_multi.registra_uso(provider, resp.usage.total_tokens)
    raw = resp.choices[0].message.content.strip()
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return []
    return parsed if isinstance(parsed, list) else []


def estrai_riferimenti(testo: str) -> list[dict]:
    """Divide il testo in chunk e aggrega i riferimenti trovati (Groq+Cerebras).
    Un chunk fallito (JSON malformato dal modello, errore di rete, ecc.) viene
    riprovato UNA volta prima di essere scartato, per non perdere dati per un
    singolo errore transitorio (visto in pratica: "Expecting value..." su JSON
    troncato dal modello).

    Ogni riferimento restituito porta con se' il testo/offset del chunk da cui
    proviene (_chunk_testo, _start_frac, _end_frac) cosi' merge_riferimenti puo'
    salvare il contesto reale invece di un blob condiviso identico per tutte le
    voci dell'episodio. Le voci il cui titolo/autore non compare nel testo del
    chunk (probabile allucinazione del modello) vengono scartate qui."""
    chunks = [testo[i:i + CHUNK_SIZE] for i in range(0, len(testo), CHUNK_SIZE)]
    n_char = max(len(testo), 1)
    print(f"    Invio {len(chunks)} chunk (Groq+Cerebras+Gemini)…")
    tutti: list[dict] = []
    scartati_non_ancorati = 0
    for idx, chunk in enumerate(chunks):
        if llm_multi.provider_disponibile() is None:
            print(f"      STOP: budget Groq E Cerebras esauriti per oggi, "
                  f"{len(chunks) - idx} chunk rimasti verranno riprovati domani")
            break
        risultati = None
        for tentativo in range(2):
            try:
                risultati = _groq_chunk(chunk)
                break
            except Exception as e:
                if tentativo == 0:
                    print(f"      chunk {idx+1}/{len(chunks)} ERRORE ({e}), riprovo una volta...")
                    time.sleep(5)
                else:
                    print(f"      chunk {idx+1}/{len(chunks)} ERRORE anche al secondo tentativo: {e}")
        if risultati is not None:
            char_start = idx * CHUNK_SIZE
            char_end = min(len(testo), char_start + len(chunk))
            ancorati = []
            for r in risultati:
                if not _titolo_e_ancorato_al_testo(r.get("titolo", ""), r.get("autore", ""), chunk):
                    scartati_non_ancorati += 1
                    continue
                r["_chunk_testo"] = chunk
                r["_start_frac"] = char_start / n_char
                r["_end_frac"] = char_end / n_char
                ancorati.append(r)
            print(f"      chunk {idx+1}/{len(chunks)}: {len(ancorati)} riferimenti "
                  f"({len(risultati) - len(ancorati)} scartati, non ancorati al testo)")
            tutti.extend(ancorati)
        if idx < len(chunks) - 1:
            time.sleep(CHUNK_SLEEP)
    if scartati_non_ancorati:
        print(f"    Totale scartati per allucinazione probabile: {scartati_non_ancorati}")
    return tutti


TITOLO_SIMILARITY_SOGLIA = 0.85  # sopra questa soglia (difflib) un titolo e' considerato doppione,
# stessa soglia/logica di trascrivi_locale_episodi.py::_titolo_e_doppione (duplicata qui invece di
# importata per evitare un import circolare: trascrivi_locale_episodi.py importa gia' da qui)


def _normalizza_titolo(titolo: str) -> str:
    return re.sub(r"[^\w\s]", "", titolo.lower()).strip()


def _titolo_e_doppione(titolo: str, titoli_esistenti: list[str]) -> bool:
    norm = _normalizza_titolo(titolo)
    if not norm:
        return False
    for esistente in titoli_esistenti:
        if difflib.SequenceMatcher(None, norm, _normalizza_titolo(esistente)).ratio() >= TITOLO_SIMILARITY_SOGLIA:
            return True
    return False


VERBI_CONVERSAZIONE = {
    "e", "sono", "ha", "hanno", "fa", "fanno", "dice", "dicono",
    "vuole", "vogliono", "andiamo", "partite",
}
# Stessa lista/logica di trascrivi_locale_episodi.py::VERBI_CONVERSAZIONE (duplicata qui
# per lo stesso motivo di TITOLO_SIMILARITY_SOGLIA sopra: evitare import circolare).


def _titolo_e_frase_di_conversazione(titolo: str) -> bool:
    """Stessa logica di trascrivi_locale_episodi.py::_titolo_e_frase_di_conversazione —
    un titolo vero e' un nome/frase breve, non una domanda o una frase con verbi
    coniugati: se lo sembra, e' quasi certamente chiacchiera trascritta, non un'opera."""
    if "?" in titolo:
        return True
    parole = _normalizza_titolo(titolo).split()
    if len(parole) > 10:
        return True
    return sum(1 for p in parole if p in VERBI_CONVERSAZIONE) >= 2


def _titolo_e_ancorato_al_testo(titolo: str, autore: str, testo: str) -> bool:
    """Verifica che titolo o autore compaiano davvero nel testo di origine.

    Il modello a volte 'trova' un riferimento che non e' nel testo (allucinazione).
    Se ne' il titolo ne' l'autore compaiono (anche parzialmente) nel chunk da cui
    e' stato estratto, scartiamo la voce invece di fidarci ciecamente.

    Aggiunto 2026-07-21: se titolo e autore normalizzati sono UGUALI (uguaglianza
    esatta, non solo "contenuto in" - un titolo reale puo' legittimamente includere
    il nome dell'autore, es. "La Dieta del Dottor Mozzi"/"Dottor Mozzi", verificato
    con test reale che quel caso NON va scartato), il modello ha nominato SOLO una
    persona, non un'opera+creatore distinti (es. "Bill Gates"/"Bill Gates") -
    scartiamo anche se entrambi sono tecnicamente ancorati al testo, stesso bug
    trovato in trascrivi_locale_episodi.py::_riferimento_valido lo stesso giorno.

    Aggiunto 2026-07-23 (bug reale trovato: "Baracco Mava" - trascrizione deformata
    di Barack Obama - salvato come "libro" con autore VUOTO, e un post Facebook
    salvato come "libro" perche' titolo/autore comparivano nel testo circostante):
    ora l'autore e' SEMPRE obbligatorio (mai un'opera senza creatore nominato, stesso
    principio gia' validato in trascrivi_locale_episodi.py::_riferimento_valido) e un
    titolo con la forma di una frase di conversazione (verbi coniugati, punto
    interrogativo, troppo lungo) viene scartato anche se le sue parole compaiono
    nel testo — l'ancoraggio da solo non basta a distinguere un titolo vero da
    chiacchiera trascritta letteralmente."""
    t_norm = _normalizza_titolo(titolo)
    a_norm = _normalizza_titolo(autore)
    # Stesso placeholder trovato in trascrivi_locale_episodi.py::AUTORE_PLACEHOLDER_SOTTOSTRINGHE:
    # controllo per SOTTOSTRINGA (non uguaglianza esatta) - trovato 2026-07-22 nel run
    # notturno reale che varianti come "Artista non specificato"/"Articolo non specificato
    # nel testo" non sono mai uguali esatte a una voce del set, quindi lo bypassavano.
    if any(s in a_norm for s in ("unknown", "sconosciut", "ignot", "non specificat", "n a", "varie", "vario")):
        autore = ""
        a_norm = ""
    if not a_norm:
        return False
    if t_norm and a_norm and t_norm == a_norm:
        return False
    if _titolo_e_frase_di_conversazione(titolo):
        return False
    testo_norm = _normalizza_titolo(testo)
    for candidato in (titolo, autore):
        norm = _normalizza_titolo(candidato)
        if not norm:
            continue
        parole = [p for p in norm.split() if len(p) >= 4]
        if not parole:
            if norm in testo_norm:
                return True
            continue
        if any(p in testo_norm for p in parole):
            return True
    return False


def merge_riferimenti(data_str: str, nuovi: list[dict], testo: str, durata: float) -> None:
    """Aggiunge i nuovi riferimenti al file JSON, senza sovrascrivere campi già compilati.
    Deduplica per CONTENUTO (titolo simile nella stessa categoria), non solo per id: rilanciare
    l'estrazione su un episodio gia' fatto non deve piu' accumulare doppioni identici.

    Ogni voce usa il proprio testo/start/end di chunk (allegati da estrai_riferimenti come
    _chunk_testo/_start_frac/_end_frac) invece del blob `testo`/`durata` passati come argomento —
    quei due restano solo come fallback per voci senza offset (retrocompatibilita')."""
    dest = RIF_DIR / f"{data_str}.json"
    esistenti: dict[str, dict] = {}
    if dest.exists():
        for r in json.loads(dest.read_text(encoding="utf-8")):
            esistenti[r["id"]] = r

    # Conta per generare id progressivi per questa data+categoria
    contatori: dict[str, int] = {}
    titoli_per_categoria: dict[str, list[str]] = {}
    for eid, r in esistenti.items():
        m = re.match(r'.+-(film|libro|musica)-clip-(\d+)', eid)
        if m:
            cat = m.group(1)
            n = int(m.group(2))
            contatori[cat] = max(contatori.get(cat, -1), n)
        if r.get("titolo"):
            titoli_per_categoria.setdefault(r.get("categoria", ""), []).append(r["titolo"])

    aggiunti = 0
    doppioni_scartati = 0
    for ref in nuovi:
        cat = ref.get("categoria", "").lower()
        if cat not in ("film", "libro", "musica"):
            continue
        titolo = ref.get("titolo", "").strip()
        if not titolo:
            continue  # nessun titolo reale identificato, scarta (evita voci vuote)
        if _titolo_e_doppione(titolo, titoli_per_categoria.get(cat, [])):
            doppioni_scartati += 1
            continue

        n = contatori.get(cat, -1) + 1
        contatori[cat] = n
        rid = f"{data_str}-{cat}-clip-{n:04d}"

        if rid in esistenti:
            continue  # già presente, rispetta il merge

        sottocat_valide = {
            "libro": {"romanzo", "poesia", "saggio", "citazione", "lettura_volo", ""},
            "film":  {"documentario", ""},
            "musica": {""},
        }
        sottocat = ref.get("sottocategoria", "").lower().strip()
        if sottocat not in sottocat_valide.get(cat, {""}):
            sottocat = ""
        ref_testo = ref.get("_chunk_testo") or testo
        ref_start = round(ref.get("_start_frac", 0.0) * durata, 2)
        ref_end = round(ref.get("_end_frac", 1.0) * durata, 2)
        esistenti[rid] = {
            "id": rid,
            "categoria": cat,
            "sottocategoria": sottocat,
            "titolo": titolo,
            "anno": ref.get("anno", ""),
            "autore": ref.get("autore", ""),
            "note": ref.get("note", ""),
            "testo": ref_testo,
            "start": ref_start,
            "end": ref_end,
            "episodio_data": data_str,
        }
        titoli_per_categoria.setdefault(cat, []).append(titolo)
        aggiunti += 1

    voci = sorted(esistenti.values(), key=lambda x: x["start"])
    RIF_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(voci, ensure_ascii=False, indent=2), encoding="utf-8")
    dettaglio = f", {doppioni_scartati} doppioni scartati" if doppioni_scartati else ""
    print(f"    -> {dest} ({aggiunti} nuovi{dettaglio}, {len(voci)} totali)")


def main() -> None:
    # Carica modello faster-whisper una sola volta
    print("Carico modello faster-whisper medium (CPU)…")
    from faster_whisper import WhisperModel  # import ritardato: fallisce solo se mancante
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    print("Modello pronto.\n")

    if len(sys.argv) > 1:
        clip_paths = [Path(p) for p in sys.argv[1:]]
    else:
        clip_paths = sorted(CLIP_DIR.glob("*.mp3"))

    if not clip_paths:
        print(f"Nessun MP3 trovato in {CLIP_DIR}")
        return

    print(f"Processo {len(clip_paths)} clip...\n")
    for mp3 in clip_paths:
        data_str = parse_data(mp3.name)
        if not data_str:
            print(f"[SKIP] {mp3.name} — non riesco a estrarre la data dal nome")
            continue

        print(f"[{data_str}] {mp3.name}")

        # Trascrizione
        try:
            testo, durata = trascrivi(mp3, model)
        except Exception as e:
            print(f"    ERRORE trascrizione: {e}")
            continue
        print(f"    {durata:.0f}s — {len(testo)} caratteri trascritti")

        if not testo.strip():
            print("    testo vuoto, salto")
            continue

        # Estrazione riferimenti
        refs = estrai_riferimenti(testo)
        print(f"    {len(refs)} riferimenti trovati")
        for r in refs:
            print(f"      [{r.get('categoria','?')}] {r.get('titolo','?')} ({r.get('autore','?')} {r.get('anno','')})")

        # Deduplicazione per (categoria, titolo) — Groq a volte ripete lo stesso
        seen_keys: set[tuple] = set()
        refs_uniq = []
        for r in refs:
            key = (r.get("categoria", "").lower(), r.get("titolo", "").lower().strip())
            if key not in seen_keys and key[1]:
                seen_keys.add(key)
                refs_uniq.append(r)
        if len(refs_uniq) < len(refs):
            print(f"    (deduplicati: {len(refs)} -> {len(refs_uniq)})")

        # Salvataggio
        merge_riferimenti(data_str, refs_uniq, testo, durata)

        time.sleep(0.5)  # throttle Groq

    print("\nFatto.")


if __name__ == "__main__":
    main()
