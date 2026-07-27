# Verifica a posteriori le voci di data/riferimenti/*.json (categoria libro/film/musica)
# contro un database ESTERNO reale (Open Library per i libri, TMDB per i film,
# MusicBrainz per la musica) invece che con un altro LLM che giudica se stesso -
# nessuno dei tre servizi richiede pagamento (vedi project_costo_zero), verificato
# il 2026-07-22 con ricerche reali sui limiti free-tier attuali:
#   - Open Library Search API: nessuna chiave, ~3 richieste/secondo se ci si
#     identifica con uno User-Agent descrittivo (fatto qui).
#   - TMDB: chiave gratuita gia' presente in ~/'TMDB API.txt', ~40 richieste/secondo.
#   - MusicBrainz: nessuna chiave, ma va rispettato RIGOROSAMENTE 1 richiesta/secondo
#     con uno User-Agent descrittivo, altrimenti risponde 503.
#
# Il confronto e' per SIMILARITA' (difflib, come gia' fatto altrove nel progetto),
# non per uguaglianza esatta: un titolo trovato dalla trascrizione puo' avere rumore
# aggiunto (es. "Divina Commedia" seguito da altro testo di chiacchiera attaccato per
# errore) che romperebbe un confronto esatto pur essendo un riferimento vero.
#
# Automatico per il grosso, come richiesto: sopra una soglia alta la voce viene
# marcata "confermato_esterno": true (nessuna azione ulteriore necessaria); sotto una
# soglia bassa "confermato_esterno": false E aggiunta al report per revisione umana;
# in mezzo, stessa cosa ma con "esito": "dubbio" nel report per distinguerla da un
# probabile falso positivo netto. NON cancella MAI nulla da solo.
#
# Uso: python scripts/verifica_riferimenti_esterna.py [data1 data2 ...]
#      senza argomenti: controlla tutte le voci non ancora verificate in data/riferimenti/
#      --dataset frammenti: stessa identica verifica ma su data/frammenti/*.json,
#      esteso 2026-07-23 su richiesta esplicita dell'utente ("TUTTI i frammenti
#      devono passare per questo database") - i riferimento_libro/film/musica dentro
#      i frammenti (assegnati da classifica_frammenti(), ora anche via Ollama) avevano
#      SOLO l'ancoraggio al testo come controllo, mai un riscontro con un database
#      esterno reale come i riferimenti bibliografici/filmografici separati.

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dati_root import dati_root, logs_root  # noqa: E402

# Mappa (cartella dati, nome campo categoria, {valore campo -> categoria libro/film/musica}, nome report).
# "riferimenti" ha gia' il campo "categoria" con i valori giusti; "frammenti" ha "tipo"
# con prefisso "riferimento_" e altri tipi (aneddoto/riflessione/...) da ignorare.
DATASET_CONFIG = {
    "riferimenti": {
        "dir": "riferimenti", "campo": "categoria",
        "mappa": {"libro": "libro", "film": "film", "musica": "musica"},
        "report": "riferimenti_non_confermati.json",
    },
    "frammenti": {
        "dir": "frammenti", "campo": "tipo",
        "mappa": {"riferimento_libro": "libro", "riferimento_film": "film", "riferimento_musica": "musica"},
        "report": "frammenti_riferimenti_non_confermati.json",
    },
}

TMDB_KEY_FILE = Path.home() / "TMDB API.txt"
GOOGLE_BOOKS_KEY_FILE = Path.home() / "API_Google_Books.txt"
USER_AGENT = "IlVoloDelMattinoArchivio/1.0 (uso non commerciale, archivio fan Radio Deejay)"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
WIKIDATA_SLEEP = 0.35   # Wikidata rifiuta le richieste troppo ravvicinate rispondendo
# con contenuto non-JSON (misurato il 2026-07-27): mai chiamare .json() senza rete.
GOOGLE_BOOKS_TENTATIVI = 3  # i 503 sono frequenti e intermittenti, non definitivi

# Parole spia nella descrizione italiana di Wikidata, per categoria attesa. Servono
# perche' la ricerca grezza restituisce l'entita' piu' POPOLARE col quel nome, non
# quella del tipo giusto: misurato il 2026-07-27, "Fantozzi" restituiva la persona
# Paolo Villaggio, "Aida" restituiva "prenome", "Il libro della giungla" il film del
# 2016 invece del libro. Con questo filtro tornano tutti corretti.
WIKIDATA_SPIE = {
    "libro": ("libro", "romanzo", "poesia", "poema", "saggio", "racconto", "raccolta",
              "opera letteraria", "testo", "commedia", "tragedia", "fumetto", "romanzo grafico",
              "book", "novel", "poem", "essay", "short story"),
    "film": ("film", "serie televisiva", "cortometraggio", "documentario", "miniserie",
             "programma televisivo", "sitcom", "movie", "television series", "tv series"),
    "musica": ("canzone", "brano", "opera lirica", "composizione", "album", "singolo",
               "sinfonia", "aria", "musica", "song", "album", "opera", "composition"),
}

