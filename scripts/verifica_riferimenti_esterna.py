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
from contextlib import contextmanager
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
CREDITS_FM_KEY_FILE = Path.home() / "API credit_fm.txt"
CREDITS_FM_API = "https://api.credits.fm/v1"
# Senza chiave l'API concede 30 lookup/min, con chiave gratuita 300 (documentazione
# ufficiale letta il 2026-07-28). La pausa si adatta da sola: senza chiave il ritmo e'
# comunque il doppio di MusicBrainz, con chiave e' cinque volte tanto.
CREDITS_FM_SLEEP_SENZA_CHIAVE = 2.1
CREDITS_FM_SLEEP_CON_CHIAVE = 0.25
GOOGLE_BOOKS_KEY_FILE = Path.home() / "API_Google_Books.txt"
USER_AGENT = "IlVoloDelMattinoArchivio/1.0 (uso non commerciale, archivio fan Radio Deejay)"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
WIKIDATA_SLEEP = 0.35   # Wikidata rifiuta le richieste troppo ravvicinate rispondendo
# con contenuto non-JSON (misurato il 2026-07-27): mai chiamare .json() senza rete.
# I 503 di Google Books sono frequenti e intermittenti, non definitivi: misurata il
# 2026-07-28 una richiesta fallita su 6 in una raffica, con chiave valida.
#
# ⚠️ Il numero di tentativi e' stato ALZATO a 5 e poi riportato a 3 lo stesso giorno,
# dopo aver cronometrato dove va il tempo: Google Books da solo mangiava il 36,5% di
# una passata (224s su 615, 10,69s per chiamata contro 1,5s di Open Library), tutto in
# attese crescenti. Con 3 tentativi e l'attesa limitata, il caso peggiore passa da 15s
# a 3,5s, e la probabilita' che tutti e tre falliscano e' sotto l'1%. Il rischio
# residuo e' coperto: se l'archivio non risponde il verdetto viene SOSPESO, non emesso,
# e la voce torna in coda per il run successivo.
GOOGLE_BOOKS_TENTATIVI = 3
GOOGLE_BOOKS_ATTESA_MAX = 2.0
WIKIDATA_TENTATIVI = 4

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

# Tutti i campi che questo script puo' scrivere su una voce. Il salvataggio finale
# rilegge il file da disco (per non sovrascrivere modifiche concorrenti) e ricopia
# SOLO i campi elencati qui: finche' l'elenco era scritto a mano dentro il ciclo,
# ogni campo nuovo veniva calcolato, contato e stampato ma mai salvato — successo
# davvero con autore/autore_dal_database/sottocategoria/link_autore/solo_autore, che
# non hanno mai raggiunto il disco. Aggiungere qui QUALSIASI campo nuovo.
CAMPI_PERSISTITI = ("confermato_esterno", "copertina", "sottocategoria",
                    "autore", "autore_dal_database", "link_autore", "solo_autore")

# Marca lasciata nella descrizione del match quando il titolo e' stato confermato ma
# l'autore proposto NON ha trovato riscontro. Non e' un difetto: la descrizione di
# Wikidata spesso non nomina l'autore ("Don Camillo - film del 1952 diretto da Julien
# Duvivier", mentre l'autore proposto e' Guareschi, che e' lo scrittore del libro da
# cui il film e' tratto). E' proprio il segnale che quel nome puo' appartenere a piu'
# di un archivio, e fa scattare la verifica incrociata fra categorie.
MARCA_AUTORE_NON_CORROBORATO = " [autore non corroborato]"


# Tempo speso per archivio, in secondi, e numero di chiamate. Serve a sapere DOVE va
# il tempo prima di ottimizzare: misurato il 2026-07-28 un ritmo di ~45 s/voce, ma le
# attese di MusicBrainz spiegano in tutto ~78 minuti su 5.868 voci. Ottimizzare
# l'archivio sbagliato e' il modo piu' rapido di perdere una giornata.
TEMPI: dict[str, list] = {}


@contextmanager
def cronometra(archivio: str):
    inizio = time.time()
    try:
        yield
    finally:
        voce = TEMPI.setdefault(archivio, [0.0, 0])
        voce[0] += time.time() - inizio
        voce[1] += 1


def stampa_tempi() -> None:
    if not TEMPI:
        return
    totale = sum(v[0] for v in TEMPI.values())
    print("\n  dove e' andato il tempo (attese incluse):")
    for archivio, (secondi, chiamate) in sorted(TEMPI.items(), key=lambda x: -x[1][0]):
        print(f"    {archivio:16} {secondi:8.0f}s  ({100*secondi/max(totale,1):4.1f}%)  "
              f"{chiamate:5} chiamate, {secondi/max(chiamate,1):5.2f}s l'una")
    print(f"    {'TOTALE':16} {totale:8.0f}s")


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


def _tmdb_key(obbligatoria: bool = True) -> str:
    """Chiave TMDB. `obbligatoria=False` per chi vuole SEGNALARE l'assenza invece di
    morire: il controllo d'ambiente deve poter dire "manca la chiave TMDB e per il
    resto tutto risponde", non uscire al primo file assente lasciando gli altri
    archivi non verificati. Successo davvero il 2026-07-28 sul K16, che non aveva
    nessuna chiave: il controllo e' morto sulla prima e non ha misurato le altre."""
    if not TMDB_KEY_FILE.exists():
        if not obbligatoria:
            return ""
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


