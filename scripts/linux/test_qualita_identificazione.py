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

# Campione di riferimento fissato il 2026-07-26: e' quello su cui sono state fatte
# le misure storiche (Ollama qwen2.5:14b recall 35%, cloud misto recall 88%) e su
# cui va confrontato QUALUNQUE nuovo modello/provider, altrimenti si confrontano
# numeri ottenuti su episodi diversi — cioe' niente.
# Per due di questi episodi esiste un insieme di riferimento costruito a mano
# leggendo la puntata intera: 2024-11-04 (17 opere) e 2017-11-09 (7 opere).
CAMPIONE_STORICO = [
    "2014-05-08", "2014-12-24", "2015-04-30", "2015-10-26", "2017-03-22",
    "2017-11-09", "2021-05-06", "2021-05-13", "2022-01-10", "2022-11-30",
    "2023-11-17", "2024-11-04", "2025-09-16", "2026-04-27",
]


def scegli_campione(trascrizioni_dir: Path, riferimenti_dir: Path,
                    n: int, seed: int) -> list[str]:
    """Sceglie n episodi stratificati per anno tra TUTTI quelli trascritti.

    ⚠️ Pescava dal solo backlog (trascritti senza riferimenti) fino al 2026-07-26:
    sbagliato, perche' il backlog si svuota mentre il recupero gira, quindi lo
    stesso seed produceva campioni DIVERSI a poche ore di distanza. Successo
    davvero: un confronto tra modelli e' partito su 14 episodi di cui solo 2 in
    comune con le misure precedenti — un A/B che non misurava niente, e se ne
    accorge solo chi guarda l'elenco stampato. Pescare da tutti i trascritti
    rende il campione stabile nel tempo; non c'e' controindicazione perche' il
    banco di prova lavora in una cartella isolata, dove ri-estrarre un episodio
    che la produzione ha gia' fatto e' innocuo.
    Per un confronto con le misure storiche usare comunque --campione storico."""
    trascritti = sorted(p.stem for p in trascrizioni_dir.glob("*.json"))
    if not trascritti:
        return []

    per_anno: dict[str, list[str]] = collections.defaultdict(list)
    for d in trascritti:
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


def metriche(riferimenti_dir: Path, solo: set | None = None) -> dict:
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
        if solo is not None and f.stem not in solo:
            continue
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


INSIEME_RIFERIMENTO = Path(__file__).resolve().parent / "insieme_riferimento.json"


def carica_insieme_riferimento() -> dict:
    """Carica il ground truth costruito a mano (vedi insieme_riferimento.json).
    Ritorna {data_episodio: {"opere": [...], "parziale": bool}}."""
    if not INSIEME_RIFERIMENTO.exists():
        return {}
    return json.loads(INSIEME_RIFERIMENTO.read_text(encoding="utf-8")).get("episodi", {})


