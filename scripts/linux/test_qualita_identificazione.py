#!/usr/bin/env python3
"""Banco di prova RIUSABILE per la qualita' dell'identificazione dei riferimenti
culturali (libri/film/musica), da usare SEMPRE su un campione PRIMA di lanciare
un batch su tutto il corpus.

Perche' esiste (2026-07-26): l'identificazione stava per passare da provider
cloud (Groq/Cerebras/Gemini) a Ollama locale sulla RTX 5070, ma qwen2.5:14b non
era MAI stato validato su questo compito — era stato scelto e testato A/B solo
per la classificazione dei frammenti, che e' un compito diverso (turni di parola
isolati vs chunk di trascrizione intera). Tutte le 4.911 voci gia' presenti in
data/riferimenti/, e il loro 49% di conferma esterna, sono un baseline prodotto
dai modelli CLOUD: cambiare provider senza rimisurare significherebbe scoprire
un eventuale peggioramento solo a batch finito, su 1.103 episodi.

E' l'equivalente per l'identificazione di test_qualita_trascrizione.py (stesso
schema: campione fisso e riusabile, metriche comparabili nel tempo, nessuno
script usa-e-getta riscritto ogni volta).

SICUREZZA: lavora in una cartella dati ISOLATA (--out), mai sullo share di
produzione. Copia li' dentro solo le trascrizioni del campione e ci scrive i
riferimenti prodotti, quindi puo' essere rilanciato quante volte serve senza
toccare un solo file reale. ILVOLO_LOGS_DIR resta invece quello VERO, cosi' il
consumo di budget cloud continua a essere contabilizzato onestamente.

Uso tipico (su OMV, dentro il venv):
    python3 -u scripts/linux/test_qualita_identificazione.py --provider ollama --episodi 14
    python3 -u scripts/linux/test_qualita_identificazione.py --provider cloud  --episodi 6
Confrontare poi le due tabelle finali: voci/episodio, % confermate su database
esterni reali (Open Library/TMDB/MusicBrainz) e secondi/episodio.

Il campione e' deterministico a parita' di --seed, quindi le due esecuzioni
lavorano sugli STESSI episodi ed il confronto e' un vero A/B, non due misure su
insiemi diversi.
"""
import argparse
import collections
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def scegli_campione(trascrizioni_dir: Path, riferimenti_dir: Path,
                    n: int, seed: int) -> list[str]:
    """Sceglie n episodi stratificati per anno tra quelli REALMENTE nel backlog:
    trascritti ma senza riferimenti estratti. Stratificare evita di misurare la
    qualita' su un solo periodo (il programma cambia formato negli anni) e il
    seed fisso rende il campione ripetibile tra provider diversi."""
    trascritti = {p.stem for p in trascrizioni_dir.glob("*.json")}
    gia_fatti = {p.stem for p in riferimenti_dir.glob("*.json")}
    backlog = sorted(trascritti - gia_fatti)
    if not backlog:
        return []

    per_anno: dict[str, list[str]] = collections.defaultdict(list)
    for d in backlog:
        per_anno[d[:4]].append(d)

    rnd = random.Random(seed)
    campione: list[str] = []
    anni = sorted(per_anno)
    # Giro a giro, un episodio per anno finche' non si raggiunge n: cosi' anche
    # con n piccolo tutti gli anni sono rappresentati prima di infoltirne uno.
    while len(campione) < n and any(per_anno[a] for a in anni):
        for anno in anni:
            if len(campione) >= n:
                break
            disponibili = [d for d in per_anno[anno] if d not in campione]
            if disponibili:
                campione.append(rnd.choice(disponibili))
    return sorted(campione)


def prepara_ambiente_isolato(out_dir: Path, campione: list[str],
                             trascrizioni_reali: Path) -> Path:
    """Costruisce una cartella dati isolata con le sole trascrizioni del campione
    e una cartella riferimenti vuota. Ritorna il path da usare come
    ILVOLO_DATA_DIR. Azzera un eventuale run precedente cosi' ogni prova parte
    pulita (altrimenti merge_riferimenti troverebbe le voci del giro prima e
    deduplicherebbe, falsando il conteggio)."""
    data_dir = out_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    (data_dir / "trascrizioni").mkdir(parents=True)
    (data_dir / "riferimenti").mkdir(parents=True)
    for d in campione:
        shutil.copy2(trascrizioni_reali / f"{d}.json", data_dir / "trascrizioni" / f"{d}.json")
    return data_dir


