# Trascrizione locale (WhisperX, CPU) di episodi 2012-2016 gia' presenti su disco,
# NON ancora caricati/archiviati (pipeline audio/archive.org e' CHIUSA, questo script
# non scarica né pubblica nulla: solo metadata testuale per la Card).
#
# Per ogni MP3 non ancora trascritto (manca data/trascrizioni/<data>.json):
#   1. WhisperX CPU (diarizzazione) sul file COSI' COM'E' su disco -> data/trascrizioni/<data>.json
#   2. genera_frammenti.genera() -> data/frammenti/<data>.json (turni di parola)
#   3. Classificazione automatica Groq dei frammenti rilevanti: assegna tipo/titolo/tema
#      SOLO se non gia' compilati a mano (merge idempotente, mai sovrascrive lavoro umano)
#   4. Estrazione riferimenti culturali (libri/film/citazioni) Groq sul testo intero
#      -> data/riferimenti/<data>.json (riusa la logica di trascrivi_e_estrai_clip.py)
#
# Uso:
#   python scripts/trascrivi_locale_episodi.py "D:\Docs\il_volo_del_mattino\Volo del mattino\audio\2016" [--da 20160120]
#
# Richiede (MAI committati in git):
#   ~/hf_token.txt        token HuggingFace per la diarizzazione pyannote (gia' usato da transcribe_utils.py)
#   GROQ_API_KEY oppure ~/API GROQ IA.txt

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from transcribe_utils import transcribe, load_lines, HF_TOKEN_FILE  # noqa: E402
import genera_frammenti  # noqa: E402
from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402
import llm_multi  # noqa: E402
from dati_root import dati_root  # noqa: E402

TRASCRIZIONI_DIR = dati_root(ROOT) / "trascrizioni"
FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"

CLASSIFY_SYSTEM = (
    "Sei un assistente che analizza trascrizioni del programma radiofonico italiano "
    "'Il Volo del Mattino' (Radio DeeJay), condotto da Fabio Volo. "
    "Rispondi SEMPRE e SOLO con un array JSON valido, nessun testo aggiuntivo."
)

