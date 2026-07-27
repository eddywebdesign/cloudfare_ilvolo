#!/usr/bin/env python3
"""Autotest degli strumenti del banco di prova. Costo zero: nessuna chiamata di rete,
nessun LLM, nessun accesso allo share di produzione.

Perche' esiste (2026-07-27): le funzioni di misura del banco hanno gia' sbagliato due
volte nel giorno stesso in cui sono state scritte — davano una recall dello 0% falsa
perche' confondevano "episodio mai estratto" con "estratto e non ha trovato nulla", e
morivano con UnicodeEncodeError su un carattere non-ASCII in console Windows. Su quelle
misure si decide quale modello mandera' in produzione l'identificazione di 1.100
episodi: se lo strumento di misura e' rotto, la campagna misura rumore.

Uso:
    python3 scripts/linux/test_banco_prova.py

Esce con codice 1 se anche un solo controllo fallisce, cosi' puo' essere messo davanti
a una campagna senza doverne leggere l'output a mano.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_qualita_identificazione as banco  # noqa: E402

falliti: list[str] = []


def verifica(descrizione: str, condizione: bool) -> None:
    """Un singolo controllo. Stampa sempre l'esito: un test che non si vede passare
    non e' una prova (lezione ricorrente di questo progetto)."""
    print(f"  [{'ok ' if condizione else 'NO '}] {descrizione}")
    if not condizione:
        falliti.append(descrizione)


def test_stesso_titolo() -> None:
    """Il confronto tra titoli decide se un'opera del ground truth risulta 'trovata':
    troppo lasco gonfia la recall di tutti i modelli, troppo severo la azzera."""
    print("\n_stesso_titolo (riconoscimento delle opere)")
    verifica("identici", banco._stesso_titolo("Smoke", "Smoke"))
    verifica("articolo in piu'", banco._stesso_titolo("Divina Commedia", "La Divina Commedia"))
    verifica("maiuscole e accenti", banco._stesso_titolo("Ai confini della realta'", "AI CONFINI DELLA REALTA'"))
    verifica("anno tra parentesi", banco._stesso_titolo("Smoke", "Smoke (1995)"))
    verifica("punteggiatura", banco._stesso_titolo("Mamma ho perso l'aereo", "Mamma ho perso l aereo"))
    # I casi negativi contano quanto i positivi: se "Smoke" combacia con "Smoking",
    # un modello che produce parole a caso sembra avere una recall alta.
    verifica("NON confonde Smoke con Smoking", not banco._stesso_titolo("Smoke", "Smoking"))
    verifica("NON confonde due opere diverse", not banco._stesso_titolo("Manhattan", "Wall Street"))
    verifica("titolo vuoto non combacia mai", not banco._stesso_titolo("", "Smoke"))


def _scrivi(dir_rif: Path, dir_tra: Path, data: str, voci: list, testo: str | None) -> None:
    """Costruisce un episodio finto: file riferimenti + (facoltativa) trascrizione."""
    (dir_rif / f"{data}.json").write_text(json.dumps(voci, ensure_ascii=False), encoding="utf-8")
    if testo is not None:
        (dir_tra / f"{data}.json").write_text(
            json.dumps({"segments": [{"text": testo, "end": 100.0}]}, ensure_ascii=False),
            encoding="utf-8")


def test_recall() -> None:
    """La distinzione che mancava e che ha prodotto la prima misura falsa:
    'mai estratto' non e' 'estratto e vuoto'."""
    print("\nmisura_recall (opere reali ritrovate)")
    with tempfile.TemporaryDirectory() as tmp:
        rif = Path(tmp) / "riferimenti"
        tra = Path(tmp) / "trascrizioni"
        rif.mkdir(); tra.mkdir()
        gt = {
            "EP-PIENO": {"opere": [{"categoria": "film", "titolo": "Smoke", "autore": "Wayne Wang"},
                                    {"categoria": "film", "titolo": "Manhattan", "autore": "Woody Allen"}]},
            "EP-VUOTO": {"opere": [{"categoria": "libro", "titolo": "Anna", "autore": "Ammaniti"}]},
            "EP-ASSENTE": {"opere": [{"categoria": "musica", "titolo": "Creep", "autore": "Radiohead"}]},
        }
        _scrivi(rif, tra, "EP-PIENO", [{"titolo": "Smoke", "categoria": "film"}], "parlano di Smoke")
        _scrivi(rif, tra, "EP-VUOTO", [], "una puntata qualsiasi")
        # EP-ASSENTE: nessun file scritto di proposito.

        r = banco.misura_recall(rif, gt, ["EP-PIENO", "EP-VUOTO", "EP-ASSENTE"])
        verifica("episodio mai estratto escluso dal denominatore", r["attese"] == 3)
        verifica("episodio mai estratto segnalato a parte", r["non_estratti"] == ["EP-ASSENTE"])
        verifica("trova l'opera presente", r["trovate"] == 1)
        verifica("episodio estratto ma vuoto conta 0 trovate (non e' escluso)",
                 r["per_episodio"]["EP-VUOTO"]["trovate"] == 0
                 and r["per_episodio"]["EP-VUOTO"]["attese"] == 1)
        verifica("elenca le opere mancate", "Manhattan" in r["per_episodio"]["EP-PIENO"]["mancate"])
        verifica("recall calcolata sul denominatore giusto", abs(r["recall"] - 1 / 3) < 0.001)