def _norm(s: str) -> str:
    """Normalizzazione minima per confrontare due titoli: minuscolo, senza
    punteggiatura ne' spazi multipli. Volutamente identica nello spirito a
    _normalizza() di verifica_riferimenti_esterna.py — se una delle due cambia,
    allineare anche l'altra."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def _stesso_titolo(a: str, b: str) -> bool:
    """Due titoli sono la stessa opera? Confronto tollerante al rumore di
    trascrizione (SequenceMatcher sui caratteri va bene per i TITOLI — per i nomi
    di PERSONA no, vedi la lezione in verifica_riferimenti_esterna._similarita_autore)."""
    import difflib
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def misura_recall(riferimenti_dir: Path, ground_truth: dict, campione: list[str]) -> dict:
    """RECALL: quante delle opere realmente presenti nell'episodio sono state
    ritrovate. E' la meta' della qualita' che nessuna metrica basata sui database
    puo' dare: un database dice se una voce PRODOTTA e' vera, mai quante ne mancano.

    Considera solo gli episodi del campione che hanno un ground truth. Gli episodi
    marcati "parziale" elencano solo riferimenti noti come PERSI: per quelli la
    misura e' una prova di non-regressione (li ritroviamo adesso?), non una recall
    vera, quindi vengono contati a parte."""
    trovate, mancate, per_episodio, non_estratti = [], [], {}, []
    for data_str in campione:
        gt = ground_truth.get(data_str)
        if not gt:
            continue
        fp = riferimenti_dir / f"{data_str}.json"
        # ⚠️ "mai estratto" NON e' "estratto e non ha trovato nulla". Contarli
        # insieme darebbe una recall falsa (misurata 0% al primo giro, solo perche'
        # 6 episodi del campione erano ancora nel backlog). E' la stessa famiglia
        # di errore dell'idempotenza che controllava solo l'esistenza del file.
        if not fp.exists():
            non_estratti.append(data_str)
            continue
        prodotte = json.loads(fp.read_text(encoding="utf-8"))
        ok_ep, ko_ep = [], []
        for opera in gt["opere"]:
            match = next((p for p in prodotte if _stesso_titolo(p.get("titolo", ""), opera["titolo"])), None)
            (ok_ep if match else ko_ep).append((opera, match))
        trovate += ok_ep
        mancate += ko_ep
        per_episodio[data_str] = {
            "attese": len(gt["opere"]), "trovate": len(ok_ep),
            "parziale": bool(gt.get("parziale")), "mancate": [o["titolo"] for o, _ in ko_ep],
        }
    tot = len(trovate) + len(mancate)
    return {"attese": tot, "trovate": len(trovate), "mancate": mancate,
            "per_episodio": per_episodio, "non_estratti": non_estratti,
            "recall": (len(trovate) / tot) if tot else None}


def misura_ancoraggio(riferimenti_dir: Path, trascrizioni_dir: Path,
                      solo: set | None = None) -> dict:
    """PRECISIONE non circolare: la voce prodotta e' davvero ancorata al testo
    dell'episodio (titolo o autore presenti nella trascrizione)?

    Perche' serve accanto alla '% confermata su database': quella misura dice solo
    che l'opera ESISTE al mondo, non che sia stata citata in QUESTA puntata — e'
    la falla che ha lasciato passare "What's Going On"/Marvin Gaye come confermato
    in un episodio dove quel titolo non compare da nessuna parte. Questa misura
    costa zero (nessuna chiamata di rete) ed e' deterministica: stesso input,
    stesso numero, sempre."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from trascrivi_e_estrai_clip import _titolo_e_ancorato_al_testo  # noqa: E402

    ancorate, non_ancorate = 0, []
    for fp in sorted(riferimenti_dir.glob("*.json")):
        if solo is not None and fp.stem not in solo:
            continue
        tp = trascrizioni_dir / fp.name
        if not tp.exists():
            continue
        segs = json.loads(tp.read_text(encoding="utf-8")).get("segments", [])
        testo = " ".join(s.get("text", "") for s in segs)
        for r in json.loads(fp.read_text(encoding="utf-8")):
            if _titolo_e_ancorato_al_testo(r.get("titolo", ""), r.get("autore", ""), testo):
                ancorate += 1
            else:
                non_ancorate.append((fp.stem, r.get("categoria", "?"), r.get("titolo", ""),
                                     r.get("autore", ""), bool(r.get("confermato_esterno"))))
    tot = ancorate + len(non_ancorate)
    return {"tot": tot, "ancorate": ancorate, "non_ancorate": non_ancorate,
            "quota": (ancorate / tot) if tot else None}