def _cron_cerca_google_books(titolo: str, autore: str) -> tuple[float, str, str, str]:
    """Cerca un libro su Google Books.
    Ritorna (punteggio, descrizione, copertina, autori trovati).

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
        return -1.0, "chiave Google Books assente", "", ""
    query = f'intitle:"{titolo}"' + (f' inauthor:"{autore}"' if autore else "")
    risposta = None
    for tentativo in range(GOOGLE_BOOKS_TENTATIVI):
        try:
            risposta = requests.get(GOOGLE_BOOKS_API, timeout=20,
                                    params={"q": query, "maxResults": 5, "key": key})
        except Exception as e:
            return -1.0, f"errore rete Google Books: {e}", "", ""
        if risposta.status_code == 200:
            break
        time.sleep(min(1.5 * (tentativo + 1), GOOGLE_BOOKS_ATTESA_MAX))
    else:
        return -1.0, f"Google Books non raggiungibile (HTTP {risposta.status_code})", "", ""

    items = risposta.json().get("items", [])
    if not items:
        return 0.0, "nessun risultato Google Books", "", ""
    migliore = (0.0, "", "", "")
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
            migliore = (punteggio, f"{vi.get('title','')} - {', '.join(autori[:2])}",
                        img, ", ".join(autori[:2]))
    return migliore


# Descrizioni Wikidata che indicano una PERSONA o un GRUPPO, mai un'opera. Filtro
# negativo, applicato prima delle spie positive: misurato il 2026-07-28 che la spia
# "musica" e' sottostringa di "musicALE", quindi "gruppo musicale statunitense" (cioe'
# i Metallica, una band) superava il filtro di categoria e veniva confermato come se
# fosse un'opera. Lo stesso vale per un regista o uno scrittore omonimo di un'opera:
# e' la ragione per cui le spie esistono ("Fantozzi" -> la persona Paolo Villaggio),
# ma le spie positive da sole non bastano quando le due descrizioni si sovrappongono.
WIKIDATA_NON_OPERE = ("gruppo musicale", "duo musicale", "band ", "cantante",
                      "cantautore", "cantautrice", "compositore", "compositrice",
                      "musicista", "regista", "scrittore", "scrittrice", "attore",
                      "attrice", "poeta", "poetessa", "personaggio",
                      "musical group", "musical duo", "singer", "songwriter",
                      "composer", "musician", "film director", "writer", "actor",
                      "actress", "poet", "fictional")


def _wikidata_api(params: dict) -> dict:
    """Unico punto d'accesso a Wikidata, con attesa crescente sul 429.

    Misurato il 2026-07-28 sul banco della verifica: dopo una ventina di richieste
    ravvicinate Wikidata risponde 429 e da li' in poi TUTTE le chiamate falliscono.
    Senza attesa crescente il fallback muore dopo i primi episodi di un batch: su
    ~1.100 episodi (~7 voci ciascuno) significa perderlo quasi del tutto, in silenzio,
    perche' un -1.0 non scarta la voce ma la rimanda a un run futuro che ricadrebbe
    nello stesso muro. Solleva se il muro non si apre: il chiamante lo trattera' come
    "non ho potuto chiedere", mai come "non esiste"."""
    attesa = WIKIDATA_SLEEP
    for _tentativo in range(WIKIDATA_TENTATIVI):
        r = requests.get(WIKIDATA_API, headers={"User-Agent": USER_AGENT}, timeout=20,
                         params={**params, "format": "json"})
        if r.status_code == 429:
            # Retry-After se c'e', altrimenti si raddoppia l'attesa a ogni giro.
            pausa = 0.0
            try:
                pausa = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                pausa = 0.0
            time.sleep(min(pausa or attesa * 4, 30.0))
            attesa *= 2
            continue
        r.raise_for_status()
        time.sleep(WIKIDATA_SLEEP)
        return r.json()
    raise RuntimeError(f"Wikidata limita le richieste (429) dopo {WIKIDATA_TENTATIVI} tentativi")


def _wikidata_cerca(termine: str, lingua: str, limite: int) -> list[dict]:
    """Ricerca di entita' per nome. Solleva se Wikidata non risponde: vedi _wikidata_api."""
    return _wikidata_api({"action": "wbsearchentities", "search": termine,
                          "language": lingua, "uselang": lingua,
                          "limit": limite, "type": "item"}).get("search", [])


# Proprieta' Wikidata che collegano un'opera a chi l'ha fatta, per categoria. Sono
# CLAIM STRUTTURATI, non testo da interpretare: e' la differenza fra leggere un dato e
# indovinarlo da una descrizione.
AUTORE_PROP = {
    "libro": ("P50",),           # autore
    "film": ("P57", "P170"),     # regista; creatore (le serie non hanno un regista unico)
    "musica": ("P86", "P175"),   # compositore; interprete
}


def _wikidata_autore_opera(titolo: str, categoria: str) -> str:
    """Chiede a Wikidata CHI ha fatto un'opera, leggendo i claim strutturati.

    Terza fonte per il completamento dell'autore, aggiunta il 2026-07-28 dopo aver
    misurato che le prime due lasciano buchi reali: Open Library restituisce per "Il
    libro della giungla" quattro schede col titolo esatto e author_name vuoto, e Google
    Books risponde 503 a intermittenza (osservato: 1 richiesta su 6 in una raffica) —
    e quel 503 diventava silenziosamente "nessun autore trovato", che e' la solita
    confusione fra "non ho potuto chiedere" e "non c'e'".

    Qui l'autore non si ricava dalla descrizione a parole: si legge il claim. Provato
    dal vivo: La traviata -> Verdi (P86), Fantozzi -> Villaggio (P58), Miami Vice ->
    Yerkovich (P170)."""
    spie = WIKIDATA_SPIE.get(categoria, ())
    proprieta = AUTORE_PROP.get(categoria, ())
    if not spie or not proprieta:
        return ""
    try:
        qid = ""
        for lingua in ("it", "en"):
            for it in _wikidata_cerca(titolo, lingua, 10):
                descrizione = (it.get("description") or "").lower()
                if not any(s in descrizione for s in spie):
                    continue
                if _similarita(titolo, it.get("label", "")) < SOGLIA_TITOLO_CERTO:
                    continue
                qid = it.get("id", "")
                break
            if qid:
                break
        if not qid:
            return ""

        claims = _wikidata_api({"action": "wbgetentities", "ids": qid,
                                "props": "claims"})["entities"][qid].get("claims", {})
        for prop in proprieta:
            for c in claims.get(prop, []):
                valore = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                if not isinstance(valore, dict) or not valore.get("id"):
                    continue
                persona = valore["id"]
                etichette = _wikidata_api({"action": "wbgetentities", "ids": persona,
                                           "props": "labels", "languages": "it|en"})
                lab = etichette["entities"][persona].get("labels", {})
                nome = (lab.get("it") or lab.get("en") or {}).get("value", "")
                if nome:
                    return nome
    except Exception as e:
        print(f"      (Wikidata non ha potuto dire l'autore di {titolo!r}: {e})")
    return ""


# Un "autore" la cui descrizione dice queste cose non e' una persona che ha scritto
# qualcosa: e' un personaggio. Serve come filtro NEGATIVO, mai come requisito positivo.
# Misurato il 2026-07-28 sul banco della verifica: il requisito positivo ("l'autore
# proposto deve risultare un autore reale della categoria") sembra la mossa ovvia ma
# perde Don Camillo (Guareschi e' 'scrittore', non un mestiere del cinema) e non ferma
# comunque Orfeo (la sua descrizione contiene 'musicista'). Anche il controllo sui
# legami strutturati di Wikidata costa -1 opera vera per -1 rumore: Guareschi non
# compare tra i crediti del film ne' come 'basato su'. Il filtro negativo invece non
# puo' far cadere un autore vero, perche' nessun autore vero e' un personaggio.
PERSONAGGI_NON_AUTORI = ("personaggio", "mitologia", "mitologic", "divinit",
                         "figura biblica", "eroe", "semidio", "creatura",
                         "fictional", "mytholog", "deity", "legendary")


def _e_personaggio_non_autore(nome: str) -> bool:
    """L'autore proposto e' in realta' un personaggio (mitologico, letterario, biblico)?

    Nato da un caso reale del gruppo rumore: "Euridice"/autore="Orfeo". L'opera esiste
    davvero (di Jacopo Peri), quindi Wikidata la conferma sul solo titolo e la voce
    entrava nell'archivio con un autore inventato dal mito. Costa una chiamata sola, e
    solo nel ramo ambiguo (autore proposto ma assente dalla descrizione dell'opera)."""
    if not nome:
        return False
    try:
        risultati = _wikidata_cerca(nome, "it", 3)
    except Exception:
        # Non ho potuto chiedere: non e' una prova di nulla, non si filtra.
        return False
    for it in risultati:
        if _similarita_autore(nome, it.get("label", "")) < 0.5:
            continue
        descrizione = (it.get("description") or "").lower()
        return any(s in descrizione for s in PERSONAGGI_NON_AUTORI)
    return False


