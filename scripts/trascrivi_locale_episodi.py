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
#   ~/hf_token.txt        token HuggingFace per la diarizzazione pyannote (gia' usato da sync_archive.py)
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

from sync_archive import transcribe, load_lines, HF_TOKEN_FILE  # noqa: E402
import genera_frammenti  # noqa: E402
from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402
import llm_multi  # noqa: E402

TRASCRIZIONI_DIR = ROOT / "data" / "trascrizioni"
FRAMMENTI_DIR = ROOT / "data" / "frammenti"

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
- Nel dubbio, ESCLUDI. Meglio pochi frammenti buoni che tanti irrilevanti.

FRAMMENTI:
{lista}

Restituisci un array JSON (vuoto [] se nessuno e' rilevante):
[
  {{"id": "...", "tipo": "citazione|lettura_volo|aneddoto|riflessione|riferimento_libro|riferimento_film|riferimento_musica", \
"titolo": "breve titolo del frammento (max 8 parole)", "tema": ["..."]}},
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
            if _titolo_e_doppione(titolo, titoli_episodio):
                doppioni_scartati += 1
                continue
            f["titolo"] = titolo
            f["tipo"] = tipo
            f["tema"] = r.get("tema", []) if isinstance(r.get("tema"), list) else []
            titoli_episodio.append(titolo)
            taggati += 1
        dettagli = []
        if doppioni_scartati:
            dettagli.append(f"{doppioni_scartati} doppioni scartati finora")
        if tipi_fuori_schema:
            dettagli.append(f"{tipi_fuori_schema} tipi fuori schema scartati finora")
        print(f"      classificazione batch {i // CLASSIFY_BATCH + 1}: {taggati} frammenti taggati"
              + (f" ({', '.join(dettagli)})" if dettagli else ""))
        if i + CLASSIFY_BATCH < len(da_classificare):
            time.sleep(CLASSIFY_SLEEP)


def parse_data(filename: str) -> str | None:
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
        cpu_affinity = list(range(args.threads))

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
        print(f"[{data_str}] {mp3.name}")

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
            print(f"  [{data_str}] completato.\n")
            continue

        # 3. classificazione automatica frammenti rilevanti
        try:
            frammenti_path = FRAMMENTI_DIR / f"{data_str}.json"
            frammenti = json.loads(frammenti_path.read_text(encoding="utf-8"))
            print(f"  classifico {len(frammenti)} frammenti (Groq+Cerebras)...")
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
                    print("  estraggo riferimenti culturali (Groq+Cerebras)...")
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

        print(f"  [{data_str}] completato.\n")

        # pausa di raffreddamento dopo il carico CPU di WhisperX, non dopo l'ultimo della lista
        if args.pausa > 0 and idx < len(mp3s) - 1:
            print(f"  raffreddamento CPU: pausa di {args.pausa}s prima del prossimo episodio...\n")
            time.sleep(args.pausa)

    print("Fatto.")


if __name__ == "__main__":
    main()
