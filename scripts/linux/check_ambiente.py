#!/usr/bin/env python3
"""Controlla che la macchina su cui gira abbia DAVVERO tutto quello che le serve.

Perche' esiste (2026-07-28). Il banco di prova gira sull'HP14, la pipeline gira su
OMV, e per settimane nessuno ha confrontato i due ambienti. Risultato: la chiave di
Google Books non era mai stata messa su OMV, quindi in produzione quel controllo usciva
subito con "chiave assente" mentre sul banco funzionava. Nessun errore, nessun log,
nessuna misura capace di accorgersene - la stessa famiglia di guasti che in questo
progetto e' costata piu' tempo di qualunque altra cosa.

Non verifica che un file esista: verifica che l'ARCHIVIO RISPONDA, con una chiamata
reale e un risultato atteso. Un file di chiave presente non dice che la chiave sia
valida, e una chiave valida non dice che l'archivio sia raggiungibile da qui.

Uso, sulla macchina da controllare:
    export ILVOLO_DATA_DIR=... ILVOLO_LOGS_DIR=...
    python3 scripts/linux/check_ambiente.py

Va eseguito su OGNI macchina che partecipa alla pipeline e i risultati vanno
confrontati fra loro: e' la differenza fra due ambienti a mordere, non lo stato
assoluto di uno solo. Esce 1 se qualcosa di essenziale manca.

NIENTE caratteri fuori ASCII nell'output e nessun contenuto di chiave stampato mai.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

essenziali_mancanti = []


def esito(nome: str, ok: bool | None, dettaglio: str = "", essenziale: bool = True) -> None:
    """ok=None significa 'non ho potuto chiedere': non e' un successo e non e' una
    bocciatura, e va distinto da entrambi - vale qui come nel resto della pipeline."""
    segno = "??" if ok is None else ("ok" if ok else "NO")
    print(f"  [{segno}] {nome:34} {dettaglio}", flush=True)
    if ok is False and essenziale:
        essenziali_mancanti.append(nome)


def controlla_percorsi() -> None:
    print("\nPERCORSI DEI DATI (se cadono sulla copia locale, ogni misura e' falsa)", flush=True)
    from dati_root import dati_root, logs_root
    # ILVOLO_DATA_DIR e' obbligatoria: senza, si legge e si scrive la copia locale del
    # repo e ogni misura e' falsa. ILVOLO_LOGS_DIR no: logs_root() sa ricavare la
    # cartella come sorella di data/, che e' corretto su OMV e in locale; serve solo
    # dove il mount non rispecchia la struttura reale (K16 via CIFS). Cio' che conta
    # non e' che la variabile sia impostata, ma DOVE si finisce davvero.
    valore = os.environ.get("ILVOLO_DATA_DIR", "")
    esito("ILVOLO_DATA_DIR", bool(valore),
          valore or "NON IMPOSTATA: si legge e si scrive la copia locale del repo")
    log_var = os.environ.get("ILVOLO_LOGS_DIR", "")
    esito("ILVOLO_LOGS_DIR", True,
          log_var or "non impostata, ricavata come sorella di data/", essenziale=False)
    for nome, percorso in (("cartella dati", dati_root(ROOT)), ("cartella log", logs_root(ROOT))):
        dentro_al_repo = str(percorso).startswith(str(ROOT))
        esito(nome, percorso.exists() and not dentro_al_repo,
              str(percorso) + ("  <- DENTRO AL REPO, non e' la cartella condivisa" if dentro_al_repo else ""))
    for sotto in ("riferimenti", "trascrizioni"):
        d = dati_root(ROOT) / sotto
        n = len(list(d.glob("*.json"))) if d.exists() else 0
        esito(f"file in {sotto}", d.exists() and n > 0, f"{n} file")


def controlla_chiavi() -> None:
    print("\nCHIAVI (presenza del file: necessaria, non sufficiente)", flush=True)
    import verifica_riferimenti_esterna as v
    chiavi = [("TMDB", v.TMDB_KEY_FILE, True),
              ("Google Books", v.GOOGLE_BOOKS_KEY_FILE, False),
              ("Credits.fm", v.CREDITS_FM_KEY_FILE, False)]
    for nome, fp, essenziale in chiavi:
        c = fp.exists() and bool(fp.read_text(encoding="utf-8").strip())
        esito(f"chiave {nome}", c, f"{fp.name}" + ("" if c else " ASSENTE"), essenziale)


def controlla_archivi() -> None:
    """Una chiamata vera per archivio, con un risultato atteso noto. Non basta un 200:
    un 200 con zero risultati su un titolo notissimo e' un archivio che non serve."""
    print("\nARCHIVI ESTERNI (chiamata reale, non solo raggiungibilita')", flush=True)
    import verifica_riferimenti_esterna as v

    prove = [
        ("Open Library", lambda: v.verifica_libro("Il nome della rosa", "Umberto Eco")[0]),
        ("TMDB", lambda: v.verifica_film("Il padrino", "Francis Ford Coppola", v._tmdb_key())[0]),
        ("MusicBrainz", lambda: v.verifica_musica("Chasing Cars", "Snow Patrol")[0]),
        ("Wikidata opere", lambda: v.cerca_wikidata("La traviata", "Giuseppe Verdi", "musica")[0]),
        ("Wikidata autori", lambda: v.verifica_autore("Giacomo Leopardi", "libro")[0]),
        ("Google Books", lambda: v.cerca_google_books("Il nome della rosa", "Umberto Eco")[0]),
        ("Credits.fm", lambda: v.cerca_credits_fm("Chasing Cars", "Snow Patrol")[0]),
    ]
    for nome, prova in prove:
        try:
            p = prova()
        except Exception as e:
            esito(nome, None, f"eccezione: {str(e)[:60]}", essenziale=False)
            continue
        if p < 0:
            esito(nome, None, "non ho potuto chiedere (rete, quota o chiave assente)", essenziale=False)
        else:
            esito(nome, p >= v.SOGLIA_ALTA, f"punteggio {p:.2f} su un caso notissimo",
                  essenziale=nome in ("Open Library", "TMDB", "MusicBrainz"))


