# Verifica a posteriori la qualita' di data/frammenti/*.json: per ogni frammento gia'
# classificato (tipo/titolo/tema non vuoti) chiede al modello (Groq/Cerebras, stesso
# budget condiviso di llm_multi.py) se la classificazione e' davvero supportata dal
# testo del frammento, o se il modello ha "allucinato" un riferimento/tema non presente
# — stesso principio di verifica_riferimenti.py, applicato ai frammenti invece che ai
# riferimenti culturali.
#
# NON cancella nulla da solo: scrive un report in logs/frammenti_dubbi.json con le
# voci giudicate non valide, da rivedere ed eventualmente correggere a mano con
# modifica_frammento.py dopo conferma.
#
# Uso: python scripts/verifica_frammenti.py [data1 data2 ...]
#      senza argomenti: controlla tutti i file in data/frammenti/

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import llm_multi  # noqa: E402
from dati_root import dati_root, logs_root  # noqa: E402

FRAMMENTI_DIR = dati_root(ROOT) / "frammenti"
REPORT_PATH = logs_root(ROOT) / "frammenti_dubbi.json"

BATCH = 10
SLEEP = 13

SYSTEM = (
    "Sei un assistente che verifica la qualita' di frammenti classificati automaticamente "
    "da un programma radiofonico italiano. Rispondi SEMPRE e SOLO con un array JSON valido."
)

PROMPT_TPL = """\
Per ciascun frammento sotto, valuta se "titolo" e "tema" sono DAVVERO supportati dal \
testo del frammento, oppure se il modello ha inventato/allucinato un riferimento o un \
argomento NON presente nel testo. Controlla anche se "tipo" e' coerente col contenuto \
(es. "riferimento_musica" ma il testo non parla di musica = non valido).

FRAMMENTI:
{lista}

Restituisci un array JSON, un elemento per ogni frammento:
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
        f'[{v["id"]}] tipo: {v["tipo"]} | titolo: {v["titolo"]} | tema: {v["tema"]} | '
        f'testo: {v["testo"][:400]}'
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

    files = [FRAMMENTI_DIR / f"{d}.json" for d in args.date] if args.date else sorted(FRAMMENTI_DIR.glob("*.json"))

    tutte_le_voci = []
    for fp in files:
        if not fp.exists():
            continue
        for f in json.loads(fp.read_text(encoding="utf-8")):
            if f.get("titolo"):
                tutte_le_voci.append({**f, "_file": fp.name})

    print(f"Verifico {len(tutte_le_voci)} frammenti classificati su {len(files)} file...")

    dubbi = []
    for i in range(0, len(tutte_le_voci), BATCH):
        if llm_multi.provider_disponibile() is None:
            print(f"STOP: budget esaurito, {len(tutte_le_voci) - i} frammenti rimasti non verificati.")
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
                "id": v["id"], "file": v["_file"], "tipo": v["tipo"],
                "titolo": v["titolo"], "tema": v["tema"],
                "testo": v["testo"][:200], "motivo": r.get("motivo", ""),
            })
            n_invalidi += 1
        print(f"  batch {i // BATCH + 1}/{-(-len(tutte_le_voci)//BATCH)}: {n_invalidi} frammenti dubbi su {len(batch)}")
        if i + BATCH < len(tutte_le_voci):
            time.sleep(SLEEP)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(dubbi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFatto. {len(dubbi)} frammenti dubbi salvati in {REPORT_PATH} — NON cancellati, solo segnalati.")


if __name__ == "__main__":
    main()