def stampa_misure(riferimenti_dir: Path, trascrizioni_dir: Path, campione: list[str],
                  ground_truth: dict, seed: int, etichetta: str) -> dict:
    """Stampa le TRE misure che insieme descrivono la qualita', e le ritorna.

    Nessuna delle tre da sola basta, ed e' il motivo per cui il progetto ha girato
    a vuoto per settimane ottimizzando contro un solo numero:
      - RECALL      : quante opere reali ritroviamo (serve il ground truth umano)
      - ANCORAGGIO  : quante voci prodotte sono davvero citate nell'episodio
      - CONFERMA DB : quante voci prodotte esistono davvero come opera
    Una pipeline puo' avere il 100% di conferma DB e recall 30% (trova poco ma
    giusto), oppure recall alta e ancoraggio basso (inventa citazioni per opere
    che esistono). Vanno lette insieme, sempre."""
    solo = set(campione)
    m = metriche(riferimenti_dir, solo=solo)
    rec = misura_recall(riferimenti_dir, ground_truth, campione)
    anc = misura_ancoraggio(riferimenti_dir, trascrizioni_dir, solo=solo)
    n_ep = len(campione)

    print("\n" + "=" * 72, flush=True)
    print(f"MISURE — {etichetta} ({n_ep} episodi)", flush=True)
    print("=" * 72, flush=True)
    print(f"  voci totali            : {m['tot']}  ({m['tot']/max(n_ep,1):.1f} per episodio)", flush=True)

    if rec.get("non_estratti"):
        # Niente caratteri fuori ASCII nell'output: la console Windows (cp1252) va in
        # UnicodeEncodeError e il banco muore a meta' misura invece di dare i numeri.
        print(f"  [!] episodi con ground truth MAI ESTRATTI (esclusi dalla recall, "
              f"non sono un fallimento della pipeline): {', '.join(rec['non_estratti'])}", flush=True)
    if rec["recall"] is not None:
        print(f"  RECALL (ground truth)  : {rec['trovate']}/{rec['attese']} = "
              f"{100*rec['recall']:.0f}%", flush=True)
        for data_str, d in sorted(rec["per_episodio"].items()):
            tag = " [parziale: solo casi noti come persi]" if d["parziale"] else ""
            print(f"      {data_str}: {d['trovate']}/{d['attese']}{tag}", flush=True)
            if d["mancate"]:
                print(f"         MANCANTI: {', '.join(d['mancate'])}", flush=True)
    else:
        print("  RECALL                 : non misurabile (nessun ground truth nel campione)", flush=True)

    if anc["quota"] is not None:
        print(f"  ANCORAGGIO al testo    : {anc['ancorate']}/{anc['tot']} = "
              f"{100*anc['quota']:.0f}%  (voci davvero citate nell'episodio)", flush=True)
        confermate_ma_non_ancorate = [v for v in anc["non_ancorate"] if v[4]]
        if confermate_ma_non_ancorate:
            print(f"      di cui MARCATE SICURE pur non essendo ancorate: "
                  f"{len(confermate_ma_non_ancorate)} — il caso peggiore, "
                  f"un errore col bollo di garanzia:", flush=True)
            for v in confermate_ma_non_ancorate[:10]:
                print(f"         [{v[0]}] {v[1]:7s} {v[2][:40]:40s} | {v[3][:22]}", flush=True)

    print(f"  confermate su DB reali : {m['conf']}  "
          f"({100*m['conf']/max(m['tot'],1):.1f}%)", flush=True)
    print("  per categoria (totali / confermate):", flush=True)
    for cat, v in m["per_cat"].most_common():
        print(f"    {cat:8s} {v:4d}  ->  {m['conf_cat'][cat]:4d} confermate "
              f"({100*m['conf_cat'][cat]/max(v,1):.0f}%)", flush=True)

    print("\n  CAMPIONE DA LEGGERE (15 voci a caso):", flush=True)
    rnd = random.Random(seed)
    for v in rnd.sample(m["voci"], min(15, len(m["voci"]))):
        print(f"    [{v[0]}] {'OK ' if v[4] else '   '} {v[1]:7s} {v[2][:44]:44s} | {v[3][:24]}", flush=True)
    return {"metriche": m, "recall": rec, "ancoraggio": anc}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider",
                        choices=["auto", "ollama", "cloud", "groq", "cerebras", "gemini", "mistral"],
                        default="auto",
                        help="auto = come in produzione; ollama/cloud = forza la famiglia; "
                             "groq/cerebras/gemini = fissa UN solo provider (indispensabile per "
                             "confrontare due modelli: in 'cloud' le chiamate ruotano sui tre e "
                             "il risultato sarebbe una media, non la misura di un modello). "
                             "Con groq si puo' scegliere il modello via ILVOLO_GROQ_MODEL.")
    parser.add_argument("--episodi", type=int, default=14, help="dimensione del campione")
    parser.add_argument("--campione", default=None,
                        help="'storico' per usare i 14 episodi su cui sono state fatte tutte le "
                             "misure del 2026-07-26 (unico modo di confrontarsi con quei numeri), "
                             "oppure un elenco di date separate da virgola.")
    parser.add_argument("--seed", type=int, default=2026,
                        help="stesso seed = stesso campione, indispensabile per confrontare due provider")
    parser.add_argument("--out", default="/tmp/eval_identificazione",
                        help="cartella isolata di lavoro (MAI lo share di produzione)")
    parser.add_argument("--pausa-chunk", type=int, default=None,
                        help="secondi tra un chunk e l'altro (default: quello di produzione, 13s). "
                             "Va alzato quando si fissa un modello con TPM basso: un chunk pesa "
                             "~2700 token, quindi 13s = ~12.400 token/minuto, sopra il limite di "
                             "8K TPM di gpt-oss-120b/qwen3.6-27b. Con 25s si scende a ~6.500/min.")
    parser.add_argument("--salta-verifica-esterna", action="store_true",
                        help="salta la verifica su Open Library/TMDB/MusicBrainz (piu' veloce, ma perdi la metrica principale)")
    parser.add_argument("--solo-misura", action="store_true",
                        help="NON estrae nulla: misura i riferimenti gia' presenti in PRODUZIONE "
                             "per gli episodi del campione. Costo zero (nessuna chiamata LLM, "
                             "nessun consumo di budget) e nessuna scrittura. E' il modo di "
                             "fotografare lo stato attuale prima di cambiare un settaggio, e di "
                             "riconfrontarlo dopo.")
    args = parser.parse_args()

    data_reale = os.environ.get("ILVOLO_DATA_DIR")
    if not data_reale:
        sys.exit("ERRORE: ILVOLO_DATA_DIR non impostata. Esportala come fa il cron "
                 "(vedi scripts/linux/lancia_clasificacion_omv.sh) prima di lanciare.")
    trascrizioni_reali = Path(data_reale) / "trascrizioni"
    riferimenti_reali = Path(data_reale) / "riferimenti"

    if args.campione == "storico":
        campione = list(CAMPIONE_STORICO)
    elif args.campione:
        campione = [d.strip() for d in args.campione.split(",") if d.strip()]
    else:
        campione = scegli_campione(trascrizioni_reali, riferimenti_reali, args.episodi, args.seed)
    mancanti = [d for d in campione if not (trascrizioni_reali / f"{d}.json").exists()]
    if mancanti:
        sys.exit(f"ERRORE: trascrizioni mancanti per {mancanti} — campione non utilizzabile.")
    if not campione:
        sys.exit("Nessun episodio trascritto disponibile.")
    print(f"Campione ({len(campione)} episodi, seed {args.seed}): {', '.join(campione)}", flush=True)

    ground_truth = carica_insieme_riferimento()
    noti = [d for d in campione if d in ground_truth]
    if noti:
        print(f"Ground truth disponibile per {len(noti)} episodi del campione: {', '.join(noti)}", flush=True)
    else:
        print("ATTENZIONE: nessun episodio del campione ha un ground truth — "
              "la recall NON sara' misurabile in questo giro.", flush=True)

    # --solo-misura: fotografa la produzione reale senza estrarre nulla. Nessuna
    # chiamata LLM, nessun consumo di budget, nessuna scrittura: e' il modo di
    # prendere il baseline PRIMA di cambiare un settaggio e di riconfrontarlo dopo.
    if args.solo_misura:
        print("\nMODALITA' SOLO MISURA: nessuna estrazione, nessuna scrittura, "
              "nessun consumo di budget.", flush=True)
        stampa_misure(Path(riferimenti_reali), trascrizioni_reali, campione,
                      ground_truth, args.seed, etichetta="PRODUZIONE ATTUALE")
        return

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
    import trascrivi_e_estrai_clip as tec  # noqa: E402
    from trascrivi_e_estrai_clip import estrai_riferimenti, merge_riferimenti  # noqa: E402

    if args.pausa_chunk is not None:
        tec.CHUNK_SLEEP = args.pausa_chunk
        print(f"Pausa tra chunk: {tec.CHUNK_SLEEP}s (default produzione: 13s)", flush=True)

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
    elif args.provider in ("groq", "cerebras", "gemini", "mistral"):
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
            refs, completo = estrai_riferimenti(testo)
            if not completo:
                # Un episodio incompleto (chunk falliti per budget/rate-limit) NON va
                # contato nella misura di qualita': altrimenti un 429 di Groq durante
                # il test farebbe sembrare il PROVIDER scarso, quando il problema era
                # solo "non ho potuto controllare", non "ho controllato e non c'era
                # nulla". Vedi il bug gemello corretto in produzione lo stesso giorno.
                falliti.append((data_str, "incompleto: chunk falliti per budget/errore"))
                print(f"[{i}/{len(campione)}] {data_str}: INCOMPLETO, escluso dalla misura", flush=True)
                continue
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

    n_ep = len(campione)
    stampa_misure(data_iso / "riferimenti", data_iso / "trascrizioni", campione,
                  ground_truth, args.seed,
                  etichetta=f"provider={provider_reale}, seed={args.seed}")
    print(f"\n  tempo estrazione       : {durata_estrazione:.0f}s  "
          f"({durata_estrazione/max(n_ep,1):.0f}s per episodio)", flush=True)
    if falliti:
        print(f"  EPISODI FALLITI        : {len(falliti)} -> {falliti}", flush=True)

    print("\nBaseline storico (cloud, 982 episodi): 5,0 voci/episodio, 49% confermate.", flush=True)
    print("Confrontare SEMPRE con il baseline preso prima della modifica:", flush=True)
    print("  python3 -u scripts/linux/test_qualita_identificazione.py --solo-misura "
          "--campione storico", flush=True)


if __name__ == "__main__":
    main()