def controlla_modelli() -> None:
    print("\nPROVIDER DEL MODELLO (solo disponibilita', nessun token speso)", flush=True)
    try:
        import llm_multi
    except Exception as e:
        esito("import llm_multi", False, str(e)[:60])
        return
    for p in ("groq", "cerebras", "gemini", "mistral"):
        try:
            usati = llm_multi.token_usati_oggi(p)
            margine = llm_multi.PROVIDER_CONFIG[p]["margine"]
            esito(f"budget {p}", usati < margine, f"{usati:,} usati su {margine:,}", essenziale=False)
        except Exception as e:
            esito(f"budget {p}", None, str(e)[:50], essenziale=False)
    try:
        scelto = llm_multi.provider_disponibile()
        esito("provider disponibile", scelto is not None, str(scelto))
    except Exception as e:
        esito("provider disponibile", None, str(e)[:60])


def main() -> None:
    print("=" * 72, flush=True)
    print(f"CONTROLLO AMBIENTE - {socket.gethostname()} - {time.strftime('%Y-%m-%d %H:%M')}", flush=True)
    print("=" * 72, flush=True)
    controlla_percorsi()
    controlla_chiavi()
    controlla_archivi()
    controlla_modelli()

    print("\n" + "=" * 72, flush=True)
    if essenziali_mancanti:
        print(f"MANCA QUALCOSA DI ESSENZIALE: {', '.join(essenziali_mancanti)}", flush=True)
        print("Questa macchina NON puo' eseguire la pipeline con risultati validi.", flush=True)
        sys.exit(1)
    print("Tutto l'essenziale risponde su questa macchina.", flush=True)
    print("Confrontare con l'esito sulle ALTRE macchine: e' la differenza che morde.", flush=True)


if __name__ == "__main__":
    main()