CLASSIFY_PROMPT_TPL = """\
Di seguito una lista di frammenti (turni di parola) di una puntata, ciascuno con un id.
Individua SOLO quelli rilevanti (citazioni lette, riferimenti a libri/film/musica, \
letture ad alta voce, aneddoti/riflessioni DAVVERO significativi) — ignora chiacchiere/sigle/pubblicita'.

CRITERIO SEVERO per "aneddoto" e "riflessione" (le due categorie piu' abusate finora,
generano titoli generici e ripetitivi tipo "lavoro e impegno" — vanno tagliate molto di piu'):
- ESCLUDI qualsiasi scambio su faccende domestiche/oggetti banali (piegare mutande, sistemare posate, \
cassetti, forchette, telefono che non si trova, ecc.) anche se e' un botta-e-risposta vivace.
- ESCLUDI battute, scherzi, prese in giro tra conduttori senza un contenuto/messaggio riutilizzabile.
- Per "aneddoto": INCLUDI solo se racconta un EVENTO CONCRETO con una svolta narrativa riconoscibile \
(un inizio, uno sviluppo, una conclusione — qualcosa che e' davvero SUCCESSO), non un commento di \
passaggio buttato li' in chiacchiera. Una battuta isolata ("mancano 339 giorni al Natale") NON e' un aneddoto.
- Per "riflessione": INCLUDI solo se esprime un INSEGNAMENTO ESPLICITO E AUTONOMO — una frase che ha senso \
compiuto e resterebbe memorabile anche letta FUORI dal contesto della puntata. Un'osservazione generica \
su lavoro/famiglia/tempo buttata li' senza svilupparla NON e' una riflessione, anche se il tema e' "giusto".
- Per entrambe: se il frammento e' sotto le ~25-30 parole E non contiene una frase autonoma e memorabile \
(non solo un tema pertinente), ESCLUDI — la lunghezza da sola non basta, ma un frammento troppo corto \
quasi mai contiene una svolta narrativa o un insegnamento completo.
- INCLUDI sempre invece: citazioni/letture ad alta voce, riferimenti identificabili a libro/film/canzone/articolo.
- Per riferimento_libro/film/musica: il titolo/autore DEVE essere esplicitamente presente o \
chiaramente deducibile dal testo del frammento stesso — MAI completarlo con conoscenza \
esterna tua. Se il testo menziona solo un argomento generico (es. "un disco", "un libro \
che ho letto") senza nominare title/autore reali, NON classificarlo come riferimento — usalo \
come aneddoto/riflessione se rispetta quei criteri, altrimenti escludilo.
- ATTENZIONE, errori concreti gia' visti (categoria "riferimento_libro" abusata quasi quanto \
aneddoto/riflessione — la trovi facilmente citata in un nome proprio o in una parola qualunque \
e la scambi per un titolo): un riferimento_libro/film/musica e' SOLO un'opera pubblicata reale \
(romanzo, film, canzone con titolo e autore identificabili), MAI un giocattolo/prodotto/marchio \
citato per nome (es. "il triciclo di legno", "l'hoverboard"), MAI la discussione di un nome \
proprio/soprannome di una persona (es. "Erika con la H invece che con la K"), MAI un fatto/aneddoto \
su una persona famosa raccontato senza citare un'opera sua specifica (es. "cosa mangia la Regina \
Elisabetta" NON e' un riferimento_libro solo perche' si parla di un libro/articolo che lo racconta, \
a meno che il TITOLO di quel libro/articolo sia nominato esplicitamente). Nel dubbio se sia \
un'opera vera o solo un nome/oggetto/fatto citato di passaggio, classifica come aneddoto/riflessione \
o escludi, MAI come riferimento_libro/film/musica "a scatola chiusa".
- Per riferimento_libro/film/musica il campo "autore" e' OBBLIGATORIO e DEVE essere una persona/gruppo \
DIVERSO dal "titolo" (chi ha scritto/diretto/cantato l'opera, non l'opera stessa). Se riesci a nominare \
SOLO una persona ma NON un titolo di opera distinto, quello NON e' un riferimento_libro/film/musica: \
e' solo una persona citata, classifica come aneddoto/riflessione o escludi.
- ATTENZIONE, altro errore concreto gia' visto: testo con rima, ritmo o struttura da ritornello/strofa \
(versi brevi che rimano tra loro, frasi ripetute piu' volte come un refrain, es. "e' il tuo controllo \
e' il tuo controllo") e' quasi sempre una CANZONE recitata/cantata, MAI classificarlo come \
riferimento_libro anche se il tema sembra "letterario" — usa riferimento_musica, o se non riesci a \
identificare un titolo/artista reale, escludilo.
- Nel dubbio, ESCLUDI. Meglio pochi frammenti buoni che tanti irrilevanti.

ESEMPI REALI (da errori gia' fatti in passato — studiali prima di rispondere):
- BUONO (aneddoto): "ragazzi volevo darvi belle notizie le ho chiesto di sposarci ha detto si \
Alessandro da Siracusa ha fatto quella roba la mi vuoi sposare?" -> evento concreto con inizio/svolta/esito, \
INCLUDI come aneddoto.
- CATTIVO (NON classificare cosi'): "buongiorno a tutti ragazzi, sono le 9 e 7 minuti, oggi Igor Sibaldi \
e' qui con noi, buongiorno Igor" -> nessun libro nominato, e' solo la presentazione di un ospite: NON e' \
riferimento_libro, ESCLUDI del tutto (non ha nemmeno un insegnamento autonomo per riflessione).
- CATTIVO (NON classificare cosi'): "abbiamo gia' perso quei pochi ascoltatori" -> una battuta isolata, \
nessuna svolta narrativa: NON e' un aneddoto, ESCLUDI.
- CATTIVO (NON classificare cosi', trovato 2026-07-21): "era quello di Bill Gates tutta gente ricca \
tutta gente ricca tecnica e ricchezza" -> Bill Gates e' una persona citata di sfuggita, nessuna opera \
sua nominata (nessun libro/film specifico): NON e' riferimento_libro, ESCLUDI.
- CATTIVO (NON classificare cosi', trovato 2026-07-21): "le mandorle per non avere il raffreddore?" \
-> domanda di chiacchiera generica, nessun libro/opera: NON e' riferimento_libro, ESCLUDI.

FRAMMENTI:
{lista}

Restituisci un array JSON (vuoto [] se nessuno e' rilevante):
[
  {{"id": "...", "tipo": "citazione|lettura_volo|aneddoto|riflessione|riferimento_libro|riferimento_film|riferimento_musica", \
"titolo": "breve titolo del frammento (max 8 parole)", \
"autore": "OBBLIGATORIO solo per riferimento_libro/film/musica: chi ha scritto/diretto/cantato l'opera, \
vuoto '' per gli altri tipi", "tema": ["..."]}},
  ...
]
Regole:
- "tipo" DEVE essere SEMPRE uno di quei 7 valori esatti, MAI inventarne altri (es. niente "riferimento_app", \
niente "riferimento_cancione" — un riferimento a una canzone e' SEMPRE "riferimento_musica").
- "tema": 1-3 parole chiave in minuscolo (es. "amore", "paura", "genitori")
- Non includere frammenti generici senza contenuto specifico
"""

