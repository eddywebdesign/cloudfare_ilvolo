# Arricchisce i riferimenti estratti da genera_riferimenti.py usando
# Google Gemini Flash (gratuito: 1500 req/giorno, nessuna carta richiesta).
# Per ogni voce con titolo vuoto, chiede a Gemini di identificare il film,
# libro o canzone dal testo di contesto della trascrizione.
#
# Input:  data/riferimenti/<data>.json  (output di genera_riferimenti.py)
# Output: stesso file aggiornato con titolo/anno/autore/note compilati
#
# Variabile d'ambiente richiesta: GEMINI_API_KEY
# Ottieni la chiave gratis su: https://aistudio.google.com/apikey
#
# Uso: python scripts/arricchisci_riferimenti.py [data1 data2 ...]
#      senza argomenti processa tutti i file in data/riferimenti/.

import json
import os
import sys
import time
from pathlib import Path

from google import genai

ROOT = Path(__file__).resolve().parent.parent
RIFERIMENTI_DIR = ROOT / "data" / "riferimenti"

CATEGORIA_LABEL = {
    "film": "film",
    "libro": "libro",
    "musica": "canzone o brano musicale",
}

PROMPT_TEMPLATE = """\
Stai analizzando un estratto di trascrizione del programma radiofonico italiano \
"Il Volo del Mattino" (Radio DeeJay), condotto da Fabio Volo.

Nel testo seguente si fa riferimento a un/una {cat_label}.

ESTRATTO:
\"\"\"{testo}\"\"\"

Se nel testo è menzionato o chiaramente identificabile un {cat_label} specifico, \
rispondi con questo JSON:
{{
  "trovato": true,
  "titolo": "<titolo in italiano se esiste, altrimenti originale>",
  "anno": "<anno di uscita, stringa, vuoto se sconosciuto>",
  "autore": "<regista / autore / artista, vuoto se sconosciuto>",
  "note": "<frase breve (max 15 parole) su perché Fabio ne parla>"
}}

Se il testo è troppo vago o non identifica un {cat_label} specifico:
{{"trovato": false, "titolo": "", "anno": "", "autore": "", "note": "non identificato"}}

Rispondi SOLO con il JSON, nessun testo aggiuntivo.
"""


def chiedi_gemini(client, voce):
    """Invia una voce a Gemini e restituisce i campi arricchiti."""
    cat = voce.get("categoria", "film")
    testo = voce.get("testo", "")
    prompt = PROMPT_TEMPLATE.format(cat_label=CATEGORIA_LABEL.get(cat, cat), testo=testo)

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        raw = response.text.strip()
        # Rimuovi eventuali backtick markdown
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rsplit("```", 1)[0]
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
        risultato = chiedi_gemini(client, v)

        if risultato is None:
            print("errore, salto")
            continue

        if risultato.get("trovato"):
            v["titolo"] = risultato.get("titolo", "")
            v["anno"]   = risultato.get("anno", "")
            v["autore"] = risultato.get("autore", "")
            v["note"]   = risultato.get("note", "")
            print(f"✓ {v['titolo'] or '(titolo vuoto)'}")
        else:
            v["note"] = "non identificato"
            print("— non identificato")

        modificato = True
        time.sleep(0.5)  # rispetta rate limit free tier (15 RPM)

    if modificato:
        path.write_text(json.dumps(voci, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    salvato -> {path.relative_to(ROOT)}")


def main():
    """Punto di ingresso."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Errore: variabile GEMINI_API_KEY non impostata.")
        print("Ottieni la chiave gratis su: https://aistudio.google.com/apikey")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    date_list = sys.argv[1:] if len(sys.argv) > 1 else sorted(
        p.stem for p in RIFERIMENTI_DIR.glob("*.json")
    )

    if not date_list:
        print("Nessun file trovato in", RIFERIMENTI_DIR)
        return

    print(f"Arricchisco {len(date_list)} file con Gemini Flash (free tier)...")
    for d in date_list:
        arricchisci(client, d)


if __name__ == "__main__":
    main()
