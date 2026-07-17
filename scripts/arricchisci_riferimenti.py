# Arricchisce i riferimenti estratti da genera_riferimenti.py usando
# Groq (gratuito, nessuna carta richiesta, funziona in Italia/EU).
# Per ogni voce con titolo vuoto, chiede al modello di identificare
# il film, libro o canzone dal testo di contesto della trascrizione.
#
# Input:  data/riferimenti/<data>.json  (output di genera_riferimenti.py)
# Output: stesso file aggiornato con titolo/anno/autore/note compilati
#
# Variabile d'ambiente richiesta: GROQ_API_KEY
# Ottieni la chiave gratis su: https://console.groq.com (registrati con Google)
#
# Uso: python scripts/arricchisci_riferimenti.py [data1 data2 ...]
#      senza argomenti processa tutti i file in data/riferimenti/.

import json
import os
import re
import sys
import time
from pathlib import Path

from groq import Groq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RIFERIMENTI_DIR = dati_root(ROOT) / "riferimenti"

CATEGORIA_LABEL = {
    "film": "film",
    "libro": "libro",
    "musica": "canzone o brano musicale",
}

SYSTEM = (
    "Sei un assistente che analizza trascrizioni del programma radiofonico italiano "
    "'Il Volo del Mattino' (Radio DeeJay), condotto da Fabio Volo. "
    "Il tuo compito è identificare riferimenti culturali specifici nel testo. "
    "Rispondi SEMPRE e SOLO con un oggetto JSON valido, nessun testo aggiuntivo."
)

PROMPT_TEMPLATE = """\
Nel seguente estratto Fabio Volo parla di un/una {cat_label}.

ESTRATTO:
\"\"\"{testo}\"\"\"

Se nel testo è menzionato o chiaramente identificabile un {cat_label} specifico, rispondi:
{{"trovato": true, "titolo": "...", "anno": "...", "autore": "...", "note": "..."}}

- titolo: il titolo (in italiano se esiste, altrimenti originale)
- anno: anno di uscita/pubblicazione (stringa vuota se non ricavabile)
- autore: regista / scrittore / artista (stringa vuota se non ricavabile)
- note: frase breve max 12 parole su perché Fabio ne parla

Se il testo è troppo vago o non identifica un {cat_label} specifico:
{{"trovato": false, "titolo": "", "anno": "", "autore": "", "note": "non identificato"}}

IMPORTANTE: rispondi "trovato": true SOLO se il titolo (o un riferimento inequivocabile
ad esso, es. una citazione riconoscibile) compare esplicitamente nell'ESTRATTO. Non
usare conoscenza esterna per indovinare di cosa potrebbe parlare Fabio: cibi, nomi di
persone, marchi o argomenti generici NON sono film/libri/canzoni. Nel dubbio, "trovato": false.
"""


def _normalizza(s):
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _ancorato_al_testo(titolo, autore, testo):
    """Scarta risultati del modello il cui titolo/autore non compare nel testo
    di origine (probabile allucinazione, es. cibo o persona scambiati per opera)."""
    testo_norm = _normalizza(testo)
    for candidato in (titolo, autore):
        norm = _normalizza(candidato)
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


def chiedi_groq(client, voce):
    """Chiama Groq e restituisce i campi arricchiti, o None in caso di errore."""
    cat = voce.get("categoria", "film")
    testo = voce.get("testo", "")
    prompt = PROMPT_TEMPLATE.format(
        cat_label=CATEGORIA_LABEL.get(cat, cat),
        testo=testo,
    )

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content.strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"\n    JSON non valido: {raw[:80]}")
        return None
    except Exception as e:
        print(f"\n    ERRORE API: {e}")
        return None


def arricchisci(client, data_str):
    """Processa un file di riferimenti e aggiorna le voci con titolo vuoto."""
    path = RIFERIMENTI_DIR / f"{data_str}.json"
    if not path.exists():
        print(f"  manca {path}, salto")
        return

    voci = json.loads(path.read_text(encoding="utf-8"))
    da_fare = [v for v in voci if not v.get("titolo") and v.get("note") != "non identificato"]

    print(f"  {data_str}: {len(voci)} voci, {len(da_fare)} da arricchire")
    if not da_fare:
        return

    modificato = False
    for v in da_fare:
        print(f"    [{v['categoria']}] {v['start']:.0f}s ... ", end="", flush=True)
        risultato = chiedi_groq(client, v)

        if risultato is None:
            print("errore, salto")
            continue

        if risultato.get("trovato") and not _ancorato_al_testo(
            risultato.get("titolo", ""), risultato.get("autore", ""), v.get("testo", "")
        ):
            v["note"] = "non identificato"
            print(f"scartato (non ancorato al testo: '{risultato.get('titolo', '')}')")
        elif risultato.get("trovato"):
            v["titolo"] = risultato.get("titolo", "")
            v["anno"]   = risultato.get("anno", "")
            v["autore"] = risultato.get("autore", "")
            v["note"]   = risultato.get("note", "")
            print(f"OK {v['titolo'] or '(titolo vuoto)'}")
        else:
            v["note"] = "non identificato"
            print("— non identificato")

        modificato = True
        time.sleep(0.3)

    if modificato:
        path.write_text(json.dumps(voci, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    salvato -> {path}")


def main():
    """Punto di ingresso."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("Errore: variabile GROQ_API_KEY non impostata.")
        print("Chiave gratis su: https://console.groq.com")
        sys.exit(1)

    client = Groq(api_key=api_key)

    date_list = sys.argv[1:] if len(sys.argv) > 1 else sorted(
        p.stem for p in RIFERIMENTI_DIR.glob("*.json")
    )

    if not date_list:
        print("Nessun file trovato in", RIFERIMENTI_DIR)
        return

    print(f"Arricchisco {len(date_list)} file con Groq (Llama 3.1)...")
    for d in date_list:
        arricchisci(client, d)


if __name__ == "__main__":
    main()