def _credits_fm_key() -> str:
    """Chiave opzionale. Assente: si lavora lo stesso, piu' piano. Deve stare anche su
    OMV (~/API_Credits_fm.txt, 600) perche' la pipeline gira li', non sull'HP14 —
    stesso passo gia' necessario per TMDB e Google Books."""
    if not CREDITS_FM_KEY_FILE.exists():
        return ""
    return CREDITS_FM_KEY_FILE.read_text(encoding="utf-8").strip()


def _credits_fm_risolvi(titolo: str, autore: str = "") -> dict | None:
    """Interroga /resolve/track. Ritorna il JSON, {} se non trova, None se non ho
    potuto chiedere (rete, 429, 503) — la solita distinzione, che qui e' importante
    perche' il chiamante non deve mai scartare una voce per un problema di rete."""
    key = _credits_fm_key()
    corpo = {"name": titolo}
    if autore:
        corpo["artist"] = autore
    try:
        intestazioni = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if key:
            # Nell'header, MAI in query string: un errore HTTP scriverebbe la chiave
            # nei log in chiaro, com'e' gia' successo con Gemini (1.295 occorrenze).
            intestazioni["x-api-key"] = key
        r = requests.post(f"{CREDITS_FM_API}/resolve/track", timeout=25,
                          headers=intestazioni, json=corpo)
    except Exception:
        return None
    finally:
        time.sleep(CREDITS_FM_SLEEP_CON_CHIAVE if key else CREDITS_FM_SLEEP_SENZA_CHIAVE)
    if r.status_code == 404:
        return {}
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _credits_fm_titolo_e_artista(titolo: str) -> bool | None:
    """Il presunto titolo e' in realta' il nome dell'artista?

    Segnale trovato il 2026-07-28 provando l'API sui casi reali: quando si cerca un
    nome d'artista, Credits.fm risponde con quell'artista in `artist_names` (Vangelis
    -> Vangelis, Barry White -> Barry White, Little Tony -> Little Tony), mentre per un
    titolo vero l'artista e' un altro (Yesterday -> Matt Monro, Superheroes -> Living
    in a Box). Misurato: 5 artisti su 7 riconosciuti e **zero falsi allarmi** su 7
    titoli veri, con UNA chiamata invece delle due che serve a MusicBrainz.

    Non basta da solo - non prende "Bob Dylan" (esiste una canzone omonima di Fallulah)
    ne' "Velvet Underground" (di Jonathan Richman) - quindi resta il primo gradino, non
    l'unico. None = non ho potuto chiedere."""
    d = _credits_fm_risolvi(titolo)
    if d is None:
        return None
    return any(_similarita_autore(titolo, a) >= 0.9 for a in (d.get("artist_names") or []))


def _cron_cerca_credits_fm(titolo: str, autore: str) -> tuple[float, str, str, str]:
    """Cerca un brano su Credits.fm, archivio aperto di crediti musicali che incrocia
    MLC, CISAC, ISNI, MusicBrainz, Spotify e Apple Music.

    Affiancato a MusicBrainz, non al suo posto: misurato il 2026-07-28 che su "La
    traviata"/Verdi risponde `unmatched`, perche' e' un archivio di registrazioni
    pop/rock e non copre la lirica, dove invece MusicBrainz e Wikidata reggono.

    ⚠️ `match_status: matched` NON e' una conferma: risponde "matched" anche per "Pink
    Floyd", "Metallica" e "Cordyceps". Come per MusicBrainz, l'esistenza di QUALCOSA
    con quel nome non dice nulla; conta la corrispondenza con l'artista proposto."""
    if not titolo:
        return 0.0, "", "", ""
    d = _credits_fm_risolvi(titolo, autore)
    if d is None:
        return -1.0, "Credits.fm non raggiungibile", "", ""
    if not d or d.get("match_status") != "matched":
        return 0.0, "nessun brano su Credits.fm", "", ""

    titolo_trovato = d.get("recording_title") or d.get("song_title") or ""
    artisti = d.get("artist_names") or []
    sim_titolo = _similarita(titolo, titolo_trovato)
    sim_autore = max((_similarita_autore(autore, a) for a in artisti), default=0.0)
    if not autore:
        # Stessa regola del resto della musica: senza autore nessun archivio puo' dire
        # che sia l'opera citata, quindi non si conferma. Vedi verifica_musica().
        return 0.0, f"{titolo_trovato} - {', '.join(artisti[:2])} [senza autore, non confermabile]", "", ""
    punteggio = sim_titolo * 0.7 + sim_autore * 0.3
    return punteggio, f"{titolo_trovato} - {', '.join(artisti[:2])}", "", ""


def _cron_cerca_wikidata(titolo: str, autore: str, categoria: str) -> tuple[float, str, str, str]:
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
            risultati = _wikidata_cerca(titolo, lingua, 10)
        except Exception as e:
            # Wikidata risponde con HTML (non JSON) quando limita le richieste: va
            # trattato come "non ho potuto chiedere", non come "non esiste".
            return -1.0, f"errore Wikidata: {e}", "", ""

        for it in risultati:
            descrizione = (it.get("description") or "").lower()
            # Prima il filtro negativo: una persona o un gruppo non e' mai un'opera,
            # per quanto la sua descrizione somigli a quella di una.
            if any(s in descrizione for s in WIKIDATA_NON_OPERE):
                continue
            if not any(s in descrizione for s in spie):
                continue
            sim_titolo = _similarita(titolo, it.get("label", ""))
            # L'autore non e' un campo strutturato qui: la descrizione italiana di
            # Wikidata lo contiene quasi sempre in chiaro ("romanzo scritto da E. L.
            # James", "opera lirica di Giuseppe Verdi"), quindi lo si cerca li'.
            sim_autore = _similarita_autore(autore, descrizione) if autore else 0.0
            autore_non_corroborato = bool(autore) and sim_autore == 0.0
            if autore_non_corroborato:
                # Autore proposto assente dalla descrizione: non basta a scartare
                # (la descrizione puo' non nominarlo), ma non merita il bonus.
                punteggio = sim_titolo * 0.8
                # ...e se quell'autore e' un personaggio, la voce non va confermata
                # in automatico: l'opera esiste, l'attribuzione no. Si declassa a
                # "dubbio" invece di scartare, perche' il titolo resta reale e un
                # occhio umano puo' chiudere il caso in due secondi.
                if _e_personaggio_non_autore(autore):
                    punteggio = min(punteggio, SOGLIA_ALTA - 0.01)
            else:
                punteggio = sim_titolo * 0.7 + (sim_autore * 0.3 if autore else sim_titolo * 0.3)
            if punteggio > 0:
                marca = MARCA_AUTORE_NON_CORROBORATO if autore_non_corroborato else ""
                return (punteggio,
                        f"{it.get('label','')} - {it.get('description','')} "
                        f"[wikidata:{it.get('id')}]{marca}",
                        "", "")
    return 0.0, "nessun match Wikidata della categoria attesa", "", ""