def metriche(riferimenti_dir: Path) -> dict:
    """Conta voci prodotte, distribuzione per categoria e quante hanno superato
    la verifica su database esterni reali. La % confermata e' la metrica
    oggettiva piu' importante: non dipende dal mio giudizio ne' da quello di un
    altro LLM, ma dall'esistenza reale dell'opera in Open Library/TMDB/MusicBrainz."""
    tot = 0
    conf = 0
    per_cat: collections.Counter = collections.Counter()
    conf_cat: collections.Counter = collections.Counter()
    voci: list[tuple] = []
    for f in sorted(riferimenti_dir.glob("*.json")):
        for r in json.loads(f.read_text(encoding="utf-8")):
            tot += 1
            cat = r.get("categoria", "?")
            per_cat[cat] += 1
            ok = bool(r.get("confermato_esterno"))
            if ok:
                conf += 1
                conf_cat[cat] += 1
            voci.append((f.stem, cat, r.get("titolo", ""), r.get("autore", ""), ok))
    return {"tot": tot, "conf": conf, "per_cat": per_cat, "conf_cat": conf_cat, "voci": voci}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider",
                        choices=["auto", "ollama", "cloud", "groq", "cerebras", "gemini"],
                        default="auto",
                        help="auto = come in produzione; ollama/cloud = forza la famiglia; "
                             "groq/cerebras/gemini = fissa UN solo provider (indispensabile per "
                             "confrontare due modelli: in 'cloud' le chiamate ruotano sui tre e "
                             "il risultato sarebbe una media, non la misura di un modello). "
                             "Con groq si puo' scegliere il modello via ILVOLO_GROQ_MODEL.")
    parser.add_argument("--episodi", type=int, default=14, help="dimensione del campione")
    parser.add_argument("--seed", type=int, default=2026,
                        help="stesso seed = stesso campione, indispensabile per confrontare due provider")
    parser.add_argument("--out", default="/tmp/eval_identificazione",
                        help="cartella isolata di lavoro (MAI lo share di produzione)")
    parser.add_argument("--salta-verifica-esterna", action="store_true",
                        help="salta la verifica su Open Library/TMDB/MusicBrainz (piu' veloce, ma perdi la metrica principale)")
    args = parser.parse_args()

    data_reale = os.environ.get("ILVOLO_DATA_DIR")
    if not data_reale:
        sys.exit("ERRORE: ILVOLO_DATA_DIR non impostata. Esportala come fa il cron "
                 "(vedi scripts/linux/lancia_clasificacion_omv.sh) prima di lanciare.")
    trascrizioni_reali = Path(data_reale) / "trascrizioni"
    riferimenti_reali = Path(data_reale) / "riferimenti"

    campione = scegli_campione(trascrizioni_reali, riferimenti_reali, args.episodi, args.seed)
    if not campione:
        sys.exit("Nessun episodio nel backlog (tutti i trascritti hanno gia' i riferimenti).")
    print(f"Campione ({len(campione)} episodi, seed {args.seed}): {', '.join(campione)}", flush=True)

    out_dir = Path(args.out)
    data_iso = prepara_ambiente_isolato(out_dir, campione, trascrizioni_reali)
    print(f"Ambiente isolato: {data_iso} (produzione NON toccata)", flush=True)

    # ILVOLO_DATA_DIR va impostata PRIMA di importare i moduli della pipeline:
    # calcolano RIF_DIR/TRASCRIZIONI_DIR a livello di modulo, all'import.
    # ILVOLO_LOGS_DIR resta invece quella vera, cosi' il budget cloud consumato
    # da questa prova viene contabilizzato davvero e non in un file usa-e-getta.
    os.environ["ILVOLO_DATA_DIR"] = str(data_iso)
    os.environ.setdefault("ILVOLO_LOGS_DIR", str(Path(data_reale).parent / "logs"))

    sys.path.insert(0, str(ROOT / "scripts"))
    import llm_multi  # noqa: E402
    from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402

    # Forzare il provider serve a rendere il confronto un vero A/B. Si agisce sul
    # rilevamento di raggiungibilita' di Ollama, cioe' esattamente la leva che
    # provider_disponibile() usa in produzione: non si simula nient'altro.
    if args.provider == "cloud":
        llm_multi._ollama_raggiungibile = lambda: False
    elif args.provider == "ollama":
        if not llm_multi._ollama_raggiungibile():
            sys.exit("ERRORE: Ollama non raggiungibile su "
                     f"{llm_multi.OLLAMA_BASE_URL} — avvialo prima (systemctl start ollama sul K16).")
        llm_multi.provider_disponibile = lambda: "ollama"
    elif args.provider in ("groq", "cerebras", "gemini"):
        # Un solo provider fisso: serve a misurare UN modello, non la media di tre.
        # Si controlla comunque il budget reale, cosi' il test si ferma da solo invece
        # di sbattere contro una raffica di 429 (e di rubare quota al batch in corso).
        scelto = args.provider
        if not llm_multi.budget_disponibile(scelto):
            sys.exit(f"ERRORE: budget {scelto} gia' esaurito per oggi, test non avviato.")
        llm_multi.provider_disponibile = lambda: scelto

    provider_reale = llm_multi.provider_disponibile()
    print(f"Provider in uso: {provider_reale}", flush=True)
    if provider_reale is None:
        sys.exit("ERRORE: nessun provider disponibile (budget cloud esaurito e Ollama spento).")

    t0 = time.time()
    falliti = []
    for i, data_str in enumerate(campione, 1):
        d = json.loads((data_iso / "trascrizioni" / f"{data_str}.json").read_text(encoding="utf-8"))
        segs = d.get("segments", [])
        if not segs:
            print(f"[{i}/{len(campione)}] {data_str}: trascrizione senza segmenti, salto", flush=True)
            continue
        testo = " ".join(s.get("text", "") for s in segs)
        durata = segs[-1].get("end", 0.0)
        te = time.time()
        try:
            refs = estrai_riferimenti(testo)
            merge_riferimenti(data_str, refs, testo, durata)
            print(f"[{i}/{len(campione)}] {data_str}: {len(refs)} voci in {time.time()-te:.0f}s", flush=True)
        except Exception as e:  # un episodio rotto non deve far perdere tutto il giro
            falliti.append((data_str, repr(e)))
            print(f"[{i}/{len(campione)}] {data_str}: ERRORE {e!r}", flush=True)
    durata_estrazione = time.time() - t0

    if not args.salta_verifica_esterna:
        print("\nVerifica su database esterni (Open Library/TMDB/MusicBrainz)...", flush=True)
        subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "verifica_riferimenti_esterna.py"),
             "--dataset", "riferimenti"],
            cwd=str(ROOT), env={**os.environ}, check=False,
        )

    m = metriche(data_iso / "riferimenti")
    n_ep = len(campione)
    print("\n" + "=" * 68, flush=True)
    print(f"RISULTATO — provider={provider_reale}, {n_ep} episodi, seed={args.seed}", flush=True)
    print("=" * 68, flush=True)
    print(f"  voci totali            : {m['tot']}  ({m['tot']/max(n_ep,1):.1f} per episodio)", flush=True)
    print(f"  confermate su DB reali : {m['conf']}  ({100*m['conf']/max(m['tot'],1):.1f}%)", flush=True)
    print(f"  tempo estrazione       : {durata_estrazione:.0f}s  ({durata_estrazione/max(n_ep,1):.0f}s per episodio)", flush=True)
    if falliti:
        print(f"  EPISODI FALLITI        : {len(falliti)} -> {falliti}", flush=True)
    print("  per categoria (totali / confermate):", flush=True)
    for cat, v in m["per_cat"].most_common():
        print(f"    {cat:8s} {v:4d}  ->  {m['conf_cat'][cat]:4d} confermate "
              f"({100*m['conf_cat'][cat]/max(v,1):.0f}%)", flush=True)

    # Il numero da solo non dimostra che le voci abbiano senso: stampa un campione
    # da leggere con gli occhi, come richiesto esplicitamente dall'utente.
    print("\n  CAMPIONE DA LEGGERE (15 voci a caso):", flush=True)
    rnd = random.Random(args.seed)
    for v in rnd.sample(m["voci"], min(15, len(m["voci"]))):
        print(f"    [{v[0]}] {'OK ' if v[4] else '   '} {v[1]:7s} {v[2][:44]:44s} | {v[3][:24]}", flush=True)

    print(f"\nBaseline storico da battere (cloud, 982 episodi): 5,0 voci/episodio, 49% confermate.", flush=True)


if __name__ == "__main__":
    main()
