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
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from sync_archive import transcribe, load_lines, HF_TOKEN_FILE  # noqa: E402
import genera_frammenti  # noqa: E402
from trascrivi_e_estrai_clip import (  # noqa: E402
    load_groq_key, estrai_riferimenti, merge_riferimenti,
)
from groq import Groq  # noqa: E402
import groq_budget  # noqa: E402

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

CRITERIO SEVERO per "aneddoto" e "riflessione" (le due categorie piu' abusate finora):
- ESCLUDI qualsiasi scambio su faccende domestiche/oggetti banali (piegare mutande, sistemare posate, \
cassetti, forchette, telefono che non si trova, ecc.) anche se e' un botta-e-risposta vivace.
- ESCLUDI battute, scherzi, prese in giro tra conduttori senza un contenuto/messaggio riutilizzabile.
- INCLUDI solo se: racconta un episodio di vita con un senso/morale chiaro, esprime un pensiero \
generalizzabile su un tema umano (amore, paura, lavoro, famiglia, tempo...), o cita/legge qualcosa \
di identificabile (libro, film, canzone, articolo).
- Nel dubbio, ESCLUDI. Meglio pochi frammenti buoni che tanti irrilevanti.

FRAMMENTI:
{lista}

Restituisci un array JSON (vuoto [] se nessuno e' rilevante):
[
  {{"id": "...", "tipo": "citazione|lettura_volo|aneddoto|riflessione|riferimento_libro|riferimento_film", \
"titolo": "breve titolo del frammento (max 8 parole)", "tema": ["..."]}},
  ...
]
Regole:
- "tema": 1-3 parole chiave in minuscolo (es. "amore", "paura", "genitori")
- Non includere frammenti generici senza contenuto specifico
"""

CLASSIFY_BATCH = 12
CLASSIFY_SLEEP = 13
CLASSIFY_MIN_PAROLE = 6  # sotto questa soglia il frammento e' quasi sempre chiacchiera/sigla:
# scartarlo PRIMA di chiamare Groq risparmia token/richieste senza perdere niente di utile
# (il prompt gia' chiede di escluderli, ma cosi' non li paghiamo nemmeno).


def classifica_frammenti(client: Groq, frammenti: list[dict]) -> None:
    """Assegna tipo/titolo/tema ai frammenti rilevanti, mutando la lista in place.
    Non tocca frammenti che hanno gia' un titolo assegnato a mano."""
    da_classificare = [
        f for f in frammenti
        if not f["titolo"] and len(f["testo"].split()) >= CLASSIFY_MIN_PAROLE
    ]
    scartati = sum(1 for f in frammenti if not f["titolo"] and len(f["testo"].split()) < CLASSIFY_MIN_PAROLE)
    if scartati:
        print(f"      {scartati} frammenti troppo brevi (<{CLASSIFY_MIN_PAROLE} parole) scartati prima di Groq")

    for i in range(0, len(da_classificare), CLASSIFY_BATCH):
        if not groq_budget.budget_disponibile():
            print(f"      STOP classificazione: budget Groq giornaliero esaurito "
                  f"({groq_budget.token_usati_oggi()} token usati oggi). Riprendera' domani.")
            break
        batch = da_classificare[i:i + CLASSIFY_BATCH]
        lista = "\n".join(f'[{f["id"]}] {f["testo"][:400]}' for f in batch)
        try:
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": CLASSIFY_PROMPT_TPL.format(lista=lista)},
                ],
                max_tokens=800,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            if resp.usage:
                groq_budget.registra_uso(resp.usage.total_tokens)
            raw = resp.choices[0].message.content.strip()
            parsed = json.loads(raw)
            risultati = parsed if isinstance(parsed, list) else next(
                (v for v in parsed.values() if isinstance(v, list)), []
            )
        except Exception as e:
            print(f"      classificazione batch {i}: ERRORE {e}")
            continue

        by_id = {f["id"]: f for f in frammenti}
        for r in risultati:
            if not isinstance(r, dict):
                continue
            f = by_id.get(r.get("id"))
            if not f or f["titolo"]:
                continue
            f["titolo"] = r.get("titolo", "")[:120]
            f["tipo"] = r.get("tipo", "")
            f["tema"] = r.get("tema", []) if isinstance(r.get("tema"), list) else []
        print(f"      classificazione batch {i // CLASSIFY_BATCH + 1}: {len(risultati)} frammenti taggati")
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
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--gpu", action="store_true", help="usa CUDA (device=cuda, compute_type=float16, batch_size=16) invece di CPU int8")
    parser.add_argument("--threads", type=int, default=4,
                         help="thread CPU usati da torch (default 4, meta' di un i5-1135G7 a 8 thread, per non saturare/scaldare troppo il chip)")
    parser.add_argument("--pausa", type=int, default=120,
                         help="secondi di pausa tra un episodio e l'altro per far raffreddare la CPU (default 120, 0 per disattivare)")
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
    if args.limit:
        mp3s = mp3s[:args.limit]

    if not mp3s:
        print(f"Nessun MP3 da processare in {cartella}")
        return

    hf_token = load_lines(HF_TOKEN_FILE)
    groq_client = Groq(api_key=load_groq_key())

    print(f"Processo {len(mp3s)} episodi da {cartella}...\n")
    for idx, mp3 in enumerate(mp3s):
        data_str = parse_data(mp3.name)
        if not data_str:
            print(f"[SKIP] {mp3.name} — data non riconosciuta nel nome")
            continue

        dest_trascr = TRASCRIZIONI_DIR / f"{data_str}.json"
        print(f"[{data_str}] {mp3.name}")

        appena_trascritto = False
        if dest_trascr.exists():
            print("  gia' trascritto, salto WhisperX")
        else:
            print("  trascrivo con WhisperX (puo' richiedere piu' di un'ora su CPU)...")
            try:
                json_path = transcribe(mp3, hf_token, device=device, compute_type=compute_type, batch_size=batch_size, threads=threads, cpu_affinity=cpu_affinity)
            except Exception as e:
                print(f"  ERRORE trascrizione: {e}")
                continue
            TRASCRIZIONI_DIR.mkdir(parents=True, exist_ok=True)
            dest_trascr.write_bytes(json_path.read_bytes())
            appena_trascritto = True

        # 2. frammenti (turni di parola)
        genera_frammenti.genera(data_str)

        # 3. classificazione automatica frammenti rilevanti
        try:
            frammenti_path = FRAMMENTI_DIR / f"{data_str}.json"
            frammenti = json.loads(frammenti_path.read_text(encoding="utf-8"))
            print(f"  classifico {len(frammenti)} frammenti con Groq...")
            classifica_frammenti(groq_client, frammenti)
            frammenti_path.write_text(json.dumps(frammenti, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  ERRORE classificazione frammenti: {e} (continuo con il resto)")

        # 4. riferimenti culturali (libri/film/citazioni) sul testo intero
        try:
            trascrizione = json.loads(dest_trascr.read_text(encoding="utf-8"))
            testo_intero = " ".join(s.get("text", "").strip() for s in trascrizione.get("segments", []))
            durata = trascrizione["segments"][-1]["end"] if trascrizione.get("segments") else 0.0
            if testo_intero.strip() and not groq_budget.budget_disponibile():
                print(f"  SALTO riferimenti culturali: budget Groq giornaliero esaurito "
                      f"({groq_budget.token_usati_oggi()} token usati oggi).")
            elif testo_intero.strip():
                print("  estraggo riferimenti culturali con Groq...")
                refs = estrai_riferimenti(groq_client, testo_intero)
                merge_riferimenti(data_str, refs, testo_intero[:2000], durata)
        except Exception as e:
            print(f"  ERRORE estrazione riferimenti: {e} (continuo con il prossimo episodio)")

        print(f"  [{data_str}] completato.\n")

        # pausa di raffreddamento SOLO dopo un carico CPU vero (WhisperX appena eseguito),
        # non dopo un episodio saltato perche' gia' fatto, e non dopo l'ultimo della lista
        if appena_trascritto and args.pausa > 0 and idx < len(mp3s) - 1:
            print(f"  raffreddamento CPU: pausa di {args.pausa}s prima del prossimo episodio...\n")
            time.sleep(args.pausa)

    print("Fatto.")


if __name__ == "__main__":
    main()