# Mestieri che rendono una persona un "autore" credibile per ciascuna categoria, come
# li scrive Wikidata nella descrizione italiana.
PROFESSIONI_AUTORE = {
    "libro": ("scrittore", "scrittrice", "poeta", "poetessa", "romanziere", "romanziera",
              "saggista", "drammaturgo", "drammaturga", "autore", "autrice", "filosofo",
              "filosofa", "giornalista", "divulgatore", "divulgatrice", "traduttore"),
    "film": ("regista", "sceneggiatore", "sceneggiatrice", "attore", "attrice",
             "produttore", "produttrice", "cineasta"),
    "musica": ("cantante", "cantautore", "cantautrice", "musicista", "compositore",
               "compositrice", "gruppo musicale", "band", "rapper", "dj", "direttore d'orchestra"),
}


def _cron_verifica_autore(nome: str, categoria: str) -> tuple[float, str, str]:
    """Verifica che un NOME sia davvero un autore reale della categoria, senza avere
    un titolo. Ritorna (punteggio, descrizione, url alla pagina Wikipedia italiana).

    Serve al caso deciso con l'utente il 2026-07-27: quando in onda viene nominato
    l'autore ma NON il titolo (succede spesso — un brano letto ad alta voce, "adesso
    vi leggo una cosa di Erri De Luca"), invece di buttare via tutto si conserva cio'
    che e' certo, l'autore, e si rimanda alle sue opere. E' l'opposto di far indovinare
    un titolo al modello: si registra solo il fatto verificato.

    Due controlli, entrambi necessari (misurati il 2026-07-27):
    - il mestiere dev'essere pertinente alla categoria, altrimenti qualunque nome
      proprio passerebbe;
    - il nome trovato deve somigliare a quello cercato PER PAROLE, altrimenti
      "Enrico" viene risolto in "Heinrich Heine" (Wikidata traduce i prenomi) e un
      saluto qualunque diventa un poeta tedesco."""
    professioni = PROFESSIONI_AUTORE.get(categoria, ())
    if not nome or not professioni:
        return 0.0, "", ""
    try:
        risultati = _wikidata_cerca(nome, "it", 5)
    except Exception as e:
        return -1.0, f"errore Wikidata: {e}", ""

    for it in risultati:
        descrizione = (it.get("description") or "").lower()
        if not any(p in descrizione for p in professioni):
            continue
        etichetta = it.get("label", "")
        # Il nome trovato deve condividere parole con quello cercato: vedi il caso
        # "Enrico" -> "Heinrich Heine" nel docstring.
        if _similarita_autore(nome, etichetta) < 0.5:
            continue
        url = f"https://it.wikipedia.org/wiki/{etichetta.replace(' ', '_')}"
        return 1.0, f"{etichetta} - {it.get('description','')}", url
    return 0.0, "nessun autore reale con questo nome per la categoria", ""


def _cron_completa_autore_dal_db(titolo: str, categoria: str, tmdb_key: str = "") -> str:
    """Dato un titolo GIA' confermato ma senza autore, chiede al database chi sia
    l'autore. Ritorna il nome trovato, o "" se il database non lo espone.

    Deciso con l'utente il 2026-07-27: se abbiamo il titolo e non l'autore, il caso
    si risolve col database invece di scartare la voce — se il titolo esiste, si
    completa l'associazione e si considera la voce chiusa. Viene chiamata solo per
    le voci confermate con autore vuoto (~13% delle confermate), quindi il costo in
    chiamate extra e' limitato a quelle.

    Non inventa nulla: se il database non restituisce un autore, resta vuoto."""
    try:
        if categoria == "libro":
            r = requests.get("https://openlibrary.org/search.json", timeout=15,
                             headers={"User-Agent": USER_AGENT},
                             params={"q": titolo, "limit": 5, "fields": "title,author_name"})
            r.raise_for_status()
            for d in r.json().get("docs", []):
                if _similarita(titolo, d.get("title", "")) >= SOGLIA_TITOLO_CERTO:
                    autori = d.get("author_name") or []
                    if autori:
                        return autori[0]
            # Open Library e' debole sull'editoria italiana: si riprova con Google Books,
            # che per "Anna" di Ammaniti da' l'autore giusto dove OL da' un omonimo.
            p, _desc, _cop, autore_gb = cerca_google_books(titolo, "")
            if p >= SOGLIA_TITOLO_CERTO and autore_gb:
                return autore_gb
        elif categoria == "film":
            risultati = _tmdb_cerca("movie", titolo, tmdb_key)
            for res in risultati[:3]:
                if _similarita(titolo, res.get("title", "") or "") >= SOGLIA_TITOLO_CERTO:
                    registi = _tmdb_registi(res.get("id"), tmdb_key)
                    if registi:
                        return registi[0]
        else:
            # ⚠️ MUSICA: completamento automatico NON fatto, deliberatamente.
            # Provate dal vivo due strategie il 2026-07-27, entrambe sbagliate in modo
            # opposto: prendendo il primo risultato per rilevanza si ottengono le COVER
            # ("Chasing Cars" -> Vitamin String Quartet, "Sapore di sale" -> Dik Dik);
            # ordinando per data di prima pubblicazione si ottengono gli OMONIMI piu'
            # antichi ("Locked Away" -> Keith Richards, che ha una canzone omonima del
            # 1988; "Come Rain or Come Shine" -> Scott Hamilton). Su un titolo musicale
            # generico MusicBrainz non permette di distinguere l'incisione originale
            # senza altre informazioni.
            # Un autore sbagliato e' peggio di nessun autore: si propaga nell'archivio
            # come se fosse verificato. Queste voci restano con l'autore vuoto e vanno
            # alla revisione manuale, dove un occhio umano decide in due secondi.
            return ""
    except Exception as e:
        print(f"      (autore non recuperabile dal database: {e})")

    # Terza fonte, per libri e film/serie: i claim strutturati di Wikidata. Copre i due
    # buchi misurati nelle prime due (schede Open Library senza autore, 503 intermittenti
    # di Google Books) e in piu' le SERIE, che il ramo film non cercava affatto:
    # _tmdb_cerca interroga solo "movie", quindi il creatore di una serie non era
    # raggiungibile da nessuna strada. La musica resta esclusa: vedi sopra.
    if categoria != "musica":
        return _wikidata_autore_opera(titolo, categoria)
    return ""


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

    # Un archivio che non risponde non deve poter emettere una condanna. Misurato il
    # 2026-07-28: Google Books da' 503 a intermittenza (1 richiesta su 6 in una
    # raffica, chiave presente) e "I fili invisibili della natura", libro reale
    # confermato pochi minuti prima, diventava "scartato (0.00)" - cioe' la voce veniva
    # marcata definitivamente come probabile falso positivo sulla base di una domanda
    # mai arrivata. Il principio era gia' scritto qui sotto ("un punteggio negativo non
    # fa mai scartare una voce") ma valeva solo DENTRO il confronto: il verdetto finale
    # lo ignorava.
    non_raggiungibile = punteggio < 0

    tentativi = []
    if categoria == "libro":
        tentativi.append(("google books", lambda: cerca_google_books(titolo, autore)))
    if categoria == "musica":
        # Credits.fm prima di Wikidata: e' specifico della musica, molto piu' veloce
        # (300 lookup/min con chiave, contro 1/s di MusicBrainz) e copre proprio dove
        # MusicBrainz sbaglia, cioe' quando restituisce una cover invece dell'incisione.
        tentativi.append(("credits.fm", lambda: cerca_credits_fm(titolo, autore)))
    tentativi.append(("wikidata", lambda: cerca_wikidata(titolo, autore, categoria)))

    for nome, cerca in tentativi:
        try:
            risultato = cerca()
        except Exception as e:
            # Rete/HTTP/risposta non-JSON: atteso e non fatale, si prova il prossimo.
            print(f"      ({nome} non interrogabile: {e})")
            continue
        # Spacchettamento FUORI dal try, deliberatamente: se l'arita' del risultato
        # cambia, l'errore deve farsi sentire invece di somigliare a un problema di
        # rete. Successo davvero il 2026-07-27: i return di cerca_google_books e
        # cerca_wikidata passarono da 3 a 4 valori, l'except generico trasformo' il
        # ValueError in una riga innocua e TUTTO il fallback multi-database resto'
        # disattivato in silenzio. Stessa famiglia del bug Groq: "fallito" non deve
        # mai somigliare a "non trovato".
        p, d, c, _ = risultato
        if p < 0:
            # Non raggiungibile: non e' una prova di inesistenza, si prosegue.
            non_raggiungibile = True
            continue
        if p > punteggio:
            punteggio, descrizione, copertina = p, f"{d} [via {nome}]", (copertina or c)

    if punteggio < SOGLIA_BASSA and non_raggiungibile:
        # Sotto SOGLIA_BASSA e con un archivio muto: il verdetto sarebbe "probabile
        # falso positivo", che NON e' innocuo — pulisci_riferimenti_non_confermati.py
        # rimuove quelle voci dal dataset. Si sospende e la voce torna in coda per un
        # run futuro. Sopra SOGLIA_BASSA invece si lascia "dubbio": nessuna voce viene
        # persa, finisce solo in revisione, quindi non vale la pena rimandare.
        return -1.0, f"{descrizione} [verdetto sospeso: un archivio non ha risposto]", copertina, sottocat
    return punteggio, descrizione, copertina, sottocat


