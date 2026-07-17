# Verifica a posteriori la qualita' di data/riferimenti/*.json: per ogni voce chiede
# al modello (Groq/Cerebras, stesso budget condiviso di llm_multi.py) se "titolo" e'
# davvero il titolo di un'opera specifica (film/libro/canzone), o un falso positivo
# (persona citata di sfuggita, testata giornalistica, marchio, luogo, argomento
# generico) — lo stesso tipo di rumore trovato in una scansione manuale il 2026-07-12.
#
# NON cancella nulla da solo: scrive un report in logs/riferimenti_dubbi.json con le
# voci giudicate non valide, da rivedere ed eventualmente cancellare a mano/con
# pulisci_riferimenti_dubbi.py dopo conferma.
#
# Uso: python scripts/verifica_riferimenti.py [data1 data2 ...]
#      senza argomenti: controlla tutti i file in data/riferimenti/

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import llm_multi  # noqa: E402
from dati_root import dati_root, logs_root  # noqa: E402

RIF_DIR = dati_root(ROOT) / "riferimenti"
REPORT_PATH = logs_root(ROOT) / "riferimenti_dubbi.json"

BATCH = 15
SLEEP = 13

SYSTEM = (
    "Sei un assistente che verifica la qualita' di riferimenti culturali estratti "
    "da un programma radiofonico italiano. Rispondi SEMPRE e SOLO con un array JSON valido."
)

PROMPT_TPL = """\
Per ciascuna voce sotto, valuta se "titolo" e' VERAMENTE il titolo specifico di \
un'opera (film/libro/canzone) coerente con "categoria", oppure un falso positivo:
- una persona citata di sfuggita (politico, giornalista, personaggio pubblico, attore \
nominato SENZA il titolo di un'opera)
- una testata giornalistica o sito di notizie
- un marchio, prodotto, azienda, tecnologia
- un luogo geografico (piazza, citta', monumento)
- un argomento generico di conversazione spacciato per un libro/film/canzone

VOCI:
{lista}

Restituisci un array JSON, un elemento per ogni voce:
[
  {{"id": "...", "valido": true|false, "motivo": "breve spiegazione SOLO se valido=false"}},
  ...
]
Nel dubbio tra valido e non valido, preferisci valido=true (falsi negativi sono peggio \
di qualche falso positivo non ancora ripulito).
"""


def valuta_batch(voci: list[dict]) -> list[dict]:
    provider = llm_multi.provider_disponibile()
    if provider is None:
        return []
    client, model = llm_multi.client_e_modello(provider)
    lista = "\n".join(
        f'[{v["id"]}] categoria: {v["categoria"]} | titolo: {v["titolo"]} | '
        f'autore: {v.get("autore","")} | note: {v.get("note","")}'
        for v in voci
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT_TPL.format(lista=lista)},
        ],
        max_tokens=1200,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    if resp.usage:
        llm_multi.registra_uso(provider, resp.usage.total_tokens)
    raw = resp.choices[0].message.content.strip()
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else next(
        (v for v in parsed.values() if isinstance(v, list)), []
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date YYYY-MM-DD da verificare (default: tutte)")
    args = parser.parse_args()

    files = [RIF_DIR / f"{d}.json" for d in args.date] if args.date else sorted(RIF_DIR.glob("*.json"))

    tutte_le_voci = []
    for fp in files:
        if not fp.exists():
            continue
        for r in json.loads(fp.read_text(encoding="utf-8")):
            if r.get("titolo"):
                tutte_le_voci.append({**r, "_file": fp.name})

    print(f"Verifico {len(tutte_le_voci)} voci con titolo su {len(files)} file...")

    dubbi = []
    for i in range(0, len(tutte_le_voci), BATCH):
        if llm_multi.provider_disponibile() is None:
            print(f"STOP: budget esaurito, {len(tutte_le_voci) - i} voci rimaste non verificate.")
            break
        batch = tutte_le_voci[i:i + BATCH]
        try:
            risultati = valuta_batch(batch)
        except Exception as e:
            print(f"  batch {i // BATCH + 1}: ERRORE {e}, salto")
            continue
        by_id = {v["id"]: v for v in batch}
        n_invalidi = 0
        for r in risultati:
            if not isinstance(r, dict) or r.get("valido", True):
                continue
            v = by_id.get(r.get("id"))
            if not v:
                continue
            dubbi.append({
                "id": v["id"], "file": v["_file"], "categoria": v["categoria"],
                "titolo": v["titolo"], "autore": v.get("autore", ""),
                "motivo": r.get("motivo", ""),
            })
            n_invalidi += 1
        print(f"  batch {i // BATCH + 1}/{-(-len(tutte_le_voci)//BATCH)}: {n_invalidi} voci dubbie su {len(batch)}")
        if i + BATCH < len(tutte_le_voci):
            time.sleep(SLEEP)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(dubbi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFatto. {len(dubbi)} voci dubbie salvate in {REPORT_PATH} — NON cancellate, solo segnalate.")


if __name__ == "__main__":
    main()