SOGLIA_ALTA = 0.72   # sopra: confermato automaticamente
SOGLIA_BASSA = 0.45  # sotto: quasi certamente falso positivo, segnalato come tale
MUSICBRAINZ_SLEEP = 1.05  # poco sopra 1 richiesta/secondo per margine di sicurezza


def _normalizza(s: str) -> str:
    return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()


def _similarita(a: str, b: str) -> float:
    a, b = _normalizza(a), _normalizza(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _similarita_autore(a: str, b: str) -> float:
    """Similarita' per NOMI DI PERSONA, per parole intere, non per carattere.

    Trovato 2026-07-23 con un test reale contro l'API vera di Open Library (caso
    "Ulisse"/"Dante Alighieri"): _similarita() carattere-per-carattere da'
    "Dante Alighieri" vs "Antonino Pagliaro" (l'autore VERO di quella traduzione,
    zero parole in comune) = 0.5 di similarita' — abbastanza da far CONFERMARE
    automaticamente (punteggio finale 0.85, ben sopra SOGLIA_ALTA) un'attribuzione
    completamente sbagliata, solo perche' due nomi italiani condividono lettere/
    sillabe comuni per caso. La similarita' a caratteri e' giusta per i TITOLI
    (tollera rumore di trascrizione, parole in piu' attaccate) ma sbagliata per i
    NOMI DI PERSONA, dove quello che conta e' se condividono PAROLE intere (nome/
    cognome), non lettere sparse. Qui il punteggio e' frazione di parole in comune
    sul piu' lungo dei due insiemi (0.0 se nessuna parola condivisa, anche se le
    lettere si somigliano)."""
    a_norm, b_norm = _normalizza(a), _normalizza(b)
    if not a_norm or not b_norm:
        return 0.0
    parole_a, parole_b = set(a_norm.split()), set(b_norm.split())
    comuni = parole_a & parole_b
    if not comuni:
        return 0.0
    return len(comuni) / max(len(parole_a), len(parole_b))


def _tmdb_key() -> str:
    if not TMDB_KEY_FILE.exists():
        print(f"Errore: chiave TMDB non trovata in {TMDB_KEY_FILE}")
        sys.exit(1)
    return TMDB_KEY_FILE.read_text(encoding="utf-8").strip()


SOGLIA_TITOLO_CERTO = 0.85  # sopra: il titolo esiste davvero come opera reale
SOGLIA_AUTORE_ESTRANEO = 0.25  # sotto: l'autore proposto non c'entra nulla col titolo trovato
SOGLIA_TITOLO_SENZA_AUTORE = 0.90  # quando l'autore originale e' vuoto: sopra = titolo confermato
# da solo, sotto = probabile rumore di chiacchiera (vedi analisi_categorie_duda.py,
# categorie A/B: 1248/1743 casi "dubbio" del 2026-07-23 avevano l'autore originale
# vuoto — la formula titolo*0.7+autore*0.3 non puo' MAI superare 0.7 con autore
# vuoto, quindi finiva SEMPRE in "dubbio" anche quando il titolo era perfetto (caso
# A) o quando era chiaramente rumore (caso B). L'autore vuoto non e' un errore -
# semplicemente non e' mai stato tentato - quindi va giudicato SOLO sul titolo.


def _google_books_key() -> str:
    """Chiave Google Books, opzionale: se manca, quel database viene semplicemente
    saltato invece di far fallire tutta la verifica."""
    if not GOOGLE_BOOKS_KEY_FILE.exists():
        return ""
    return GOOGLE_BOOKS_KEY_FILE.read_text(encoding="utf-8").strip()


def cerca_google_books(titolo: str, autore: str) -> tuple[float, str, str]:
    """Cerca un libro su Google Books. Ritorna (punteggio, descrizione, copertina).

    Perche' serve accanto a Open Library (misurato il 2026-07-27 sui casi reali che
    la pipeline scartava): Open Library e' debole sull'editoria italiana e dava ZERO
    risultati per "Cinquanta sfumature di grigio" e "I fili invisibili della natura",
    e per "Anna" di Ammaniti restituiva un libro sudafricano omonimo. Google Books
    trova correttamente tutti e tre.

    ⚠️ Punteggio -1.0 = NON HO POTUTO CHIEDERE (rete, 503, chiave assente), che NON
    e' "il libro non esiste": il chiamante non deve mai scartare una voce per questo.
    E' la stessa distinzione che mancava nel bug Groq del 27/07, dove "chunk fallito"
    veniva scritto come "nessun riferimento trovato". I 503 di Google Books sono
    frequenti e intermittenti, quindi si riprova con attesa crescente."""
    key = _google_books_key()
    if not key:
        return -1.0, "chiave Google Books assente", ""
    query = f'intitle:"{titolo}"' + (f' inauthor:"{autore}"' if autore else "")
    risposta = None
    for tentativo in range(GOOGLE_BOOKS_TENTATIVI):
        try:
            risposta = requests.get(GOOGLE_BOOKS_API, timeout=20,
                                    params={"q": query, "maxResults": 5, "key": key})
        except Exception as e:
            return -1.0, f"errore rete Google Books: {e}", ""
        if risposta.status_code == 200:
            break
        time.sleep(1.5 * (tentativo + 1))
    else:
        return -1.0, f"Google Books non raggiungibile (HTTP {risposta.status_code})", ""

    items = risposta.json().get("items", [])
    if not items:
        return 0.0, "nessun risultato Google Books", ""
    migliore = (0.0, "", "")
    for it in items:
        vi = it.get("volumeInfo", {})
        sim_titolo = _similarita(titolo, vi.get("title", ""))
        autori = vi.get("authors", []) or []
        sim_autore = max((_similarita_autore(autore, a) for a in autori), default=0.0)
        # Stessa formula di verifica_libro: il titolo pesa 70%, l'autore 30%. Con
        # autore mai estratto si giudica sul solo titolo, come gia' fa Open Library.
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3 if autore else sim_titolo
        if punteggio > migliore[0]:
            img = (vi.get("imageLinks") or {}).get("thumbnail", "")
            migliore = (punteggio, f"{vi.get('title','')} - {', '.join(autori[:2])}", img)
    return migliore


def cerca_wikidata(titolo: str, autore: str, categoria: str) -> tuple[float, str, str]:
    """Cerca un'opera su Wikidata, filtrando per la categoria attesa.

    E' l'unico database provato che copre con lo stesso endpoint libri, film, serie,
    opere liriche e canzoni, gratis e senza chiave — e copre proprio i buchi degli
    altri tre (misurato il 2026-07-27): l'opera lirica (MusicBrainz rispondeva con
    una singola aria per "Aida" e col Rigoletto per "La traviata"), le poesie dentro
    una raccolta ("Il sabato del villaggio"), le serie TV (TMDB non espone i
    creatori, quindi "Miami Vice" veniva sempre declassato).

    Il filtro per categoria NON e' un dettaglio: senza, la ricerca restituisce
    l'entita' piu' popolare con quel nome — "Fantozzi" da' la PERSONA Paolo
    Villaggio, "Aida" da' "prenome". Si cerca prima in italiano (il corpus e'
    italiano) e si ripiega sull'inglese, che copre i titoli originali stranieri.

    Punteggio -1.0 = non ho potuto chiedere, vedi cerca_google_books()."""
    spie = WIKIDATA_SPIE.get(categoria, ())
    for lingua in ("it", "en"):
        try:
            r = requests.get(WIKIDATA_API, headers={"User-Agent": USER_AGENT}, timeout=15,
                             params={"action": "wbsearchentities", "search": titolo,
                                     "language": lingua, "uselang": lingua,
                                     "format": "json", "limit": 10, "type": "item"})
            r.raise_for_status()
            risultati = r.json().get("search", [])
        except Exception as e:
            # Wikidata risponde con HTML (non JSON) quando limita le richieste: va
            # trattato come "non ho potuto chiedere", non come "non esiste".
            return -1.0, f"errore Wikidata: {e}", ""
        time.sleep(WIKIDATA_SLEEP)

        for it in risultati:
            descrizione = (it.get("description") or "").lower()
            if not any(s in descrizione for s in spie):
                continue
            sim_titolo = _similarita(titolo, it.get("label", ""))
            # L'autore non e' un campo strutturato qui: la descrizione italiana di
            # Wikidata lo contiene quasi sempre in chiaro ("romanzo scritto da E. L.
            # James", "opera lirica di Giuseppe Verdi"), quindi lo si cerca li'.
            sim_autore = _similarita_autore(autore, descrizione) if autore else 0.0
            if autore and sim_autore == 0.0:
                # Autore proposto assente dalla descrizione: non basta a scartare
                # (la descrizione puo' non nominarlo), ma non merita il bonus.
                punteggio = sim_titolo * 0.8
            else:
                punteggio = sim_titolo * 0.7 + (sim_autore * 0.3 if autore else sim_titolo * 0.3)
            if punteggio > 0:
                return (punteggio,
                        f"{it.get('label','')} - {it.get('description','')} [wikidata:{it.get('id')}]",
                        "")
    return 0.0, "nessun match Wikidata della categoria attesa", ""


def verifica_con_fallback(titolo: str, autore: str, categoria: str,
                          primo: tuple[float, str, str, str]) -> tuple[float, str, str, str]:
    """Se il database principale non ha confermato, interroga gli altri prima di
    condannare la voce.

    Il principio, deciso con l'utente il 2026-07-27: "la verita' la dicono i
    database, un'opera esiste SOLO se e' li' dentro" — ma allora quei database vanno
    interrogati tutti, e nel modo che ciascuno si aspetta. Misurato quel giorno: 9
    opere reali su 9 (Don Camillo, Fantozzi, Miami Vice, La traviata, Aida, Il sabato
    del villaggio, Cinquanta sfumature di grigio, Il libro della giungla, Anna)
    venivano scartate da un solo database interrogato male, non perche' non esistano.

    ⚠️ Un punteggio negativo che arriva da qui significa "non ho potuto chiedere":
    in quel caso si restituisce il risultato del database principale, MAI uno scarto
    - una voce non deve morire per un 503.
    """
    punteggio, descrizione, copertina, sottocat = primo
    if punteggio >= SOGLIA_ALTA:
        return primo

    tentativi = []
    if categoria == "libro":
        tentativi.append(("google books", lambda: cerca_google_books(titolo, autore)))
    tentativi.append(("wikidata", lambda: cerca_wikidata(titolo, autore, categoria)))

    for nome, cerca in tentativi:
        try:
            p, d, c = cerca()
        except Exception as e:
            print(f"      ({nome} non interrogabile: {e})")
            continue
        if p < 0:
            # Non raggiungibile: non e' una prova di inesistenza, si prosegue.
            continue
        if p > punteggio:
            punteggio, descrizione, copertina = p, f"{d} [via {nome}]", (copertina or c)
    return punteggio, descrizione, copertina, sottocat


def verifica_libro(titolo: str, autore: str) -> tuple[float, str, str, str]:
    """Cerca su Open Library, ritorna (punteggio, descrizione del match, URL
    copertina, sottocategoria — sempre '' qui: a differenza di film/serie (TMDB) e
    classica/opera (tag MusicBrainz), Open Library non ha un segnale abbastanza
    affidabile per distinguere 'teatro' dalle altre sottocategorie di libro, quindi
    quella resta a carico del prompt/modello, non del database. Il quarto valore
    esiste solo per uniformita' di firma con verifica_film/verifica_musica).
    Nessuna chiave richiesta ne' per la ricerca ne' per le copertine
    (covers.openlibrary.org e' pubblico).

    Aggiunto 2026-07-23 (caso reale trovato: "Ulisse" attribuito a "Dante Alighieri" —
    Ulisse e' un personaggio DENTRO l'Inferno di Dante, non un'opera a se' stante):
    prima il punteggio combinato (titolo 70% + autore 30%) per un titolo vero con
    autore sbagliato finiva quasi sempre appena SOTTO la soglia di conferma (~0.7),
    cadendo in "dubbio" e richiedendo sempre revisione umana anche quando l'errore
    era in realta' certo. Ora, se il titolo esiste chiaramente (similarita' >= 0.85)
    ma NESSUN autore trovato per quel titolo somiglia a quello proposto (< 0.25),
    e' un'attribuzione sbagliata con alta confidenza, non un caso ambiguo: il
    punteggio viene forzato sotto SOGLIA_BASSA per farlo cadere in
    "probabile_falso_positivo" invece che in "dubbio" — riduce la coda di revisione
    umana per questo specifico tipo di errore, gia' verificato non ambiguo."""
    try:
        resp = requests.get(
            "https://openlibrary.org/search.json",
            params={"title": titolo, "limit": 5},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except Exception as e:
        return -1.0, f"errore rete: {e}", "", ""
    migliore = (0.0, "", "")
    titolo_certo_ma_autore_estraneo = False
    miglior_sim_titolo = 0.0
    for d in docs:
        titolo_trovato = d.get("title", "")
        sim_titolo = _similarita(titolo, titolo_trovato)
        miglior_sim_titolo = max(miglior_sim_titolo, sim_titolo)
        autori_trovati = d.get("author_name", []) or []
        sim_autore = max((_similarita_autore(autore, a) for a in autori_trovati), default=0.0)
        if autore and sim_titolo >= SOGLIA_TITOLO_CERTO and sim_autore < SOGLIA_AUTORE_ESTRANEO:
            titolo_certo_ma_autore_estraneo = True
        # Il titolo pesa piu' dell'autore: un titolo giusto con autore diverso e'
        # comunque un indizio forte di opera reale (es. traduzioni/edizioni diverse).
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3
        if punteggio > migliore[0]:
            cover_id = d.get("cover_i")
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""
            migliore = (punteggio, f"{titolo_trovato} — {', '.join(autori_trovati[:2])}", cover_url)
    if titolo_certo_ma_autore_estraneo and migliore[0] < SOGLIA_ALTA:
        return (
            min(migliore[0], SOGLIA_BASSA - 0.01),
            migliore[1] + " [titolo reale ma nessun autore trovato somiglia a "
                          f"{autore!r}: attribuzione probabilmente sbagliata]",
            migliore[2], "",
        )
    if not autore and docs:
        # Autore mai estratto in origine (non sbagliato, solo assente): giudicare
        # SOLO sul titolo, la formula 70/30 non puo' mai confermarlo altrimenti.
        if miglior_sim_titolo >= SOGLIA_TITOLO_SENZA_AUTORE:
            return (max(migliore[0], SOGLIA_ALTA + 0.01),
                    migliore[1] + " [confermato solo per titolo, autore mai estratto]",
                    migliore[2], "")
        return (min(migliore[0], SOGLIA_BASSA - 0.01),
                migliore[1] + " [titolo non abbastanza simile, probabile rumore di chiacchiera]",
                migliore[2], "")
    return (*migliore, "")


TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w200"


def _tmdb_registi(movie_id: int, tmdb_key: str) -> list[str]:
    """Registi di un film TMDB. Serve una chiamata separata a /credits: l'endpoint
    di ricerca non restituisce i crediti, ed e' esattamente il motivo per cui fino
    al 2026-07-26 verifica_film() non poteva controllare l'autore (vedi sotto)."""
    try:
        resp = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}/credits",
            params={"api_key": tmdb_key},
            timeout=10,
        )
        resp.raise_for_status()
        crew = resp.json().get("crew", [])
    except Exception:
        return []
    return [c.get("name", "") for c in crew if c.get("job") == "Director"]