def giudica_voce(titolo: str, autore: str, categoria: str,
                 tmdb_key: str = "") -> tuple[float, str, str, str, str]:
    """Giudizio completo di UNA voce: database principale, database di riserva e
    guardrail. Ritorna (punteggio, match, copertina, sottocategoria, link_autore).

    Esiste come funzione a se' perche' il banco di prova della verifica
    (scripts/linux/test_verifica_esterna.py) deve giudicare ESATTAMENTE come la
    produzione: se il banco riscrivesse la stessa sequenza per conto suo, misurerebbe
    una catena che non e' quella che gira davvero, e la misura varrebbe zero il giorno
    in cui le due copie divergono. E' proprio quello che e' successo il 2026-07-28
    con rivaluta_dubbi_esterni.py, che chiamava i database uno per uno saltando il
    fallback.

    Punteggio -1.0 = non ho potuto chiedere, vedi cerca_google_books()."""
    if not titolo:
        # Voce di SOLO AUTORE: non c'e' un'opera da cercare, si verifica che il nome
        # sia davvero un autore reale della categoria.
        punteggio, match, url = verifica_autore(autore, categoria)
        return punteggio, match, "", "", url

    if categoria == "libro":
        primo = verifica_libro(titolo, autore)
        time.sleep(0.35)  # margine sotto ~3 richieste/secondo
    elif categoria == "film":
        primo = verifica_film(titolo, autore, tmdb_key)
        time.sleep(0.05)
    else:
        primo = verifica_musica(titolo, autore)
        time.sleep(MUSICBRAINZ_SLEEP)

    punteggio, match, copertina, sub_suggerita = verifica_con_fallback(
        titolo, autore, categoria, primo)

    # Veto applicato DOPO tutta la catena, non dentro un singolo database: un titolo
    # musicale senza autore che coincide con un nome d'artista non e' un'opera, e
    # ognuno dei database lo conferma per una strada diversa. MusicBrainz trova la
    # registrazione omonima (tributi, compilation), Wikidata trova l'album eponimo
    # ("Bob Dylan" e' davvero un album del 1962) o direttamente la band. Misurato il
    # 2026-07-28: mettendo il controllo solo dentro verifica_musica, 4 casi su 7
    # rientravano lo stesso dal fallback.
    if categoria == "musica" and titolo and not autore and punteggio >= SOGLIA_ALTA:
        if _musicbrainz_e_nome_artista(titolo):
            punteggio = min(punteggio, SOGLIA_BASSA - 0.01)
            match = (f"{titolo} risulta un ARTISTA, non un'opera: il nome e' finito nel "
                     f"campo del titolo e non c'e' un'opera da verificare")
            copertina, sub_suggerita = "", ""
        else:
            # Nessun archivio puo' confermare un titolo musicale senza autore: vedi la
            # nota estesa in verifica_musica(). Il cap va ripetuto QUI perche' il
            # fallback puo' rialzare il punteggio dopo — misurato su "Velvet
            # Underground", che Wikidata riconferma come album eponimo del 1969.
            punteggio = min(punteggio, SOGLIA_ALTA - 0.01)
            match += " [senza autore: alla revisione]"
            copertina = ""

    # Trovato 2026-07-22 nel run reale sul backlog: "Ray Charles"/autore="Ray Charles"
    # e "Lucio Dalla"/autore="Lucio Dalla" confermati automaticamente perche' il
    # database esterno (MusicBrainz include tributi e compilation col nome
    # dell'artista come titolo) trova un "match" che pero' dice solo "l'artista
    # esiste", non "e' un'opera specifica citata". Se titolo e autore normalizzati
    # sono uguali, non fidarsi MAI del punteggio esterno, per quanto alto sia.
    titolo_norm = _normalizza(titolo)
    if titolo_norm and titolo_norm == _normalizza(autore):
        punteggio = min(punteggio, SOGLIA_ALTA - 0.01)

    return punteggio, match, copertina, sub_suggerita, ""


