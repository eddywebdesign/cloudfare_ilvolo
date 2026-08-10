#!/usr/bin/env python3
"""Testo di una puntata con l'indice di segmento davanti a ogni riga.

E' la base della rilettura integrale delle 21 puntate del campione (2026-08-01).
Serve a una cosa sola: rendere ogni prova INDIRIZZABILE. Chi legge non scrive piu'
"quest'opera e' in questa puntata", scrive "segmento 268", e verifica_insieme_riferimento.py
puo' andare a controllare che le parole citate siano davvero li'.

Il formato e' volutamente povero - "[268] testo" e basta - perche' chi legge deve
vedere la trascrizione, non un'interpretazione della trascrizione. I tempi, i
punteggi di confidenza e il dettaglio parola per parola restano nel JSON di
partenza per chi ne ha bisogno.

Uso:
    python3 scripts/linux/estrai_testo_puntata.py 2016-12-06
    python3 scripts/linux/estrai_testo_puntata.py 2016-12-06 --con-speaker
    python3 scripts/linux/estrai_testo_puntata.py --campione --out CARTELLA

NIENTE caratteri fuori ASCII nell'output di servizio (il testo trascritto resta
com'e': e' la fonte, non si tocca).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dati_root import dati_root  # noqa: E402

GT = Path(__file__).resolve().parent.parent / "linux" / "insieme_riferimento.json"


def campione():
    """Le 21 date del campione, dal ground truth attuale.

    Del file vecchio si prende SOLO l'elenco delle date: e' l'unica cosa che non e'
    in discussione. Le opere che contiene non devono arrivare a chi legge, o la
    lettura smetterebbe di essere cieca e si limiterebbe a confermare."""
    return sorted(json.loads(GT.read_text(encoding="utf-8"))["episodi"])


def righe_puntata(data_ep, con_speaker=False):
    percorso = dati_root(ROOT) / "trascrizioni" / f"{data_ep}.json"
    if not percorso.exists():
        raise FileNotFoundError(percorso)
    segmenti = json.loads(percorso.read_text(encoding="utf-8")).get("segments", [])
    righe = []
    for i, s in enumerate(segmenti):
        testo = s.get("text", "").strip()
        if con_speaker:
            # Lo speaker viene dalla diarizzazione e sui segmenti musicali spesso
            # manca del tutto: e' un indizio su cosa e' parlato e cosa e' cantato,
            # non un verdetto.
            voci = Counter(w.get("speaker") for w in s.get("words", []) if w.get("speaker"))
            chi = voci.most_common(1)[0][0] if voci else "?"
            righe.append(f"[{i}] ({chi}) {testo}")
        else:
            righe.append(f"[{i}] {testo}")
    return righe


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("date", nargs="*", help="date da estrarre (YYYY-MM-DD)")
    p.add_argument("--campione", action="store_true",
                   help="tutte le 21 date del campione")
    p.add_argument("--con-speaker", action="store_true",
                   help="anteponi lo speaker prevalente del segmento")
    p.add_argument("--out", type=Path,
                   help="cartella dove scrivere <data>.txt invece di stampare")
    args = p.parse_args()

    date = campione() if args.campione else args.date
    if not date:
        p.error("serve almeno una data, oppure --campione")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    for data_ep in date:
        righe = righe_puntata(data_ep, args.con_speaker)
        if args.out:
            (args.out / f"{data_ep}.txt").write_text("\n".join(righe) + "\n",
                                                     encoding="utf-8")
            print(f"{data_ep}  {len(righe):4d} segmenti  -> {args.out / (data_ep + '.txt')}")
        else:
            print("\n".join(righe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
