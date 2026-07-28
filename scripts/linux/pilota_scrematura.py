#!/usr/bin/env python3
"""Quanto lavoro si chiude coi soli database, senza mai passare dal modello?

Obiettivo dell'utente (2026-07-28): "affinare il processo su K16 al punto di ESCLUDERE
i dati che classifichiamo grazie ai database, evitando di darli in pasto alle IA".
Il budget del modello e' l'unica risorsa scarsa: ogni opera che gli archivi sanno
chiudere da soli e' budget che resta per il resto.

Lo stadio in prova, tutto in locale e gratuito:
  1. il riconoscitore sul K16 tira fuori le entita' dall'episodio (misurato: 0,2s per
     episodio, 70 entita' distinte, e ritrova il 56% dei TITOLI noti - ma non gli
     autori, che in onda non si pronunciano);
  2. ogni entita' passa da Wikidata UNA volta sola: non solo per sapere se esiste, ma
     per sapere COSA E'. La descrizione dice la categoria ("film del 1979", "romanzo
     di...", "canzone di..."), che il riconoscitore da solo non puo' dare;
  3. cio' che risulta un'opera di una categoria che ci interessa e' CHIUSO, non va al
     modello. Tutto il resto resta da giudicare.

⚠️ La misura ha due facce e vanno lette insieme, come sempre in questo progetto:
  - CHIUSE BENE: opere note davvero riconosciute e classificate (lavoro risparmiato);
  - RUMORE PROMOSSO: entita' che NON sono opere citate ma che Wikidata conferma lo
    stesso. E' il rischio vero di questo stadio: nell'episodio piu' denso il
    riconoscitore restituisce anche America, Bologna, Boston, Brescia, California,
    New York, Radio DJ. "Manhattan" e' un film E un quartiere, e l'archivio non sa
    in quale senso e' stato nominato in onda. Se lo stadio promuove i luoghi, non
    abbiamo risparmiato lavoro: abbiamo sporcato l'archivio a monte invece che a valle.

Uso (sul K16):
    export ILVOLO_DATA_DIR=/mnt/ilvolodellasera-data ILVOLO_LOGS_DIR=/mnt/ilvolodellasera-logs
    python3 scripts/linux/pilota_scrematura.py --episodi 2024-11-04 2017-11-09

NIENTE caratteri fuori ASCII nell'output.
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dati_root import dati_root, logs_root  # noqa: E402
import test_qualita_identificazione as banco  # noqa: E402
import pilota_nomi_locali as nomi  # noqa: E402
import verifica_riferimenti_esterna as ve  # noqa: E402

# Le stesse spie della verifica a valle: una sola definizione di "cosa e' un'opera di
# questa categoria" per tutta la pipeline, altrimenti lo stadio a monte e quello a
# valle finiscono per dire due cose diverse sullo stesso titolo.
CATEGORIE = ("libro", "film", "musica")


def classifica_con_wikidata(nome: str) -> tuple[str, str]:
    """Che cos'e' questa entita', secondo Wikidata? Ritorna (categoria, descrizione).

    Una sola chiamata per entita', non una per categoria: si guarda la descrizione e si
    vede in quale delle nostre categorie ricade. Categoria vuota = non e' un'opera che
    ci interessa (una persona, un luogo, un'azienda, o niente del tutto).

    Riusa il filtro negativo della verifica a valle: una persona o un gruppo non e' mai
    un'opera, per quanto la descrizione somigli."""
    try:
        risultati = ve._wikidata_cerca(nome, "it", 5)
    except Exception:
        return "", "non ho potuto chiedere"
    for it in risultati:
        if ve._similarita(nome, it.get("label", "")) < 0.85:
            continue
        descrizione = (it.get("description") or "").lower()
        if any(s in descrizione for s in ve.WIKIDATA_NON_OPERE):
            return "", descrizione
        for cat in CATEGORIE:
            if any(s in descrizione for s in ve.WIKIDATA_SPIE.get(cat, ())):
                return cat, descrizione
        return "", descrizione
    return "", "nessun match"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodi", nargs="*")
    parser.add_argument("--modello", default="osiria/bert-italian-cased-ner")
    args = parser.parse_args()

    trascrizioni = dati_root(ROOT) / "trascrizioni"
    ground_truth = banco.carica_insieme_riferimento()
    episodi = args.episodi or sorted(ground_truth)
    episodi = [d for d in episodi if (trascrizioni / f"{d}.json").exists() and d in ground_truth]
    if not episodi:
        sys.exit("ERRORE: nessun episodio utilizzabile. ILVOLO_DATA_DIR e' impostata?")

    riconoscitore = nomi.carica_riconoscitore(args.modello)
    chiuse_bene = attese = 0
    promosse_totali = 0
    dettaglio = {}
    inizio = time.time()

    for data_str in episodi:
        d = json.loads((trascrizioni / f"{data_str}.json").read_text(encoding="utf-8"))
        testo = " ".join(s.get("text", "") for s in d.get("segments", []))
        ents = nomi.entita_di(riconoscitore, testo)
        candidati = sorted({e.get("word", "").strip() for e in ents
                            if len(e.get("word", "").strip()) > 2})

        promosse = []
        for c in candidati:
            cat, descrizione = classifica_con_wikidata(c)
            if cat:
                promosse.append((c, cat, descrizione[:60]))

        gt = ground_truth[data_str]
        note = [(o.get("titolo", ""), o.get("categoria", "")) for o in gt["opere"]]
        trovate = []
        for titolo, cat_attesa in note:
            attese += 1
            match = next((p for p in promosse if nomi.contiene([p[0]], titolo)), None)
            if match:
                chiuse_bene += 1
                trovate.append((titolo, match[1], match[1] == cat_attesa))

        promosse_totali += len(promosse)
        titoli_noti = {banco._norm(t) for t, _ in note}
        rumore = [p for p in promosse if not any(nomi.contiene([p[0]], t) for t, _ in note)]
        dettaglio[data_str] = {
            "candidati": len(candidati), "promosse": len(promosse),
            "opere_note": len(note), "chiuse": len(trovate),
            "rumore_promosso": [f"{p[0]} -> {p[1]}" for p in rumore],
        }
        print(nomi.ascii_sicuro(
            f"\n  {data_str}: {len(candidati)} entita' -> {len(promosse)} promosse a opera | "
            f"opere note chiuse {len(trovate)}/{len(note)}"), flush=True)
        for t, cat, giusta in trovate:
            print(nomi.ascii_sicuro(f"      CHIUSA  {t!r} come {cat}"
                                    + ("" if giusta else "  <- CATEGORIA SBAGLIATA")), flush=True)
        for p in rumore[:12]:
            print(nomi.ascii_sicuro(f"      rumore  {p[0]!r} -> {p[1]}  ({p[2]})"), flush=True)

    print("\n" + "=" * 72, flush=True)
    print("SCREMATURA A MONTE - quanto si chiude senza il modello", flush=True)
    print("=" * 72, flush=True)
    print(f"  opere note CHIUSE dai soli database : {chiuse_bene}/{attese} "
          f"({100*chiuse_bene/max(attese,1):.0f}%)  <- lavoro tolto al modello", flush=True)
    print(f"  entita' promosse a opera in totale  : {promosse_totali} "
          f"(di cui {promosse_totali-chiuse_bene} NON sono opere note)", flush=True)
    print(f"  tempo                               : {time.time()-inizio:.0f}s "
          f"per {len(episodi)} episodi", flush=True)
    print("  Le due righe si leggono INSIEME: promuovere tutto chiude tutto e non "
          "risparmia niente.", flush=True)

    cartella = logs_root(ROOT) / "banco_pilota_nomi"
    cartella.mkdir(parents=True, exist_ok=True)
    fp = cartella / f"scrematura_{date.today()}_{datetime.now().strftime('%H%M')}.json"
    fp.write_text(json.dumps({"chiuse": chiuse_bene, "attese": attese,
                              "promosse": promosse_totali, "per_episodio": dettaglio},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nArchiviato in {fp}", flush=True)


if __name__ == "__main__":
    main()
