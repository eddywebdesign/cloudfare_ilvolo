#!/usr/bin/env python3
"""Banco di prova della VERIFICA su database esterni. Zero budget LLM: interroga solo
Open Library, Google Books, TMDB, MusicBrainz e Wikidata.

Perche' esiste (2026-07-28): e' misurato che il collo di bottiglia
dell'identificazione non e' il modello ma la verifica a valle - i modelli producevano
gia' i candidati giusti e li scartavamo noi. Ma finora ogni rinforzo della verifica e'
stato validato a mano, su casi scritti solo dentro un messaggio di commit. Il giorno
dopo un cambio d'arita' ha disattivato in silenzio l'intero fallback multi-database:
nessun errore, nessun log, semplicemente risultati peggiori. Questo banco rende quel
tipo di guasto visibile in un minuto e senza spendere un token.

I DUE GRUPPI SI LEGGONO INSIEME. Alzare la conferma e' banale (basta abbassare le
soglie); alzarla senza far entrare rumore e' l'unico risultato che conta. Un rinforzo
che recupera opere vere ma ammette anche un solo caso di rumore non e' un
miglioramento: quella voce entra nell'archivio col bollo di "verificato".

Uso:
    python3 scripts/linux/test_verifica_esterna.py                 # tutti i gruppi
    python3 scripts/linux/test_verifica_esterna.py --gruppo vere   # solo uno
    python3 scripts/linux/test_verifica_esterna.py --confronta     # solo la storia

Esce con codice 1 se un caso e' peggiorato rispetto al run precedente archiviato,
cosi' puo' essere messo davanti a un cambio di codice senza doverne leggere l'output.

NIENTE caratteri fuori ASCII nell'output: la console Windows (cp1252) va in
UnicodeEncodeError e il banco muore a meta' misura invece di dare i numeri.
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verifica_riferimenti_esterna import (  # noqa: E402
    SOGLIA_ALTA, SOGLIA_BASSA, giudica_voce, completa_autore_dal_db,
    verifica_categorie_incrociate, deve_incrociare, _tmdb_key,
)
from dati_root import logs_root  # noqa: E402

INSIEME = Path(__file__).resolve().parent / "insieme_verifica.json"


def carica_insieme() -> dict:
    return json.loads(INSIEME.read_text(encoding="utf-8"))


def ascii_sicuro(s: str) -> str:
    """Ripulisce il testo che ARRIVA DALLE API prima di stamparlo.

    Non basta evitare i caratteri non-ASCII nel codice: il 2026-07-28 il banco e'
    morto a meta' misura su un diacritico combinante dentro un'etichetta restituita da
    un database (UnicodeEncodeError, console Windows cp1252). Un guasto della console
    non deve poter interrompere una misura gia' pagata."""
    return (s or "").encode("ascii", "replace").decode("ascii")


def esito_di(punteggio: float) -> str:
    """Le stesse tre fasce della produzione, cosi' il banco parla la sua lingua."""
    if punteggio < 0:
        return "non chiedibile"
    if punteggio >= SOGLIA_ALTA:
        return "confermato"
    return "dubbio" if punteggio >= SOGLIA_BASSA else "scartato"


def prova_opere(casi: list, atteso: str, tmdb_key: str, etichetta: str) -> list[dict]:
    """Esegue un gruppo di opere contro la catena REALE (giudica_voce, la stessa che
    gira in produzione) e ritorna un record per caso."""
    print(f"\n{etichetta} - attese: {atteso.upper()}")
    risultati = []
    for c in casi:
        titolo, autore, categoria = c["titolo"], c.get("autore", ""), c["categoria"]
        try:
            punteggio, match, _cop, _sub, _url = giudica_voce(titolo, autore, categoria, tmdb_key)
        except Exception as e:
            # Un errore qui non e' "l'opera non esiste": e' il banco che non ha potuto
            # chiedere. Va segnalato come tale, mai contato come esito.
            print(ascii_sicuro(f"  [??] {titolo!r}: ERRORE {e}"))
            risultati.append({"titolo": titolo, "autore": autore, "categoria": categoria,
                              "atteso": atteso, "esito": "errore", "punteggio": None,
                              "match": str(e), "ok": None})
            continue

        esito = esito_di(punteggio)
        # "non ho potuto chiedere" non e' un esito: contarlo come successo nel gruppo
        # rumore (dove basta NON confermare) farebbe sembrare perfetto un banco che ha
        # solo perso la rete. Stessa distinzione del bug Groq, applicata alla misura.
        ok = None if esito == "non chiedibile" else (
            (esito == "confermato") if atteso == "confermato" else (esito != "confermato"))
        segno = "?? " if ok is None else ("ok " if ok else "NO ")
        print(ascii_sicuro(f"  [{segno}] {titolo!r}"
                          + (f" / {autore}" if autore else " / (autore assente)")
                          + f" -> {esito} ({punteggio:.2f}) {match[:70]}"))
        risultati.append({"titolo": titolo, "autore": autore, "categoria": categoria,
                          "atteso": atteso, "esito": esito, "punteggio": round(punteggio, 3),
                          "match": match[:120], "ok": ok})
    return risultati