def _tmdb_cerca(endpoint: str, titolo: str, tmdb_key: str) -> list[dict]:
    """endpoint: 'movie' o 'tv'. Wrapper minimale per non duplicare i parametri."""
    try:
        resp = requests.get(
            f"https://api.themoviedb.org/3/search/{endpoint}",
            params={"api_key": tmdb_key, "query": titolo, "language": "it-IT"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        return []


def verifica_film(titolo: str, autore: str, tmdb_key: str) -> tuple[float, str, str, str]:
    """Cerca su TMDB, ritorna (punteggio, descrizione del match, URL locandina).

    ⚠️ BUG CORRETTO 2026-07-26: questa funzione riceveva `autore` e NON LO USAVA MAI
    — confrontava solo il titolo. Era l'unica delle tre (libro/film/musica) a farlo,
    quindi qualunque regista inventato dal modello veniva promosso a "confermato"
    purche' il titolo esistesse su TMDB. Casi reali trovati nel campione del 26/07:
    "Smoke" -> Harvey Keitel (e' l'ATTORE, il regista e' Wayne Wang), "I marciapiedi
    di New York" -> Woody Allen (e' Scorsese), "Mamma ho perso l'aereo" -> Robert
    Zemeckis e poi Mamoru Hosoda (e' Chris Columbus), "Una poltrona per due" ->
    Jerry Lewis (e' John Landis): tutti e cinque "confermati".
    Conseguenza grave: il 94% di conferma della categoria film era una misura vuota
    (diceva solo "il titolo esiste"), e su quella misura si stavano prendendo
    decisioni sulla qualita' dell'intera pipeline.

    Ora applica la STESSA logica gia' collaudata in verifica_libro/verifica_musica:
    punteggio combinato titolo 70% + autore 30%, declassamento esplicito quando il
    titolo e' certo ma nessun regista somiglia a quello proposto, e giudizio sul
    solo titolo quando l'autore non e' mai stato estratto.

    ⚠️ AGGIORNATO 2026-07-27 (tassonomia): cerca ANCHE su /search/tv, non solo
    /search/movie — TMDB tiene film e serie in archivi separati, motivo per cui
    prima Breaking Bad/Stranger Things/Gomorra risultavano tutte "film". La
    sottocategoria (film/serie/documentario) viene DERIVATA da quale endpoint ha
    dato il match migliore, non fatta indovinare al modello nel prompt: il database
    lo sa gia' con certezza, farlo dire al modello sarebbe un'altra fonte di errore
    come quella appena corretta sugli autori. Il quarto elemento ritornato e' la
    sottocategoria suggerita ('' se il match non e' abbastanza forte da fidarsene)."""
    risultati_movie = _tmdb_cerca("movie", titolo, tmdb_key)
    risultati_tv = _tmdb_cerca("tv", titolo, tmdb_key)
    if not risultati_movie and not risultati_tv:
        return -1.0, "nessun risultato TMDB (movie/tv)", "", ""

    # Prima passata sui soli titoli, su ENTRAMBI gli endpoint insieme: identifica i
    # candidati migliori senza spendere una chiamata /credits per ognuno (TMDB non ha
    # un limite stretto, ma chiamate extra per ogni voce su migliaia sono tempo sprecato).
    candidati = []
    for tipo, risultati, campi_titolo in (
        ("film", risultati_movie, ("title", "original_title")),
        ("serie", risultati_tv, ("name", "original_name")),
    ):
        for r in risultati[:5]:
            sim_titolo = max(_similarita(titolo, r.get(c, "") or "") for c in campi_titolo)
            candidati.append((sim_titolo, tipo, r))
    candidati.sort(key=lambda c: c[0], reverse=True)

    migliore = (0.0, "", "", "")
    migliore_autore_verificato = False  # True solo se il VINCITORE ATTUALE ha
    # davvero superato il controllo regista (tipo=="film" e sim_autore alta) — non
    # basta che qualche candidato l'abbia superato, deve averlo fatto proprio quello
    # che vince, altrimenti un remake con lo stesso titolo ma regista diverso
    # protegge indebitamente un match non verificato (vedi bug sotto).
    titolo_certo_ma_autore_estraneo = False
    miglior_sim_titolo = candidati[0][0] if candidati else 0.0
    SOGLIA_AUTORE_VERIFICATO = 0.5  # sim_autore sopra questa soglia = il regista
    # trovato somiglia abbastanza a quello proposto da considerarlo lo stesso film,
    # non solo "non palesemente estraneo" (SOGLIA_AUTORE_ESTRANEO=0.25 e' troppo
    # permissiva per questo scopo specifico).

    for sim_titolo, tipo, r in candidati[:3]:
        registi = (_tmdb_registi(r.get("id"), tmdb_key) if (autore and tipo == "film") else [])
        # Le serie TV non hanno un "regista" unico in TMDB (created_by e' piu' vicino
        # a "ideatore", spesso vuoto o multiplo per produzioni corali) — per le serie
        # ci si affida al solo titolo, come gia' fatto per la musica senza autore.
        sim_autore = max((_similarita_autore(autore, d) for d in registi), default=0.0)
        if autore and tipo == "film" and sim_titolo >= SOGLIA_TITOLO_CERTO and sim_autore < SOGLIA_AUTORE_ESTRANEO:
            titolo_certo_ma_autore_estraneo = True
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3 if (autore and tipo == "film") else sim_titolo
        if punteggio > migliore[0]:
            nome = r.get("title") if tipo == "film" else r.get("name")
            data_campo = "release_date" if tipo == "film" else "first_air_date"
            anno = (r.get(data_campo) or "")[:4]
            poster = r.get("poster_path")
            cover_url = f"{TMDB_IMG_BASE}{poster}" if poster else ""
            desc = f"{nome} ({anno})"
            if registi:
                desc += f" — regia: {', '.join(registi[:2])}"
            sub = tipo if sim_titolo >= SOGLIA_TITOLO_CERTO else ""
            migliore = (punteggio, desc, cover_url, sub)
            migliore_autore_verificato = bool(autore) and tipo == "film" and sim_autore >= SOGLIA_AUTORE_VERIFICATO

    # ⚠️ BUG CORRETTO 2026-07-27, due giri nella stessa notte:
    # (1) la ricerca ANCHE su /search/tv permette a una serie omonima non
    #     verificata (le serie non passano MAI dal controllo regista) di vincere
    #     con punteggio 1.0 sul solo titolo, scavalcando un film con l'autore
    #     sbagliato PRIMA che il declassamento sotto scattasse (la condizione
    #     originale guardava "migliore[0] < SOGLIA_ALTA", che con la serie vincente
    #     non era piu' vera). Corretto rendendo il declassamento incondizionato.
    # (2) MA cosi' facendo si rompe il caso legittimo di un remake (es. Ghostbusters
    #     2016/Feig vs 1984/Reitman): il declassamento scattava SEMPRE se un
    #     QUALSIASI candidato nel pool aveva l'autore sbagliato, anche quando il
    #     vincitore vero era un ALTRO candidato con l'autore giusto (il film 1984
    #     con Reitman, che vinceva onestamente col punteggio combinato). Provato dal
    #     vivo: Ghostbusters/Ivan Reitman e Serendipity/Peter Chelsom, entrambi
    #     corretti, finivano scartati per colpa di un candidato estraneo nel pool.
    # Fix: si declassa solo se il VINCITORE ATTUALE non ha superato il controllo
    # regista lui stesso — un film diverso con autore sbagliato nello stesso pool
    # non deve piu' contaminare un match che ha vinto onestamente.
    #
    # LIMITE NOTO (non risolto stanotte, scelta deliberata): se una serie autentica
    # (es. Stranger Things, creatori Duffer Brothers) condivide il titolo con un
    # film INDIPENDENTE e non correlato (es. un film indie del 2013 chiamato anch'esso
    # "Stranger Things"), quel film irrilevante fa scattare comunque il declassamento,
    # perche' per le serie non esiste un controllo regista/creatori (TMDB lo espone
    # solo su /tv/{id}, non implementato). Risultato: la serie corretta finisce in
    # coda di revisione umana invece che confermata subito. Si accetta questo falso
    # negativo (nella direzione sicura: chiede revisione, non conferma alla cieca)
    # piuttosto che tentare un'euristica affrettata che rischi di riaprire la falla
    # vera (autore inventato confermato) risolta sopra. Da migliorare in futuro
    # aggiungendo _tmdb_creatori_serie() analoga a _tmdb_registi().
    if titolo_certo_ma_autore_estraneo and not migliore_autore_verificato:
        return (
            min(migliore[0], SOGLIA_BASSA - 0.01),
            migliore[1] + f" [titolo reale ma nessun regista somiglia a {autore!r}: "
                          "attribuzione probabilmente sbagliata]",
            migliore[2], "",
        )
    if not autore and (risultati_movie or risultati_tv):
        # Autore mai estratto (non sbagliato, assente): giudicare solo sul titolo,
        # stesso principio di verifica_libro/verifica_musica.
        if miglior_sim_titolo >= SOGLIA_TITOLO_SENZA_AUTORE:
            return (max(migliore[0], SOGLIA_ALTA + 0.01),
                    migliore[1] + " [confermato solo per titolo, autore mai estratto]",
                    migliore[2], migliore[3])
        return (min(migliore[0], SOGLIA_BASSA - 0.01),
                migliore[1] + " [titolo non abbastanza simile, probabile rumore di chiacchiera]",
                migliore[2], "")
    return migliore


GENERI_CLASSICA = {"classical", "opera", "orchestral", "chamber music", "baroque"}


def _sottocategoria_da_tag(tags: list[dict]) -> str:
    """Deriva classica/opera dai tag di genere che MusicBrainz gia' restituisce nella
    stessa risposta (inc=tags, nessuna chiamata aggiuntiva). Se il tag 'opera' e'
    presente vince su 'classical' generico. Nessun tag riconosciuto -> stringa vuota,
    NON si inventa nulla: la sottocategoria resta indeterminata piuttosto che sbagliata."""
    nomi = {(t.get("name") or "").lower() for t in (tags or [])}
    if "opera" in nomi:
        return "opera"
    if nomi & GENERI_CLASSICA:
        return "classica"
    return ""


def verifica_musica(titolo: str, autore: str) -> tuple[float, str, str, str]:
    """Cerca su MusicBrainz, ritorna (punteggio, descrizione del match, URL copertina,
    sottocategoria suggerita: 'classica'/'opera'/'' se non distinguibile).
    URL copertina via Cover Art Archive o '' se la release migliore non ha copertina
    caricata - non verificato con una richiesta separata, l'URL e' costruito
    otticamente dall'MBID della prima release associata: il template deve gestire
    un eventuale 404 lato client, non e' garantito che l'immagine esista davvero).
    Il chiamante deve rispettare MUSICBRAINZ_SLEEP tra una chiamata e l'altra.

    IMPORTANTE (trovato con un test reale il 2026-07-22): il titolo va passato SENZA
    virgolette (query per token, non frase esatta) - un titolo trascritto con un
    errore whisper (es. "Cray baby" invece di "Cry Baby") con una frase esatta tra
    virgolette dava 0 risultati, azzerando il punteggio anche se la canzone reale
    esiste. Senza virgolette Lucene usa la sua relevance ranking sui singoli token e
    trova comunque il titolo giusto tra i primi risultati. L'autore invece resta tra
    virgolette (nome proprio, meno soggetto a rumore di trascrizione).

    ⚠️ AGGIORNATO 2026-07-27 (tassonomia): 'classica'/'opera' derivate dai tag di
    genere che la risposta gia' contiene (inc=tags aggiunto), non fatte indovinare
    al modello — stesso principio di verifica_film per film/serie via TMDB."""
    try:
        titolo_pulito = re.sub(r'["\']', "", titolo)
        query = f'recording:({titolo_pulito})' + (f' AND artist:"{autore}"' if autore else "")
        resp = requests.get(
            "https://musicbrainz.org/ws/2/recording",
            params={"query": query, "fmt": "json", "limit": 5, "inc": "releases+tags"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])
    except Exception as e:
        return -1.0, f"errore rete: {e}", "", ""
    migliore = (0.0, "", "", "")
    titolo_certo_ma_autore_estraneo = False
    miglior_sim_titolo = 0.0
    for r in recordings:
        titolo_trovato = r.get("title", "")
        sim_titolo = _similarita(titolo, titolo_trovato)
        miglior_sim_titolo = max(miglior_sim_titolo, sim_titolo)
        artisti = [ac.get("name", "") for ac in r.get("artist-credit", []) if isinstance(ac, dict)]
        sim_autore = max((_similarita_autore(autore, a) for a in artisti), default=0.0)
        if autore and sim_titolo >= SOGLIA_TITOLO_CERTO and sim_autore < SOGLIA_AUTORE_ESTRANEO:
            titolo_certo_ma_autore_estraneo = True
        punteggio = sim_titolo * 0.7 + sim_autore * 0.3
        if punteggio > migliore[0]:
            releases = r.get("releases") or []
            release_id = releases[0].get("id") if releases else None
            cover_url = f"https://coverartarchive.org/release/{release_id}/front-250" if release_id else ""
            sub = _sottocategoria_da_tag(r.get("tags"))
            migliore = (punteggio, f"{titolo_trovato} — {', '.join(artisti[:2])}", cover_url, sub)
    if titolo_certo_ma_autore_estraneo and migliore[0] < SOGLIA_ALTA:
        return (
            min(migliore[0], SOGLIA_BASSA - 0.01),
            migliore[1] + " [titolo reale ma nessun artista trovato somiglia a "
                          f"{autore!r}: attribuzione probabilmente sbagliata]",
            migliore[2], "",
        )
    if not autore and recordings:
        # Stesso principio di verifica_libro(): autore mai estratto, giudicare solo
        # sul titolo (la formula 70/30 non puo' mai confermarlo altrimenti).
        if miglior_sim_titolo >= SOGLIA_TITOLO_SENZA_AUTORE:
            return (max(migliore[0], SOGLIA_ALTA + 0.01),
                    migliore[1] + " [confermato solo per titolo, autore mai estratto]",
                    migliore[2], migliore[3])
        return (min(migliore[0], SOGLIA_BASSA - 0.01),
                migliore[1] + " [titolo non abbastanza simile, probabile rumore di chiacchiera]",
                migliore[2], "")
    return migliore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="*", help="date YYYY-MM-DD da verificare (default: tutte)")
    parser.add_argument("--limit", type=int, default=0, help="numero massimo di voci da processare in questo run (0 = tutte)")
    parser.add_argument("--dataset", choices=list(DATASET_CONFIG), default="riferimenti",
                         help="quale cartella dati verificare (default: riferimenti)")
    args = parser.parse_args()

    cfg = DATASET_CONFIG[args.dataset]
    data_dir = dati_root(ROOT) / cfg["dir"]
    report_path = logs_root(ROOT) / cfg["report"]
    campo, mappa = cfg["campo"], cfg["mappa"]

    tmdb_key = _tmdb_key()

    files = [data_dir / f"{d}.json" for d in args.date] if args.date else sorted(data_dir.glob("*.json"))

    tutte_le_voci = []
    for fp in files:
        if not fp.exists():
            continue
        dati = json.loads(fp.read_text(encoding="utf-8"))
        for r in dati:
            if r.get("titolo") and r.get(campo) in mappa and "confermato_esterno" not in r:
                tutte_le_voci.append((fp, r))

    if args.limit:
        tutte_le_voci = tutte_le_voci[:args.limit]

    print(f"Verifico {len(tutte_le_voci)} voci ({args.dataset}) contro database esterni reali (Open Library/TMDB/MusicBrainz)...")

    dubbi = []
    confermati = 0
    scartati = 0
    per_file: dict[Path, list[dict]] = {}

    for i, (fp, r) in enumerate(tutte_le_voci):
        categoria = mappa[r[campo]]
        titolo = r["titolo"]
        autore = r.get("autore", "")
        try:
            if categoria == "libro":
                primo = verifica_libro(titolo, autore)
                time.sleep(0.35)  # margine sotto ~3 richieste/secondo
            elif categoria == "film":
                primo = verifica_film(titolo, autore, tmdb_key)
                time.sleep(0.05)
            else:  # musica
                primo = verifica_musica(titolo, autore)
                time.sleep(MUSICBRAINZ_SLEEP)
            # Il database principale non basta da solo: vedi verifica_con_fallback().
            punteggio, match, copertina, sub_suggerita = verifica_con_fallback(
                titolo, autore, categoria, primo)
        except Exception as e:
            print(f"  [{i+1}/{len(tutte_le_voci)}] ERRORE imprevisto su {titolo!r}: {e}, salto")
            continue

        if punteggio < 0:
            # Errore di rete: non scrivere nulla, riprovare in un run futuro.
            continue

        # Trovato 2026-07-22 nel run reale sul backlog: "Ray Charles"/autore="Ray
        # Charles" e "Lucio Dalla"/autore="Lucio Dalla" confermati automaticamente
        # perche' il database esterno (MusicBrainz include tributi/compilation con
        # lo stesso nome dell'artista come titolo) trova un "match" che pero' dice
        # solo "l'artista esiste", non "e' un'opera specifica citata". Stesso
        # controllo strutturale gia' fatto in trascrivi_e_estrai_clip.py: se
        # titolo e autore normalizzati sono uguali, non fidarsi MAI del punteggio
        # esterno, forzare "dubbio" a prescindere da quanto alto sia.
        titolo_norm = _normalizza(titolo)
        autore_norm = _normalizza(autore)
        titolo_e_autore_uguali = bool(titolo_norm) and titolo_norm == autore_norm
        if titolo_e_autore_uguali:
            punteggio = min(punteggio, SOGLIA_ALTA - 0.01)

        r["confermato_esterno"] = punteggio >= SOGLIA_ALTA
        # Copertina salvata SOLO se il match e' confermato: un titolo dubbio/scartato
        # non deve mostrare la copertina di un'opera probabilmente sbagliata.
        if r["confermato_esterno"] and copertina:
            r["copertina"] = copertina
        # Sottocategoria (tassonomia 2026-07-27): scritta SOLO se confermato e SOLO
        # se il campo e' ancora vuoto — non sovrascrive mai un giudizio gia' presente
        # (dato dal modello in estrazione, o da una revisione umana precedente).
        if r["confermato_esterno"] and sub_suggerita and not (r.get("sottocategoria") or "").strip():
            r["sottocategoria"] = sub_suggerita
        per_file.setdefault(fp, []).append(r)

        if punteggio >= SOGLIA_ALTA:
            confermati += 1
        else:
            esito = "dubbio" if punteggio >= SOGLIA_BASSA else "probabile_falso_positivo"
            if punteggio < SOGLIA_BASSA:
                scartati += 1
            dubbi.append({
                "id": r.get("id"), "file": fp.name, "categoria": categoria,
                "titolo": titolo, "autore": autore, "punteggio": round(punteggio, 3),
                "match_trovato": match, "esito": esito,
            })

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(tutte_le_voci)}] confermati finora: {confermati}, dubbi/scartati: {len(dubbi)}")

    for fp, voci_modificate in per_file.items():
        dati = json.loads(fp.read_text(encoding="utf-8"))
        by_id = {r.get("id"): r for r in voci_modificate}
        for r in dati:
            if r.get("id") in by_id:
                r["confermato_esterno"] = by_id[r["id"]]["confermato_esterno"]
                if "copertina" in by_id[r["id"]]:
                    r["copertina"] = by_id[r["id"]]["copertina"]
        fp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")

    esistenti = {}
    if report_path.exists():
        try:
            for v in json.loads(report_path.read_text(encoding="utf-8")):
                esistenti[v["id"]] = v
        except (json.JSONDecodeError, OSError):
            pass
    for v in dubbi:
        esistenti[v["id"]] = v
    fuso = list(esistenti.values())

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(fuso, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nFatto. {confermati} confermate automaticamente, {scartati} probabili falsi positivi, "
          f"{len(dubbi) - scartati} dubbie — report completo in {report_path} ({len(fuso)} voci totali). "
          "NON cancellato nulla, solo segnalato/marcato.")


if __name__ == "__main__":
    main()
