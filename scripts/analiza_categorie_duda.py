# Analisi di SOLA LETTURA (non modifica MAI nulla) dei casi "dubbio" prodotti da
# verifica_riferimenti_esterna.py. Creato 2026-07-23 su richiesta esplicita
# dell'utente: prima di continuare ad aggiustare a caso, capire con dati reali QUALI
# TIPI di dubbio esistono davvero (mescolati oggi in un unico cajon "dubbio").
#
# Categorie (regole oggettive, verificabili sugli stessi campi gia' salvati nel
# report: punteggio, titolo, autore, match_trovato):
#   A. titolo_esatto_autore_mai_estratto — l'autore originale era vuoto (mai un
#      errore, solo mai identificato) e il titolo trovato e' quasi identico:
#      la formula (titolo 70% + autore 30%) non puo' MAI confermare un titolo
#      perfetto se l'autore e' vuoto (max 0.7 < SOGLIA_ALTA 0.72) — bug di
#      formula, non ambiguita' reale.
#   B. rumore_testo_breve — autore vuoto e titolo trovato solo parzialmente
#      simile: probabile chiacchiera che ha trovato un match debole per caso.
#   C. autore_reale_titolo_non_trovato — l'autore proposto e' vero (trovato tra
#      gli autori del match), ma il titolo specifico non corrisponde bene:
#      il caso INVERSO di Ulisse/Dante, genuinamente ambiguo (puo' essere una
#      canzone/opera reale trascritta foneticamente male).
#   D. opera_tradizionale_senza_autore_umano — autore o titolo in una lista nota
#      di eccezioni (Bibbia, Corano, ecc. — "Dio"/"non specificato" come autore).
#   E. titolo_reale_autore_incompatibile — titolo trovato con alta confidenza,
#      ma NESSUN autore del match somiglia a quello proposto (caso Ulisse/Dante,
#      gia' corretto per i libri in verifica_libro(), ma non ancora per film/
#      musica — questa analisi dice quanti ce ne sono ancora).
#   Z. non_classificato — non rientra chiaramente in nessuna delle precedenti.
#
# Uso: python scripts/analiza_categorie_duda.py [--dataset riferimenti|frammenti|tutti]
#      Stampa il conteggio per categoria + scrive logs/analisi_categorie_duda.json
#      con una manciata di esempi per categoria (per revisione umana).

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verifica_riferimenti_esterna import DATASET_CONFIG, _similarita, _similarita_autore  # noqa: E402
from dati_root import logs_root  # noqa: E402

AUTORI_NON_UMANI = {
    "dio", "dios", "god", "non specificato", "sconosciuto", "n a", "varie", "vario",
    "autori vari", "aa vv", "aavv", "vari",  # trovato 2026-07-23: "Vangelo"/"Autori vari"
}
OPERE_TRADIZIONALI = {"bibbia", "la bibbia", "sacra bibbia", "corano", "il corano", "talmud", "torah"}

SOGLIA_TITOLO_ALTA = 0.90   # categoria A: titolo praticamente identico
SOGLIA_TITOLO_CERTO = 0.85  # categoria E: titolo certo
SOGLIA_AUTORE_ESTRANEO = 0.25  # sotto: l'autore trovato non c'entra
SOGLIA_AUTORE_REALE = 0.6   # sopra: l'autore proposto e' davvero tra quelli trovati
SOGLIA_TITOLO_DEBOLE = 0.6  # sotto: il titolo specifico non e' stato trovato


def _parse_match(match_trovato: str) -> tuple[str, list[str]]:
    """match_trovato ha forma 'Titolo trovato — Autore1, Autore2'."""
    if " — " not in (match_trovato or ""):
        return match_trovato or "", []
    titolo_trovato, autori = match_trovato.split(" — ", 1)
    return titolo_trovato.strip(), [a.strip() for a in autori.split(",") if a.strip()]


def categorizza(voce: dict) -> str:
    titolo = (voce.get("titolo") or "").strip()
    autore = (voce.get("autore") or "").strip()
    titolo_trovato, autori_trovati = _parse_match(voce.get("match_trovato", ""))

    t_norm = titolo.lower()
    a_norm = autore.lower()
    if a_norm in AUTORI_NON_UMANI or t_norm in OPERE_TRADIZIONALI:
        return "D_opera_tradizionale_senza_autore_umano"

    # Trovato 2026-07-23 (caso reale "Brad Pitt"/"Brad Pitt" come film): il modello
    # ha nominato SOLO una persona, non un'opera+creatore distinti — gia' impedito
    # dal confermarsi automaticamente (main() lo cappa sotto SOGLIA_ALTA), ma finiva
    # mescolato in Z senza essere riconosciuto come il SUO proprio pattern.
    if t_norm and a_norm and t_norm == a_norm:
        return "F_persona_confusa_con_opera"

    sim_titolo = _similarita(titolo, titolo_trovato) if titolo_trovato else 0.0
    sim_autore = max((_similarita_autore(autore, a) for a in autori_trovati), default=0.0) if autore else 0.0

    if not autore:
        if sim_titolo >= SOGLIA_TITOLO_ALTA:
            return "A_titolo_esatto_autore_mai_estratto"
        return "B_rumore_testo_breve"

    if sim_titolo >= SOGLIA_TITOLO_CERTO and sim_autore < SOGLIA_AUTORE_ESTRANEO:
        return "E_titolo_reale_autore_incompatibile"

    if sim_autore >= SOGLIA_AUTORE_REALE and sim_titolo < SOGLIA_TITOLO_DEBOLE:
        return "C_autore_reale_titolo_non_trovato"

    return "Z_non_classificato"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=list(DATASET_CONFIG) + ["tutti"], default="tutti")
    args = parser.parse_args()

    datasets = list(DATASET_CONFIG) if args.dataset == "tutti" else [args.dataset]

    conteggi: dict[str, int] = {}
    esempi: dict[str, list[dict]] = {}

    for ds in datasets:
        report_path = logs_root(ROOT) / DATASET_CONFIG[ds]["report"]
        if not report_path.exists():
            print(f"[{ds}] nessun report trovato in {report_path}, salto.")
            continue
        voci = [v for v in json.loads(report_path.read_text(encoding="utf-8")) if v.get("esito") == "dubbio"]
        print(f"[{ds}] {len(voci)} voci 'dubbio' da analizzare...")
        for v in voci:
            cat = categorizza(v)
            conteggi[cat] = conteggi.get(cat, 0) + 1
            esempi.setdefault(cat, [])
            if len(esempi[cat]) < 8:
                esempi[cat].append({"dataset": ds, **v})

    print("\n=== Conteggio per categoria (tutti i dataset richiesti) ===")
    for cat in sorted(conteggi):
        print(f"  {cat}: {conteggi[cat]}")
    totale = sum(conteggi.values())
    print(f"  TOTALE: {totale}")

    out_path = logs_root(ROOT) / "analisi_categorie_duda.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"conteggi": conteggi, "esempi": esempi}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nScritto {out_path} (conteggi + fino a 8 esempi per categoria, SOLA LETTURA — nessun dato modificato).")


if __name__ == "__main__":
    main()