def prova_completamento_autore(casi: list, tmdb_key: str) -> list[dict]:
    """I casi 'titolo senza autore' non bastano confermati: la voce e' chiusa solo se
    il database sa anche DIRE chi e' l'autore. Misurato a parte perche' e' un secondo
    risultato, non lo stesso (la musica ne e' esclusa deliberatamente)."""
    senza_autore = [c for c in casi if not c.get("autore", "").strip()]
    if not senza_autore:
        return []
    print("\nCOMPLETAMENTO AUTORE (titolo confermato, autore mai estratto)")
    risultati = []
    for c in senza_autore:
        titolo, categoria = c["titolo"], c["categoria"]
        try:
            trovato = completa_autore_dal_db(titolo, categoria, tmdb_key)
        except Exception as e:
            print(ascii_sicuro(f"  [??] {titolo!r}: ERRORE {e}"))
            continue
        atteso_pieno = categoria != "musica"  # la musica non si completa, per scelta
        ok = bool(trovato) if atteso_pieno else not trovato
        if trovato:
            spiegazione = trovato
        elif atteso_pieno:
            spiegazione = "NESSUN AUTORE (il database dovrebbe saperlo)"
        else:
            spiegazione = "nessun autore, come atteso per la musica"
        print(ascii_sicuro(f"  [{'ok ' if ok else 'NO '}] {titolo!r} ({categoria}) -> {spiegazione}"))
        risultati.append({"titolo": titolo, "categoria": categoria,
                          "autore_trovato": trovato, "ok": ok})
        time.sleep(0.35)
    return risultati


def prova_multicategoria(casi: list, tmdb_key: str) -> list[dict]:
    """Un nome che esiste in due archivi va riportato in entrambi, ciascuno nella sua
    categoria (regola dell'utente, 2026-07-28). Qui si verifica che la categoria attesa
    in piu' venga davvero trovata: se non la troviamo, la voce gemella non nascera'."""
    print("\nMULTICATEGORIA (lo stesso nome esiste in piu' di un archivio)")
    risultati = []
    for c in casi:
        titolo, autore = c["titolo"], c.get("autore", "")
        estratta, attesa = c["categoria_estratta"], c["attesa_anche"]
        try:
            # Si passa PRIMA dalla stessa guardia della produzione: i casi negativi
            # non falliscono perche' l'altro archivio non ha il titolo (ce l'ha:
            # esistono libri intitolati "Pink Floyd"), ma perche' non si deve nemmeno
            # andare a cercare. Chiamare direttamente la ricerca incrociata misurerebbe
            # la cosa sbagliata.
            punteggio, match, _c, _s, _u = giudica_voce(titolo, autore, estratta, tmdb_key)
            if deve_incrociare(titolo, autore, punteggio >= SOGLIA_ALTA, match):
                trovate = verifica_categorie_incrociate(titolo, autore, estratta, tmdb_key)
            else:
                trovate = []
        except Exception as e:
            print(ascii_sicuro(f"  [??] {titolo!r}: ERRORE {e}"))
            continue
        categorie = [t[0] for t in trovate]
        ok = (attesa in categorie) if attesa else (categorie == [])
        if not ok and attesa:
            # Prima di dichiarare una regressione: la categoria attesa non e' stata
            # trovata perche' non c'e', o perche' in quel momento un archivio non ha
            # risposto? Senza questa distinzione un 503 di Google Books somiglia a un
            # peggioramento e fa fallire il gate per niente.
            p_att, _m, _c, _s, _u = giudica_voce(titolo, autore, attesa, tmdb_key)
            if p_att < 0:
                ok = None
        segno = "?? " if ok is None else ("ok " if ok else "NO ")
        print(ascii_sicuro(f"  [{segno}] {titolo!r} (estratta: {estratta}) "
                           f"-> trovata anche in: {categorie or 'nessun altro archivio'} "
                           f"(attesa: {attesa})"))
        risultati.append({"titolo": titolo, "autore": autore, "categoria": estratta,
                          "attesa_anche": attesa, "trovate": categorie, "ok": ok})
    return risultati