TIPI_VALIDI = {
    "citazione", "lettura_volo", "aneddoto", "riflessione",
    "riferimento_libro", "riferimento_film", "riferimento_musica",
}

# Guardarraili deterministici, trovati necessari il 2026-07-20: le regole del prompt
# sopra (titolo ancorato al testo, minimo ~25-30 parole per aneddoto/riflessione) sono
# gia' scritte ma un modello piccolo/gratuito (Groq 8B, Cerebras, Gemini flash-lite) non
# le rispetta in modo affidabile — misurato: 9,6% dei riferimento_* storici NON ancorati,
# 25,8% di aneddoto/riflessione sotto le 25 parole, nonostante il prompt lo vietasse gia'.
# `verifica_frammenti.py` (un altro LLM che giudica) non li aveva presi (0 segnalati) —
# un modello debole che giudica un altro modello debole non e' una rete affidabile.
# Qui la regola diventa CODICE, non piu' una richiesta che il modello puo' ignorare.
RIF_TIPI = {"riferimento_libro", "riferimento_film", "riferimento_musica"}
NARR_TIPI = {"aneddoto", "riflessione"}
MIN_PAROLE_NARRATIVO = 25


def _normalizza_per_ancoraggio(s: str) -> str:
    return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()


def _titolo_ancorato(titolo: str, testo: str) -> bool:
    """Stessa logica di controlla_ancoraggio_riferimenti.py::ancorato() — almeno una
    parola di 4+ lettere del titolo proposto deve comparire nel testo del frammento."""
    testo_norm = _normalizza_per_ancoraggio(testo)
    norm = _normalizza_per_ancoraggio(titolo)
    if not norm:
        return False
    parole = [p for p in norm.split() if len(p) >= 4]
    if not parole:
        return norm in testo_norm
    return any(p in testo_norm for p in parole)


# Guardarraili aggiunti il 2026-07-21: campionando 40 riferimento_* REALI a caso (non scelti
# a mano), il 55% erano falsi positivi in cui il "titolo" e' preso quasi letteralmente da
# chiacchiera normale (una persona citata di sfuggita, un argomento generico) - l'ancoraggio
# da solo non li blocca perche' le parole del "titolo" fanno parte dello stesso testo di
# chiacchiera. Riusa lo schema titolo+autore gia' presente in trascrivi_e_estrai_clip.py
# invece di inventarne uno nuovo.
VERBI_CONVERSAZIONE = {
    "e", "sono", "ha", "hanno", "fa", "fanno", "dice", "dicono",
    "vuole", "vogliono", "andiamo", "partite",
}