def deve_incrociare(titolo: str, autore: str, confermato: bool, match: str) -> bool:
    """Vale la pena cercare la stessa opera anche nelle altre categorie?

    Sta qui, e non dentro main(), perche' il banco di prova deve poter misurare la
    STESSA condizione che gira in produzione: e' una guardia che protegge da un
    guasto reale, quindi va provata, non ricopiata.

    Serve la coppia COMPLETA titolo + autore, e basta quella. Due usi:
    - voce confermata -> lo stesso nome puo' appartenere anche a un altro archivio, e
      allora nasce la gemella (Don Camillo film E libro);
    - voce non confermata -> la categoria estratta puo' essere sbagliata, e viene
      corretta invece di buttare la voce.

    L'autore e' obbligatorio in entrambi i casi, ed e' la parte che protegge: senza,
    "Pink Floyd" e "Solare" (voci musica senza autore, inverificabili per definizione)
    venivano ritrovate come LIBRO - esistono biografie omonime - e riconfermate con la
    categoria sbagliata. Misurato il 2026-07-28 sull'archivio vero.

    Il parametro `match` non serve piu' alla decisione ma resta nella firma perche' il
    chiamante lo ha e una guardia che un domani volesse guardarlo non debba cambiare
    di nuovo l'interfaccia: e' proprio un cambio d'arita' non propagato che il 27/07
    ha spento in silenzio l'intero fallback multi-database."""
    del confermato, match  # la decisione oggi non li usa, vedi docstring
    return bool(titolo) and bool(autore.strip())


def verifica_categorie_incrociate(titolo: str, autore: str, categoria_estratta: str,
                                  tmdb_key: str = "") -> list[tuple[str, float, str, str, str]]:
    """Cerca la stessa coppia titolo/autore anche nelle ALTRE categorie.

    Regola impostata dall'utente il 2026-07-28: davanti a un'ambiguita' che non
    sappiamo risolvere, se il dato estratto e' certo ed esiste nei database, e lo
    stesso nome richiama piu' archivi, si riportano ENTRAMBE le voci, ciascuna
    associata alla propria categoria. "Don Camillo" e' un film del 1952 ed e' anche
    il libro di Guareschi da cui il film e' tratto: sceglierne uno solo e' una perdita
    di informazione, e sceglierlo male e' un errore. Non e' un caso di scuola - e'
    proprio il motivo per cui Don Camillo veniva scartato: TMDB confermava il film ma
    controllava il regista, e Guareschi e' lo scrittore.

    Ritorna una lista di (categoria, punteggio, match, copertina, sottocategoria) per
    OGNI categoria diversa da quella estratta che conferma sopra SOGLIA_ALTA. Lista
    vuota se nessuna: e' il caso normale e non costa nulla al chiamante.

    ⚠️ L'altra categoria vale SOLO se conferma anche l'AUTORE. Senza questo vincolo,
    provata sull'archivio vero su 12 episodi, la regola produceva accostamenti falsi:
    "Kansas City"/Mario Monicelli finiva in musica (la canzone esiste davvero, il
    regista non c'entra), "House of Cards"/Beau Willimon veniva spostato a libro (il
    romanzo e' di Michael Dobbs), "Flipper" e "The Flintstones" lo stesso. Il titolo
    da solo combacia in troppi archivi: e' la corrispondenza autore-titolo a rendere
    certa l'identificazione, ed e' esattamente cio' che la regola dell'utente chiede.
    Conseguenza accettata: senza un autore estratto non nasce nessuna gemella, per
    quanto il titolo esista altrove.

    ⚠️ SOLO libro <-> film. La musica e' esclusa in ENTRAMBE le direzioni, misurato
    sull'archivio vero (seconda fetta di 12 episodi): "Yesterday"/The Beatles,
    "Thriller"/Michael Jackson e "True Colors"/Cyndi Lauper diventavano LIBRI. Non e'
    un difetto di soglia e non si aggiusta stringendo: dei libri SU un artista esistono
    davvero e sono accreditati all'artista, quindi il controllo dell'autore - che
    protegge le altre direzioni - qui corrobora sempre ed e' cieco. Fra libro e film
    invece l'adattamento e' un fatto che gli archivi registrano (Don Camillo, Il
    padrino)."""
    if not titolo or not autore.strip():
        return []
    if categoria_estratta == "musica":
        return []
    altre = [c for c in ("libro", "film") if c != categoria_estratta]
    trovate = []
    for cat in altre:
        try:
            punteggio, match, copertina, sub, _url = giudica_voce(titolo, autore, cat, tmdb_key)
        except Exception as e:
            print(f"      (categoria {cat} non interrogabile: {e})")
            continue
        autore_confermato = (MARCA_AUTORE_NON_CORROBORATO not in match
                             and "autore mai estratto" not in match)
        if punteggio >= SOGLIA_ALTA and autore_confermato:
            trovate.append((cat, punteggio, match, copertina, sub))
    return trovate


def _cron_verifica_libro(titolo: str, autore: str) -> tuple[float, str, str, str]:
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


# Mestieri che rendono una persona un "autore" credibile di un film. Non solo il
# regista: in un programma che parla di libri, il nome citato accanto a un film e'
# spessissimo lo SCRITTORE del romanzo da cui e' tratto. Misurato il 2026-07-28 sul
# banco: con i soli registi, "Il padrino"/Mario Puzo e "Don Camillo"/Guareschi non
# potevano essere confermati come film - Puzo firma la sceneggiatura, Guareschi il
# romanzo - ed e' proprio la coppia titolo/autore che il programma pronuncia in onda.
CREDITI_AUTORE_FILM = ("Director", "Screenplay", "Writer", "Novel", "Author",
                       "Story", "Original Story", "Book")


def _tmdb_registi(movie_id: int, tmdb_key: str) -> list[str]:
    """Chi ha fatto un film, secondo TMDB: regista, sceneggiatore, autore del romanzo.
    Serve una chiamata separata a /credits: l'endpoint di ricerca non restituisce i
    crediti, ed e' esattamente il motivo per cui fino al 2026-07-26 verifica_film()
    non poteva controllare l'autore (vedi sotto)."""
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
    return [c.get("name", "") for c in crew if c.get("job") in CREDITI_AUTORE_FILM]


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


def _cron_verifica_film(titolo: str, autore: str, tmdb_key: str) -> tuple[float, str, str, str]:
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


# Dischi (release-group) sotto i quali l'omonimia non conta. Non e' un numero scelto a
# occhio: misurato il 2026-07-28 su 13 casi reali del corpus, i due insiemi si separano
# senza sovrapposizione a 20. Artisti davvero citati in onda: Sons... no, artisti ->
# Little Tony 25, Nick Drake 28, Nickelback 70, Janis Joplin 123, Barry White 153,
# Vangelis 161. Titoli plausibili con un omonimo oscuro: Monday Morning 1, Imani 1,
# Cordyceps 3, Chanel 7, Superheroes 9, Sons and Daughters 15.
SOGLIA_ARTISTA_NOTO = 20