def test_ancoraggio() -> None:
    """L'unica misura di precisione non circolare: dice se la voce e' citata DAVVERO
    nell'episodio, cosa che nessun database esterno puo' sapere."""
    print("\nmisura_ancoraggio (voci davvero citate nel testo)")
    with tempfile.TemporaryDirectory() as tmp:
        rif = Path(tmp) / "riferimenti"
        tra = Path(tmp) / "trascrizioni"
        rif.mkdir(); tra.mkdir()
        _scrivi(rif, tra, "EP-1", [
            {"titolo": "Smoke", "autore": "Wayne Wang", "categoria": "film"},
            {"titolo": "Interstellar", "autore": "Christopher Nolan", "categoria": "film"},
        ], "ieri ho rivisto Smoke, che film")
        a = banco.misura_ancoraggio(rif, tra)
        verifica("voce citata nel testo -> ancorata", a["ancorate"] == 1)
        verifica("voce mai nominata -> non ancorata", len(a["non_ancorate"]) == 1)
        verifica("la non ancorata e' quella giusta", a["non_ancorate"][0][2] == "Interstellar")
        verifica("quota calcolata", abs(a["quota"] - 0.5) < 0.001)


def test_config_e_tetto() -> None:
    """Il file di configurazione esiste solo per il test: la sua assenza non deve
    rompere nulla, e i modelli in gara devono avere una pausa coerente col loro TPM."""
    print("\nconfigurazione del banco (file dedicato alla fase di test)")
    cfg = banco.carica_config_banco()
    verifica("il file di configurazione esiste ed e' leggibile", bool(cfg))
    verifica("definisce un tetto per run", isinstance(cfg.get("tetto_token_per_run"), int))
    verifica("il tetto e' sopra il costo del campione (~100K) ma non illimitato",
             100_000 < cfg.get("tetto_token_per_run", 0) <= 400_000)
    modelli = cfg.get("modelli", [])
    verifica("elenca i modelli in gara", len(modelli) >= 4)
    verifica("ogni modello ha provider, nome e pausa",
             all(m.get("provider") and m.get("modello") and m.get("pausa_chunk") for m in modelli))
    lenti = [m for m in modelli if "gpt-oss" in m["modello"]]
    verifica("i modelli gpt-oss (8K TPM) hanno una pausa piu' lunga di 13s",
             bool(lenti) and all(m["pausa_chunk"] > 13 for m in lenti))
    trovato = banco.config_modello(cfg, "groq", "llama-3.1-8b-instant")
    verifica("config_modello trova una coppia esistente", trovato.get("quota_giorno") == 500_000)
    verifica("config_modello non inventa nulla per una coppia assente",
             banco.config_modello(cfg, "groq", "modello-che-non-esiste") == {})
    verifica("senza file di configurazione il banco non si rompe",
             banco.config_modello({}, "groq", "x") == {})


def test_ground_truth() -> None:
    """Il ground truth e' il denominatore di tutta la campagna: un errore qui si
    propaga a ogni modello misurato."""
    print("\ninsieme di riferimento (ground truth letto a mano)")
    gt = banco.carica_insieme_riferimento()
    verifica("carica gli episodi", len(gt) >= 5)
    verifica("ogni episodio dichiara quando e' stato letto per intero",
             all(v.get("letto_per_intero_il") for v in gt.values()))
    verifica("ogni opera ha categoria e titolo",
             all(o.get("categoria") and o.get("titolo")
                 for v in gt.values() for o in v["opere"]))
    verifica("le categorie sono quelle della tassonomia",
             all(o["categoria"] in ("libro", "film", "musica")
                 for v in gt.values() for o in v["opere"]))
    verifica("gli episodi del campione modelli hanno quasi tutti un ground truth",
             sum(1 for d in banco.CAMPIONE_MODELLI if d in gt) >= 5)


def test_archivio_risultati() -> None:
    """Senza archivio, sei run non sono confrontabili se non a memoria."""
    print("\narchivio dei risultati e tabella comparativa")
    with tempfile.TemporaryDirectory() as tmp:
        cartella = Path(tmp) / "banco_prova"
        misure_finte = {
            "metriche": {"tot": 10, "conf": 6, "per_cat": {"film": 6, "musica": 4},
                         "conf_cat": {}, "voci": [("EP-1", "film", "Smoke", "Wayne Wang", True)]},
            "recall": {"recall": 0.5, "trovate": 5, "attese": 10, "mancate": [],
                       "per_episodio": {}, "non_estratti": []},
            "ancoraggio": {"quota": 0.9, "ancorate": 9, "non_ancorate": [], "tot": 10},
        }
        fp = banco.salva_risultato(cartella, "groq", "openai/gpt-oss-120b", ["EP-1"],
                                   misure_finte, 120.0, [], None)
        verifica("il file viene scritto", fp.exists())
        verifica("la barra nel nome del modello non crea sottocartelle", fp.parent == cartella)
        d = json.loads(fp.read_text(encoding="utf-8"))
        verifica("conserva provider e modello", d["provider"] == "groq"
                 and d["modello"] == "openai/gpt-oss-120b")
        verifica("conserva le tre misure",
                 d["recall"] == 0.5 and d["ancoraggio"] == 0.9 and d["confermate_db"] == 6)
        verifica("conserva le voci prodotte, per poterle leggere a mano", len(d["voci"]) == 1)
        # La tabella non deve esplodere ne' su cartella vuota ne' su dati parziali.
        banco.stampa_confronto(cartella)
        banco.stampa_confronto(Path(tmp) / "cartella-inesistente")
        verifica("la tabella comparativa gira senza errori", True)


def main() -> int:
    print("Autotest del banco di prova (nessuna chiamata di rete, nessun LLM)")
    test_stesso_titolo()
    test_recall()
    test_ancoraggio()
    test_config_e_tetto()
    test_ground_truth()
    test_archivio_risultati()
    print("\n" + "=" * 60)
    if falliti:
        print(f"FALLITI {len(falliti)} controlli:")
        for f in falliti:
            print(f"  - {f}")
        return 1
    print("Tutti i controlli passati: gli strumenti di misura sono affidabili.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