def _titolo_e_frase_di_conversazione(titolo: str) -> bool:
    """Un titolo vero e' un nome/frase breve, non una domanda o una frase con verbi
    coniugati: se lo sembra, e' quasi certamente chiacchiera trascritta, non un'opera."""
    if "?" in titolo:
        return True
    parole = _normalizza_per_ancoraggio(titolo).split()
    if len(parole) > 10:
        return True
    return sum(1 for p in parole if p in VERBI_CONVERSAZIONE) >= 2


def _riferimento_valido(titolo: str, autore: str, testo: str) -> bool:
    """Guardrail completo per riferimento_libro/film/musica: titolo e autore devono
    essere entrambi presenti, distinti l'uno dall'altro (altrimenti e' solo una persona
    citata, non un'opera+creatore), ancorati al testo, e il titolo non deve avere la
    forma di una frase di conversazione."""
    if not autore or not titolo:
        return False
    t_norm = _normalizza_per_ancoraggio(titolo)
    a_norm = _normalizza_per_ancoraggio(autore)
    if not t_norm or not a_norm:
        return False
    if t_norm == a_norm:
        return False
    if _titolo_e_frase_di_conversazione(titolo):
        return False
    # Basta che UNO dei due sia ancorato: l'autore/artista spesso non e' nominato
    # nel frammento cantato/letto stesso (es. testo di canzone senza dire chi la canta) -
    # richiederlo su entrambi scarterebbe classificazioni corrette (verificato con test reali).
    if not (_titolo_ancorato(titolo, testo) or _titolo_ancorato(autore, testo)):
        return False
    return True

CLASSIFY_BATCH = 12
CLASSIFY_SLEEP = 13
CLASSIFY_MIN_PAROLE = 6  # sotto questa soglia il frammento e' quasi sempre chiacchiera/sigla:
# scartarlo PRIMA di chiamare Groq risparmia token/richieste senza perdere niente di utile
# (il prompt gia' chiede di escluderli, ma cosi' non li paghiamo nemmeno).
TITOLO_SIMILARITY_SOGLIA = 0.85  # sopra questa soglia (difflib) un titolo e' considerato doppione


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