def prova_autori(casi: list, tmdb_key: str) -> list[dict]:
    """Le voci di solo autore ('adesso vi leggo una cosa di Erri De Luca'): il nome
    deve reggere come autore reale, e un saluto qualunque non deve diventare un poeta."""
    print("\nSOLO AUTORE (nessun titolo pronunciato)")
    risultati = []
    for c in casi:
        autore, categoria, atteso = c["autore"], c["categoria"], c["atteso"]
        try:
            punteggio, match, _cop, _sub, url = giudica_voce("", autore, categoria, tmdb_key)
        except Exception as e:
            print(ascii_sicuro(f"  [??] {autore!r}: ERRORE {e}"))
            continue
        esito = esito_di(punteggio)
        ok = None if esito == "non chiedibile" else (
            (esito == "confermato") if atteso == "confermato" else (esito != "confermato"))
        # Un autore confermato senza link non e' un risultato completo: il link e'
        # tutto cio' che quella voce potra' mostrare, non avendo un titolo.
        if atteso == "confermato" and esito == "confermato" and not url:
            ok = False
        segno = "?? " if ok is None else ("ok " if ok else "NO ")
        print(ascii_sicuro(f"  [{segno}] {autore!r} ({categoria}) -> {esito} "
                          f"({punteggio:.2f}) {match[:60]}"))
        risultati.append({"autore": autore, "categoria": categoria, "atteso": atteso,
                          "esito": esito, "punteggio": round(punteggio, 3),
                          "link": url, "ok": ok})
    return risultati


def chiave(r: dict) -> str:
    """Identificatore stabile di un caso, per confrontare due run."""
    return f"{r.get('titolo', '')}|{r.get('autore', '')}|{r.get('categoria', '')}"