def _cron__musicbrainz_e_nome_artista(nome: str) -> bool:
    """Il presunto titolo e' in realta' il nome di un artista NOTO?

    Interroga l'entita' /artist, che verifica_musica non ha mai usato (usa solo
    /recording). Riconosce Bob Dylan, Metallica, Velvet Underground, Nick Drake,
    Vangelis, e non scatta su Chasing Cars, Sapore di sale, Locked Away, La traviata.

    La notorieta' NON e' un dettaglio: senza, il filtro e' troppo largo. Misurato il
    2026-07-28 su 60 voci vere del corpus, il solo controllo del nome revocava anche
    "Superheroes", "Monday Morning", "Sons and daughters", "Imani", "Chanel" — titoli
    plausibili che su MusicBrainz coincidono con artisti oscuri, perche' esiste un
    artista per quasi ogni parola comune. Il conteggio dei dischi separa i due gruppi
    senza sovrapposizione: noti 123-1263 (Janis Joplin, Pink Floyd, Bob Dylan, U2),
    omonimi oscuri 1-15. Quando il nome e' quello di un artista famoso e l'autore non
    e' stato estratto, cio' che e' stato nominato in onda e' quasi sempre l'artista."""
    if not nome.strip():
        return False
    # Primo gradino, una chiamata sola e cinque volte piu' veloce: se Credits.fm
    # risponde con quell'artista, e' deciso e si risparmiano le due chiamate a
    # MusicBrainz. Zero falsi allarmi misurati su 7 titoli veri. Se non lo riconosce
    # (non prende "Bob Dylan" ne' "Velvet Underground") si prosegue col controllo
    # completo: e' un acceleratore, non una scorciatoia sulla qualita'.
    if _credits_fm_titolo_e_artista(nome) is True:
        return True
    try:
        r = requests.get("https://musicbrainz.org/ws/2/artist", timeout=20,
                         headers={"User-Agent": USER_AGENT},
                         params={"query": f'artist:"{nome}"', "fmt": "json", "limit": 5})
        r.raise_for_status()
        artisti = r.json().get("artists", [])
        time.sleep(MUSICBRAINZ_SLEEP)
        # Si guardano TUTTI i candidati col nome giusto, non il primo: MusicBrainz
        # ordina per rilevanza della stringa, non per notorieta', e per "Velvet
        # Underground" il primo risultato e' una band australiana omonima del 1967 con
        # un solo disco. Fermarsi al primo faceva sembrare oscuro un nome famoso.
        for a in artisti:
            if _similarita_autore(nome, a.get("name", "")) < 0.9 or not a.get("id"):
                continue
            r2 = requests.get("https://musicbrainz.org/ws/2/release-group", timeout=20,
                              headers={"User-Agent": USER_AGENT},
                              params={"artist": a["id"], "fmt": "json", "limit": 1})
            r2.raise_for_status()
            time.sleep(MUSICBRAINZ_SLEEP)
            if r2.json().get("release-group-count", 0) >= SOGLIA_ARTISTA_NOTO:
                return True
    except Exception:
        # Non ho potuto chiedere: non e' una prova che sia un titolo, non si filtra.
        return False
    return False


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