def classifica_frammenti(frammenti: list[dict]) -> None:
    """Assegna tipo/titolo/tema ai frammenti rilevanti, mutando la lista in place.
    Non tocca frammenti che hanno gia' un titolo assegnato a mano. Scarta i frammenti il cui
    titolo proposto e' troppo simile a uno gia' assegnato nello STESSO episodio (evita doppioni
    generici tipo "lavoro e impegno" ripetuto piu' volte)."""
    da_classificare = [
        f for f in frammenti
        if not f["titolo"] and len(f["testo"].split()) >= CLASSIFY_MIN_PAROLE
    ]
    scartati = sum(1 for f in frammenti if not f["titolo"] and len(f["testo"].split()) < CLASSIFY_MIN_PAROLE)
    if scartati:
        print(f"      {scartati} frammenti troppo brevi (<{CLASSIFY_MIN_PAROLE} parole) scartati prima di Groq")

    titoli_episodio = [f["titolo"] for f in frammenti if f["titolo"]]
    doppioni_scartati = 0
    tipi_fuori_schema = 0
    non_ancorati = 0
    troppo_corti = 0

    for i in range(0, len(da_classificare), CLASSIFY_BATCH):
        provider = llm_multi.provider_disponibile()
        if provider is None:
            print("      STOP classificazione: budget Groq E Cerebras esauriti per oggi. Riprendera' domani.")
            break
        client, model = llm_multi.client_e_modello(provider)
        batch = da_classificare[i:i + CLASSIFY_BATCH]
        lista = "\n".join(f'[{f["id"]}] {f["testo"][:400]}' for f in batch)
        risultati = None
        for tentativo in range(2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": CLASSIFY_SYSTEM},
                        {"role": "user", "content": CLASSIFY_PROMPT_TPL.format(lista=lista)},
                    ],
                    max_tokens=800,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                if resp.usage:
                    llm_multi.registra_uso(provider, resp.usage.total_tokens)
                raw = resp.choices[0].message.content.strip()
                parsed = json.loads(raw)
                risultati = parsed if isinstance(parsed, list) else next(
                    (v for v in parsed.values() if isinstance(v, list)), []
                )
                break
            except Exception as e:
                if tentativo == 0:
                    print(f"      classificazione batch {i}: ERRORE ({e}), riprovo una volta...")
                    time.sleep(5)
                else:
                    print(f"      classificazione batch {i}: ERRORE anche al secondo tentativo: {e}")
        if risultati is None:
            continue

        by_id = {f["id"]: f for f in frammenti}
        taggati = 0
        for r in risultati:
            if not isinstance(r, dict):
                continue
            f = by_id.get(r.get("id"))
            if not f or f["titolo"]:
                continue
            tipo = r.get("tipo", "")
            if tipo not in TIPI_VALIDI:
                tipi_fuori_schema += 1
                print(f"      tipo fuori schema scartato: {tipo!r} (id {r.get('id')})")
                continue
            titolo = r.get("titolo", "")[:120]
            autore = r.get("autore", "")[:120]
            if tipo in RIF_TIPI and not _riferimento_valido(titolo, autore, f["testo"]):
                non_ancorati += 1
                continue
            if tipo in NARR_TIPI and len(f["testo"].split()) < MIN_PAROLE_NARRATIVO:
                troppo_corti += 1
                continue
            if _titolo_e_doppione(titolo, titoli_episodio):
                doppioni_scartati += 1
                continue
            f["titolo"] = titolo
            f["tipo"] = tipo
            if tipo in RIF_TIPI:
                f["autore"] = autore
            f["tema"] = r.get("tema", []) if isinstance(r.get("tema"), list) else []
            titoli_episodio.append(titolo)
            taggati += 1
        dettagli = []
        if doppioni_scartati:
            dettagli.append(f"{doppioni_scartati} doppioni scartati finora")
        if tipi_fuori_schema:
            dettagli.append(f"{tipi_fuori_schema} tipi fuori schema scartati finora")
        if non_ancorati:
            dettagli.append(f"{non_ancorati} riferimenti non ancorati scartati finora")
        if troppo_corti:
            dettagli.append(f"{troppo_corti} aneddoto/riflessione troppo corti scartati finora")
        print(f"      classificazione batch {i // CLASSIFY_BATCH + 1}: {taggati} frammenti taggati"
              + (f" ({', '.join(dettagli)})" if dettagli else ""))
        if i + CLASSIFY_BATCH < len(da_classificare):
            time.sleep(CLASSIFY_SLEEP)


def _archivia_mp3(mp3: Path) -> None:
    """Sposta l'mp3 appena completato in 'gia_trascritti/' dentro la stessa cartella:
    cosi' basta guardare la cartella per capire a colpo d'occhio cosa manca ancora,
    senza dover controllare data/frammenti/ o chiedere. Se la cartella di destinazione
    ha gia' un file con lo stesso nome (es. doppio lancio), non sovrascrive: lascia
    l'mp3 dov'e'."""
    dest_dir = mp3.parent / "gia_trascritti"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / mp3.name
    if dest.exists():
        print(f"  (gia' presente in gia_trascritti/, non sposto: {mp3.name})")
        return
    mp3.rename(dest)
    print(f"  mp3 spostato in gia_trascritti/{mp3.name}")


