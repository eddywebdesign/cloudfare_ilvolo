#!/usr/bin/env python3
"""La scrematura locale trova le STESSE opere del modello, o altre?

E' la misura che decide il valore dell'intera strada, e costa zero: se il
riconoscitore locale ritrova solo cio' che il modello gia' trovava, abbiamo un filtro
comodo e nient'altro; se ne trova di diverse, l'unione supera quello che il modello fa
da solo e allora non stiamo risparmiando, stiamo RECUPERANDO opere che oggi si
perdono - che vale molto di piu'.

Non e' una domanda teorica: fra i modelli cloud la stessa misura aveva gia' dato un
risultato netto (presi singolarmente 60-71%, messi insieme 82%), quindi due strumenti
diversi che sbagliano in modo diverso valgono piu' del migliore dei due.

Confronta tre insiemi sulle stesse puntate:
  - le opere note (ground truth letto a mano);
  - quelle che la PRODUZIONE ha in archivio per quell'episodio (cioe' il modello);
  - quelle che la SCREMATURA locale chiude da sola.

Uso (sul K16):
    export ILVOLO_DATA_DIR=/mnt/ilvolodellasera-data ILVOLO_LOGS_DIR=/mnt/ilvolodellasera-logs
    python3 scripts/linux/pilota_unione.py

NIENTE caratteri fuori ASCII nell'output.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root  # noqa: E402
import test_qualita_identificazione as banco  # noqa: E402
import pilota_nomi_locali as nomi  # noqa: E402
import pilota_scrematura as screm  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modello", default="osiria/bert-italian-cased-ner")
    parser.add_argument("--etichette", default="MISC")
    parser.add_argument("--indice", default=str(Path.home() / "dump_wikipedia" / "indice_opere.json"))
    args = parser.parse_args()

    trascrizioni = dati_root(ROOT) / "trascrizioni"
    riferimenti = dati_root(ROOT) / "riferimenti"
    ground_truth = banco.carica_insieme_riferimento()
    indice = screm.carica_indice_locale(Path(args.indice))
    riconoscitore = nomi.carica_riconoscitore(args.modello)
    ammesse = set(args.etichette.split(","))

    solo_modello = solo_locale = entrambi = nessuno = 0
    esclusive_locali = []

    for data_str in sorted(ground_truth):
        fp_tra = trascrizioni / f"{data_str}.json"
        fp_rif = riferimenti / f"{data_str}.json"
        if not fp_tra.exists() or not fp_rif.exists():
            continue
        testo = " ".join(s.get("text", "") for s in
                         json.loads(fp_tra.read_text(encoding="utf-8")).get("segments", []))
        prodotte = [v.get("titolo", "") for v in json.loads(fp_rif.read_text(encoding="utf-8"))]

        ents = nomi.entita_di(riconoscitore, testo)
        chiuse_locali = []
        for e in ents:
            w = e.get("word", "").strip()
            if len(w) <= 2 or e.get("entity_group", "MISC") not in ammesse:
                continue
            cat, _ = screm.classifica_con_indice(w, indice)
            if cat:
                chiuse_locali.append(w)

        for opera in ground_truth[data_str]["opere"]:
            titolo = opera.get("titolo", "")
            dal_modello = any(banco.opera_riconosciuta(p, opera) for p in prodotte)
            dal_locale = any(nomi.contiene([c], titolo) for c in chiuse_locali)
            if dal_modello and dal_locale:
                entrambi += 1
            elif dal_modello:
                solo_modello += 1
            elif dal_locale:
                solo_locale += 1
                esclusive_locali.append(f"{data_str} {titolo}")
            else:
                nessuno += 1

    tot = entrambi + solo_modello + solo_locale + nessuno
    trovate_modello = entrambi + solo_modello
    unione = entrambi + solo_modello + solo_locale
    print("\n" + "=" * 72, flush=True)
    print("SI SOVRAPPONGONO O NO?", flush=True)
    print("=" * 72, flush=True)
    print(f"  opere note nel campione     : {tot}", flush=True)
    print(f"  trovate da ENTRAMBI         : {entrambi}", flush=True)
    print(f"  solo dal MODELLO in archivio: {solo_modello}", flush=True)
    print(f"  solo dalla SCREMATURA locale: {solo_locale}   <- il numero che decide", flush=True)
    print(f"  da nessuno dei due          : {nessuno}", flush=True)
    print(f"\n  modello da solo : {trovate_modello}/{tot} ({100*trovate_modello/max(tot,1):.0f}%)", flush=True)
    print(f"  UNIONE          : {unione}/{tot} ({100*unione/max(tot,1):.0f}%)", flush=True)
    if esclusive_locali:
        print("\n  opere che SOLO la scrematura locale ritrova:", flush=True)
        for e in esclusive_locali:
            print(nomi.ascii_sicuro(f"    {e}"), flush=True)


if __name__ == "__main__":
    main()