def confronta_col_precedente(cartella: Path, adesso: dict) -> list[str]:
    """Elenca i casi PEGGIORATI rispetto all'ultimo run archiviato.

    E' la parte che il 2026-07-27 mancava: la misura era stata presa davvero, ma
    nessuno poteva rifarla il giorno dopo per accorgersi che era caduta."""
    precedenti = sorted(cartella.glob("*.json"))
    if not precedenti:
        print("\n(nessun run precedente archiviato: questo diventa il riferimento)")
        return []
    prima = json.loads(precedenti[-1].read_text(encoding="utf-8"))
    per_chiave_prima = {chiave(r): r for gruppo in ("vere", "rumore", "autori", "multicategoria")
                        for r in prima.get("risultati", {}).get(gruppo, [])}
    peggiorati = []
    migliorati = []
    for gruppo in ("vere", "rumore", "autori", "multicategoria"):
        for r in adesso["risultati"].get(gruppo, []):
            vecchio = per_chiave_prima.get(chiave(r))
            if vecchio is None or vecchio.get("ok") is None or r.get("ok") is None:
                continue
            if vecchio["ok"] and not r["ok"]:
                peggiorati.append(f"{gruppo}: {chiave(r)} (era ok, ora {r['esito']})")
            elif not vecchio["ok"] and r["ok"]:
                migliorati.append(f"{gruppo}: {chiave(r)} (era {vecchio['esito']}, ora ok)")
    print(f"\nConfronto con {precedenti[-1].name}:")
    for m in migliorati:
        print(f"  MEGLIO  {m}")
    for p in peggiorati:
        print(f"  PEGGIO  {p}")
    if not migliorati and not peggiorati:
        print("  nessun caso cambiato")
    return peggiorati


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gruppo",
                        choices=["vere", "rumore", "autori", "multicategoria", "tutti"],
                        default="tutti")
    parser.add_argument("--confronta", action="store_true",
                        help="non interroga nulla: mostra solo la storia dei run archiviati")
    parser.add_argument("--non-archiviare", action="store_true",
                        help="esegue senza scrivere il risultato (prova a vuoto)")
    args = parser.parse_args()

    cartella = logs_root(ROOT) / "banco_verifica"
    insieme = carica_insieme()
    tmdb_key = _tmdb_key()
    if not tmdb_key:
        print("ATTENZIONE: chiave TMDB assente (~/TMDB API.txt): i casi film non sono misurabili.")

    if args.confronta:
        for fp in sorted(cartella.glob("*.json")):
            d = json.loads(fp.read_text(encoding="utf-8"))
            s = d.get("sommario", {})
            print(f"  {fp.name}: vere {s.get('vere_ok')}/{s.get('vere_tot')}, "
                  f"rumore ammesso {s.get('rumore_ammesso')}/{s.get('rumore_tot')}, "
                  f"autori {s.get('autori_ok')}/{s.get('autori_tot')}")
        return

    inizio = time.time()
    risultati = {"vere": [], "rumore": [], "autori": [], "completamento_autore": [],
                 "multicategoria": []}

    if args.gruppo in ("vere", "tutti"):
        risultati["vere"] = prova_opere(insieme["vere"], "confermato", tmdb_key,
                                        "GRUPPO VERE - opere reali che la pipeline scartava")
        risultati["completamento_autore"] = prova_completamento_autore(insieme["vere"], tmdb_key)
    if args.gruppo in ("rumore", "tutti"):
        risultati["rumore"] = prova_opere(insieme["rumore"], "scartato", tmdb_key,
                                          "GRUPPO RUMORE - non-opere prese dai run reali")
    if args.gruppo in ("autori", "tutti"):
        risultati["autori"] = prova_autori(insieme["autori"], tmdb_key)
    if args.gruppo in ("multicategoria", "tutti"):
        risultati["multicategoria"] = prova_multicategoria(insieme["multicategoria"], tmdb_key)

    vere_ok = sum(1 for r in risultati["vere"] if r["ok"])
    rumore_ammesso = sum(1 for r in risultati["rumore"] if r["ok"] is False)
    autori_ok = sum(1 for r in risultati["autori"] if r["ok"])
    compl_ok = sum(1 for r in risultati["completamento_autore"] if r["ok"])
    multi_ok = sum(1 for r in risultati["multicategoria"] if r["ok"])
    multi_tot = sum(1 for r in risultati["multicategoria"] if r["ok"] is not None)
    sommario = {
        "vere_ok": vere_ok, "vere_tot": len(risultati["vere"]),
        "rumore_ammesso": rumore_ammesso, "rumore_tot": len(risultati["rumore"]),
        "autori_ok": autori_ok, "autori_tot": len(risultati["autori"]),
        "completamento_ok": compl_ok, "completamento_tot": len(risultati["completamento_autore"]),
        "multicategoria_ok": multi_ok, "multicategoria_tot": multi_tot,
    }

    print("\n" + "=" * 72)
    print("SOMMARIO")
    print("=" * 72)
    print(f"  opere vere recuperate  : {vere_ok}/{len(risultati['vere'])}")
    print(f"  RUMORE AMMESSO         : {rumore_ammesso}/{len(risultati['rumore'])}"
          f"   (deve restare 0: una voce sbagliata entra nell'archivio come 'verificata')")
    print(f"  solo autore            : {autori_ok}/{len(risultati['autori'])}")
    print(f"  autore completato dal DB: {compl_ok}/{len(risultati['completamento_autore'])}")
    print(f"  multicategoria         : {multi_ok}/{multi_tot}"
          f"   (lo stesso nome riportato in entrambi gli archivi)")
    print(f"  tempo                  : {time.time() - inizio:.0f}s")

    adesso = {"data": str(date.today()), "ora": datetime.now().strftime("%H:%M"),
              "sommario": sommario, "risultati": risultati}
    peggiorati = confronta_col_precedente(cartella, adesso)

    if not args.non_archiviare:
        cartella.mkdir(parents=True, exist_ok=True)
        nome = f"{date.today()}_{datetime.now().strftime('%H%M')}.json"
        (cartella / nome).write_text(json.dumps(adesso, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"\nArchiviato in {cartella / nome}")

    if peggiorati:
        sys.exit(1)


if __name__ == "__main__":
    main()