def parse_data(filename: str) -> str | None:
    # Priorita' al formato YYYY-MM-DD (sempre a inizio nome file, affidabile) —
    # alcuni filename hanno un secondo blocco di 8 cifre embedded piu' avanti
    # (es. "2014-05-06_reloaded_21140506_volo.mp3": "21140506" e' un refuso nel
    # nome originale, NON e' 2014-05-06) che il vecchio regex prendeva per primo.
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cartella", help="cartella con i file Audio YYYYMMDD*.mp3")
    parser.add_argument("--da", default=None, help="data minima YYYYMMDD (incluso)")
    parser.add_argument("--a", default=None, help="data massima YYYYMMDD (incluso) — con --da, "
                         "permette di dividere il lavoro tra piu' macchine su range non sovrapposti")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--gpu", action="store_true", help="usa CUDA (device=cuda, compute_type=float16, batch_size=16) invece di CPU int8")
    parser.add_argument("--threads", type=int, default=4,
                         help="thread CPU usati da torch (default 4, meta' di un i5-1135G7 a 8 thread, per non saturare/scaldare troppo il chip)")
    parser.add_argument("--pausa", type=int, default=120,
                         help="secondi di pausa tra un episodio e l'altro per far raffreddare la CPU (default 120, 0 per disattivare)")
    parser.add_argument("--skip-classify", action="store_true",
                         help="ferma la pipeline dopo aver generato i frammenti grezzi (WhisperX + genera_frammenti), "
                              "NESSUNA chiamata Groq/Cerebras — per macchine secondarie (es. laptop) che lavorano in "
                              "parallelo al mini PC: il budget LLM e' condiviso per account, non per macchina, quindi "
                              "solo UNA macchina deve classificare (vedi riclassifica_frammenti.py per farlo centralmente "
                              "sui frammenti sincronizzati da piu' fonti)")
    args = parser.parse_args()

    if args.gpu:
        device, compute_type, batch_size, threads, cpu_affinity = "cuda", "float16", 16, None, None
    else:
        device, compute_type, batch_size, threads = "cpu", "int8", 8, args.threads
        # garanzia a livello di sistema operativo: --threads da solo non basta
        # (CTranslate2/OpenMP possono comunque usare piu' core durante l'ASR)
        # *2: un thread per core fisico distinto, non le coppie SMT (0,1 = stesso core fisico
        # su CPU con hyperthreading/SMT interleaved) - evita di concentrare il calore su meta'
        # dei core fisici e la contesa SMT inutile su un carico CPU-bound come questo
        cpu_affinity = [i * 2 for i in range(args.threads)]

    cartella = Path(args.cartella)
    mp3s = sorted(cartella.glob("*.mp3"))
    if args.da:
        mp3s = [p for p in mp3s if (parse_data(p.name) or "").replace("-", "") >= args.da]
    if args.a:
        mp3s = [p for p in mp3s if (parse_data(p.name) or "").replace("-", "") <= args.a]

    # esclude le date gia' completate PRIMA di applicare --limit: altrimenti
    # --limit N rischia di selezionare episodi vecchi (gia' trascritti) invece
    # di N episodi davvero nuovi, sprecando CPU e budget Groq/Cerebras.
    def _gia_fatto(mp3: Path) -> bool:
        data_str = parse_data(mp3.name)
        if not data_str:
            return False
        return (TRASCRIZIONI_DIR / f"{data_str}.json").exists() or (FRAMMENTI_DIR / f"{data_str}.json").exists()

    mp3s = [p for p in mp3s if not _gia_fatto(p)]

    if args.limit:
        mp3s = mp3s[:args.limit]

    if not mp3s:
        print(f"Nessun episodio nuovo da processare in {cartella} (tutti gia' trascritti nel range dato)")
        return

    hf_token = load_lines(HF_TOKEN_FILE)

    print(f"Processo {len(mp3s)} episodi da {cartella}...\n")
    for idx, mp3 in enumerate(mp3s):
        data_str = parse_data(mp3.name)
        if not data_str:
            print(f"[SKIP] {mp3.name} — data non riconosciuta nel nome")
            continue

        dest_trascr = TRASCRIZIONI_DIR / f"{data_str}.json"
        dest_frammenti = FRAMMENTI_DIR / f"{data_str}.json"
        print(f"[{idx + 1}/{len(mp3s)}] [{data_str}] {mp3.name}")

        print("  trascrivo con WhisperX (puo' richiedere piu' di un'ora su CPU)...")
        try:
            json_path = transcribe(mp3, hf_token, device=device, compute_type=compute_type, batch_size=batch_size, threads=threads, cpu_affinity=cpu_affinity)
        except Exception as e:
            print(f"  ERRORE trascrizione: {e}")
            continue
        TRASCRIZIONI_DIR.mkdir(parents=True, exist_ok=True)
        dest_trascr.write_bytes(json_path.read_bytes())

        # 2. frammenti (turni di parola)
        genera_frammenti.genera(data_str)

        if args.skip_classify:
            print("  --skip-classify: nessuna chiamata Groq/Cerebras qui (budget condiviso per account, "
                  "non per macchina). Frammenti grezzi pronti per riclassifica_frammenti.py centrale.")
            _archivia_mp3(mp3)
            print(f"  [{data_str}] completato.\n")
            continue

        # 3. classificazione automatica frammenti rilevanti
        try:
            frammenti_path = FRAMMENTI_DIR / f"{data_str}.json"
            frammenti = json.loads(frammenti_path.read_text(encoding="utf-8"))
            print(f"  classifico {len(frammenti)} frammenti (Groq+Cerebras+Gemini)...")
            classifica_frammenti(frammenti)
            frammenti_path.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  ERRORE classificazione frammenti: {e} (continuo con il resto)")

        # 4. riferimenti culturali (libri/film/citazioni) sul testo intero
        if not dest_trascr.exists():
            print("  JSON grezzo gia' pulito da un run precedente, salto riferimenti "
                  "(presumibilmente gia' estratti allora)")
        else:
            try:
                trascrizione = json.loads(dest_trascr.read_text(encoding="utf-8"))
                testo_intero = " ".join(s.get("text", "").strip() for s in trascrizione.get("segments", []))
                durata = trascrizione["segments"][-1]["end"] if trascrizione.get("segments") else 0.0
                if testo_intero.strip() and llm_multi.provider_disponibile() is None:
                    print("  SALTO riferimenti culturali: budget Groq E Cerebras esauriti per oggi.")
                elif testo_intero.strip():
                    print("  estraggo riferimenti culturali (Groq+Cerebras+Gemini)...")
                    refs = estrai_riferimenti(testo_intero)
                    merge_riferimenti(data_str, refs, testo_intero[:2000], durata)
            except Exception as e:
                print(f"  ERRORE estrazione riferimenti: {e} (continuo con il prossimo episodio)")

        # pulizia: il mini PC non deve accumulare i JSON grezzi WhisperX (~900KB/episodio).
        # Cancellato SOLO se i frammenti (il derivato compatto, sincronizzato via Syncthing)
        # sono stati generati con successo — mai se genera_frammenti e' fallito prima. Non si
        # arriva mai qui con --skip-classify (continue sopra), quindi il grezzo resta disponibile
        # sulla macchina secondaria finche' non fa anche lei classificazione/riferimenti in futuro.
        if dest_trascr.exists() and dest_frammenti.exists():
            dest_trascr.unlink()
            print("  JSON grezzo cancellato dal mini PC (frammenti gia' al sicuro)")

        _archivia_mp3(mp3)
        print(f"  [{data_str}] completato.\n")

        # pausa di raffreddamento dopo il carico CPU di WhisperX, non dopo l'ultimo della lista
        if args.pausa > 0 and idx < len(mp3s) - 1:
            print(f"  raffreddamento CPU: pausa di {args.pausa}s prima del prossimo episodio...\n")
            time.sleep(args.pausa)

    print("Fatto.")


if __name__ == "__main__":
    main()