def _cron_verifica_musica(titolo: str, autore: str) -> tuple[float, str, str, str]:
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
        # Un titolo musicale senza autore che coincide con un NOME D'ARTISTA non e' un
        # titolo: e' l'artista, che il modello ha messo nel campo sbagliato. MusicBrainz
        # da solo non se ne accorge mai, perche' per quasi ogni nome famoso esiste una
        # registrazione omonima (tributi, compilation, bootleg) — quindi confermava.
        # Misurato sul corpus il 2026-07-28: 504 voci musica senza autore, 268 gia'
        # CONFERMATE, e leggendone un campione a mano la maggior parte e' di questo
        # tipo: "Bob Dylan", "Metallica", "Velvet Underground", "Nick Drake",
        # "Vangelis". Il guardrail titolo==autore non poteva vederle: l'autore e' vuoto.
        # Il veto sui nomi d'artista sta in giudica_voce(), a valle di TUTTA la catena:
        # metterlo solo qui lasciava rientrare dal fallback 4 casi su 7 (misurato).
        #
        # ⚠️ UN TITOLO MUSICALE SENZA AUTORE NON SI CONFERMA MAI DA SOLO (deciso con
        # l'utente il 2026-07-28). A differenza dei libri, dove Open Library e Google
        # Books rispondono su un titolo preciso, MusicBrainz ha una registrazione per
        # quasi ogni stringa breve: misurato sul corpus, 268 voci di questo tipo erano
        # gia' confermate e leggendone un campione la maggior parte non e' musica
        # citata ("Solare", "Chris", "Tirelli", "Suddenly", versi di sigle, nomi
        # propri). Confermare sul solo titolo qui non misura l'esistenza dell'opera,
        # misura la vastita' del catalogo. Restano quindi in DUBBIO: la voce non viene
        # persa (solo i "probabile falso positivo" vengono rimossi), va in revisione.
        # E' il primo punto in cui si accetta il limite invece di forzarlo.
        if miglior_sim_titolo >= SOGLIA_TITOLO_SENZA_AUTORE:
            return (min(max(migliore[0], SOGLIA_BASSA + 0.01), SOGLIA_ALTA - 0.01),
                    migliore[1] + " [titolo musicale senza autore: esiste qualcosa con "
                                  "questo nome, ma nessun archivio puo' dire che sia "
                                  "l'opera citata - va alla revisione]",
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
    parser.add_argument("--rifai", action="store_true",
                        help="rigiudica anche le voci gia' marcate. Serve dopo un "
                             "rinforzo della verifica: normalmente lo script salta chi "
                             "ha gia' 'confermato_esterno', quindi un miglioramento "
                             "vale solo per le estrazioni future e l'archivio resta "
                             "fermo al giudizio del giorno in cui fu scritto.")
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
            # Basta titolo O autore: dal 2026-07-27 esistono voci di solo autore
            # (nominato in onda senza titolo), che vanno verificate come tali.
            ha_qualcosa = (r.get("titolo") or "").strip() or (r.get("autore") or "").strip()
            gia_giudicata = "confermato_esterno" in r
            # Le voci gemelle nate dalla verifica incrociata non si rigiudicano: sono
            # gia' il RISULTATO di una verifica, e rimetterle in coda le farebbe
            # moltiplicare a ogni passata.
            if r.get("da_categoria_incrociata"):
                continue
            if ha_qualcosa and r.get(campo) in mappa and (args.rifai or not gia_giudicata):
                tutte_le_voci.append((fp, r))

    if args.limit:
        tutte_le_voci = tutte_le_voci[:args.limit]

    print(f"Verifico {len(tutte_le_voci)} voci ({args.dataset}) contro database esterni reali (Open Library/TMDB/MusicBrainz)...")

    dubbi = []
    confermati = 0
    scartati = 0
    completati_autore = 0  # titoli confermati il cui autore e' stato messo dal database
    incrociate = 0         # voci gemelle aggiunte in un'altra categoria
    corrette_categoria = 0  # voci la cui categoria era sbagliata e il db l'ha corretta
    per_file: dict[Path, list[dict]] = {}
    nuove_per_file: dict[Path, list[dict]] = {}
    # Dalla categoria (libro/film/musica) al valore che il dataset scrive nel suo campo
    # ("categoria" per i riferimenti, "tipo" con prefisso per i frammenti).
    inversa = {v: k for k, v in mappa.items()}

    for i, (fp, r) in enumerate(tutte_le_voci):
        categoria = mappa[r[campo]]
        titolo = (r.get("titolo") or "").strip()
        autore = r.get("autore", "")
        try:
            # Stessa identica catena usata dal banco di prova della verifica.
            punteggio, match, copertina, sub_suggerita, url_autore = giudica_voce(
                titolo, autore, categoria, tmdb_key)
        except Exception as e:
            print(f"  [{i+1}/{len(tutte_le_voci)}] ERRORE imprevisto su {titolo!r}: {e}, salto")
            continue

        if punteggio < 0:
            # Errore di rete: non scrivere nulla, riprovare in un run futuro.
            continue

        # Voce di solo autore confermata: si conserva il collegamento alle sue opere
        # invece di buttare via tutto (deciso con l'utente il 2026-07-27).
        if url_autore and punteggio >= SOGLIA_ALTA:
            r["link_autore"] = url_autore
            r["solo_autore"] = True

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
        # Titolo confermato ma autore mai estratto: lo completa il database, e la voce
        # e' chiusa (deciso con l'utente il 2026-07-27). Prima restava a meta': un
        # titolo verificato con un campo autore vuoto, indistinguibile da un errore.
        if r["confermato_esterno"] and not (r.get("autore") or "").strip():
            trovato = completa_autore_dal_db(titolo, categoria, tmdb_key)
            if trovato:
                r["autore"] = trovato
                r["autore_dal_database"] = True
                completati_autore += 1
        # VERIFICA INCROCIATA FRA CATEGORIE (regola dell'utente, 2026-07-28).
        # Scatta solo sull'ambiguita' vera, non su ogni voce: se il titolo e' stato
        # confermato E l'autore ha trovato riscontro, non c'e' nulla da disambiguare e
        # si risparmiano due interrogazioni per voce su tutto l'archivio. Scatta
        # quando il titolo regge ma l'autore no (Don Camillo: TMDB conferma il film e
        # controlla il regista, ma Guareschi e' lo scrittore del libro) o quando la
        # categoria estratta non conferma affatto (il modello puo' averla sbagliata).
        # La guardia sta in deve_incrociare(), cosi' il banco misura la stessa
        # condizione che gira qui invece di una sua copia.
        if deve_incrociare(titolo, autore, r["confermato_esterno"], match):
            for cat_alt, p_alt, m_alt, cop_alt, sub_alt in verifica_categorie_incrociate(
                    titolo, autore, categoria, tmdb_key):
                if not r["confermato_esterno"]:
                    # La categoria estratta non reggeva e un'altra si': non e' un
                    # doppione, e' una correzione. La voce cambia categoria.
                    r[campo] = inversa.get(cat_alt, cat_alt)
                    r["confermato_esterno"] = True
                    r["categoria_corretta_dal_db"] = True
                    if cop_alt:
                        r["copertina"] = cop_alt
                    if sub_alt and not (r.get("sottocategoria") or "").strip():
                        r["sottocategoria"] = sub_alt
                    match = m_alt
                    corrette_categoria += 1
                    continue
                # La voce regge gia' nella sua categoria e lo stesso nome esiste anche
                # in un altro archivio: si riportano ENTRAMBE, ciascuna con la propria
                # categoria, invece di sceglierne una a caso.
                gemella = dict(r)
                gemella["id"] = f"{r.get('id')}-{cat_alt}"
                gemella[campo] = inversa.get(cat_alt, cat_alt)
                gemella["confermato_esterno"] = True
                gemella["copertina"] = cop_alt
                gemella["sottocategoria"] = sub_alt
                gemella["da_categoria_incrociata"] = True
                gemella.pop("autore_dal_database", None)
                nuove_per_file.setdefault(fp, []).append(gemella)
                incrociate += 1

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

    campi_write_back = CAMPI_PERSISTITI + (campo, "categoria_corretta_dal_db")
    for fp in set(per_file) | set(nuove_per_file):
        dati = json.loads(fp.read_text(encoding="utf-8"))
        by_id = {r.get("id"): r for r in per_file.get(fp, [])}
        for r in dati:
            aggiornata = by_id.get(r.get("id"))
            if aggiornata is None:
                continue
            for campo_scritto in campi_write_back:
                if campo_scritto in aggiornata:
                    r[campo_scritto] = aggiornata[campo_scritto]
        # Voci gemelle nate dalla verifica incrociata: si aggiungono, senza duplicare
        # se un run precedente le aveva gia' create (l'id e' deterministico apposta).
        gia_presenti = {r.get("id") for r in dati}
        for gemella in nuove_per_file.get(fp, []):
            if gemella.get("id") not in gia_presenti:
                dati.append(gemella)
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

    stampa_tempi()

    autori = f", {completati_autore} autori completati dal database" if completati_autore else ""
    extra = f", {incrociate} voci gemelle in un'altra categoria" if incrociate else ""
    extra += f", {corrette_categoria} categorie corrette dal database" if corrette_categoria else ""
    print(f"\nFatto. {confermati} confermate automaticamente, {scartati} probabili falsi positivi, "
          f"{len(dubbi) - scartati} dubbie{autori}{extra} - report completo in {report_path} "
          f"({len(fuso)} voci totali). NON cancellato nulla, solo segnalato/marcato.")


def verifica_libro(titolo: str, autore: str):
    with cronometra('open library'):
        return _cron_verifica_libro(titolo, autore)


def cerca_google_books(titolo: str, autore: str):
    with cronometra('google books'):
        return _cron_cerca_google_books(titolo, autore)


def verifica_film(titolo: str, autore: str, tmdb_key: str):
    with cronometra('tmdb'):
        return _cron_verifica_film(titolo, autore, tmdb_key)


def verifica_musica(titolo: str, autore: str):
    with cronometra('musicbrainz'):
        return _cron_verifica_musica(titolo, autore)


def cerca_wikidata(titolo: str, autore: str, categoria: str):
    with cronometra('wikidata'):
        return _cron_cerca_wikidata(titolo, autore, categoria)


def cerca_credits_fm(titolo: str, autore: str):
    with cronometra('credits.fm'):
        return _cron_cerca_credits_fm(titolo, autore)


def verifica_autore(nome: str, categoria: str):
    with cronometra('wikidata autore'):
        return _cron_verifica_autore(nome, categoria)


def completa_autore_dal_db(titolo: str, categoria: str, tmdb_key: str = ""):
    with cronometra('completa autore'):
        return _cron_completa_autore_dal_db(titolo, categoria, tmdb_key)


def _musicbrainz_e_nome_artista(nome: str):
    with cronometra('veto artista'):
        return _cron__musicbrainz_e_nome_artista(nome)

if __name__ == "__main__":
    main()
