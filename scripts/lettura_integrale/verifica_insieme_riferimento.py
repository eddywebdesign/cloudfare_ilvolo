#!/usr/bin/env python3
"""Il ground truth deve dimostrare se stesso: ogni voce ancorata a un segmento reale.

PERCHE' ESISTE QUESTO SCRIPT
Il 2026-07-31 e' stato misurato che insieme_riferimento.json non era verificabile:
  - 45 opere su 101 senza NESSUNA citazione: dicevano "quest'opera e' in questa
    puntata" senza dire dove. Non controllabili da nessuno, nemmeno a posteriori.
  - 6 citazioni su 56 NON esistono nel testo: ricostruite invece che copiate,
    con segmenti lontani incollati e parole tolte senza segno (l'esempio peggiore
    e' "Believer" 2017-05-16, che salta i segmenti 7-8-9 e cancella sette parole).
  - Il file certificava se stesso: la sua documentazione dichiarava "letto per
    intero da un umano" mentre era stato compilato in sessioni Claude.

Le opere c'erano davvero: il difetto era nella PROVA, non nella sostanza. Ma quando
la prova e' fabbricata non si distinguono piu' le voci giuste da quelle sbagliate,
e vanno ricontrollate tutte. Il difetto e' rimasto invisibile quattro giorni perche'
non esisteva nessun controllo: questo script e' quel controllo, e per scelta viene
scritto PRIMA della rilettura, non dopo.

LA REGOLA
Ogni voce (opere, marginali) porta 'segmento' e 'citazione', e la
citazione deve essere sottostringa LETTERALE del testo di quel segmento. Niente
"[...]" a nascondere un salto: due punti distanti sono due voci separate. Un
intervallo di segmenti CONSECUTIVI e' ammesso (una frase puo' spezzarsi in tre
righe), ma allora la citazione deve contenere anche il testo in mezzo, tutto.

Uso:
    python3 scripts/linux/verifica_insieme_riferimento.py
    python3 scripts/linux/verifica_insieme_riferimento.py --file altro.json
    python3 scripts/linux/verifica_insieme_riferimento.py --diagnosi

--diagnosi legge anche i file nel formato VECCHIO (senza 'segmento') e riporta la
scomposizione che ha motivato tutto questo: quante voci senza prova, quante con una
citazione che si ritrova nel testo, quante con una citazione che non esiste.

Esce 1 se qualcosa non torna, 0 se il file si regge in piedi.
NIENTE caratteri fuori ASCII nell'output.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dati_root import dati_root  # noqa: E402

# Il ground truth VECCHIO vive ancora in scripts/linux/, dove il banco lo legge. Questa
# fase e' un banco a se' (decisione utente 2026-08-01: "NON CONTAMINARE il resto"): lo
# legge in sola lettura e non lo sostituisce. Il risultato della rilettura andra' in un
# file NUOVO, e l'adozione sara' un atto separato e deliberato.
GT_DEFAULT = Path(__file__).resolve().parent.parent / "linux" / "insieme_riferimento.json"
CATEGORIE_AMMESSE = {"libro", "film", "musica", "arte", "tema"}
LIVELLI_AMMESSI = {"opera", "autore", "tema"}
# Il tetto era 5 quando una voce era sempre un'opera con un titolo pronunciato in un
# punto preciso. Con lo schema del 2026-08-02 non regge piu': una voce 'tema' copre per
# natura un blocco (la bellezza su 2013-11-27 sta ai segmenti 17-24), e anche un'opera
# puo' svilupparsi su un tratto lungo (Hey Joe: compleanno al 186, annuncio al 188,
# versi al 190-194).
#
# Alzarlo NON indebolisce la difesa contro le citazioni fabbricate: quella e' il
# confronto LETTERALE, che resta intatto e che ha gia' intercettato due citazioni
# inventate. Il tetto serviva solo a evitare che 'segmento' diventasse un'etichetta
# vaga; 40 lo tiene un riferimento localizzabile senza mutilare i temi.
MAX_SEGMENTI_PER_CITAZIONE = 40
INSIEMI = ("opere", "marginali")


def norm(testo):
    """Confronto tollerante alla forma, intransigente sulle parole.

    Accenti, maiuscole e punteggiatura non sono la sostanza di una citazione: la
    trascrizione scrive "perche'" o "perche", "E'" o "e'". Le PAROLE invece devono
    esserci tutte e nello stesso ordine, ed e' esattamente cio' che questo confronto
    lascia intatto."""
    testo = unicodedata.normalize("NFKD", testo.lower())
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    testo = testo.replace("'", " ").replace("’", " ")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", testo).split())


def carica_segmenti(data_ep):
    """Testo dei segmenti di una puntata, nell'ordine, indicizzato da 0."""
    percorso = dati_root(ROOT) / "trascrizioni" / f"{data_ep}.json"
    if not percorso.exists():
        return None
    dati = json.loads(percorso.read_text(encoding="utf-8"))
    return [s.get("text", "") for s in dati.get("segments", [])]


def intervallo(voce):
    """'segmento' e' un indice singolo oppure [inizio, fine] di segmenti CONSECUTIVI.

    Ritorna (inizio, fine, errore)."""
    seg = voce.get("segmento")
    if seg is None:
        return None, None, "manca il campo 'segmento'"
    if isinstance(seg, bool):
        return None, None, f"'segmento' malformato: {seg!r}"
    if isinstance(seg, int):
        return seg, seg, None
    if (isinstance(seg, list) and len(seg) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in seg)):
        a, b = seg
        if b < a:
            return None, None, f"intervallo rovesciato {seg}"
        if b - a + 1 > MAX_SEGMENTI_PER_CITAZIONE:
            return None, None, (f"intervallo di {b - a + 1} segmenti, il massimo e' "
                                f"{MAX_SEGMENTI_PER_CITAZIONE}: una citazione cosi' lunga "
                                f"non e' una prova, e' un'area")
        return a, b, None
    return None, None, f"'segmento' malformato: {seg!r}"


def verifica_voce(voce, segmenti, insieme):
    """Errori di una singola voce. Lista vuota = la voce si regge."""
    errori = []

    # Nel formato vecchio i marginali erano stringhe nude ("Le Iene, programma TV").
    # Una voce che non e' un oggetto non ha dove tenere segmento e citazione: e' un
    # errore da riportare, non un'eccezione che ferma il controllo di tutto il resto.
    if not isinstance(voce, dict):
        return [f"voce non e' un oggetto ma {type(voce).__name__}: {str(voce)[:70]!r}"]

    if insieme == "opere":
        cat = voce.get("categoria")
        if cat not in CATEGORIE_AMMESSE:
            errori.append(f"categoria {cat!r} fuori da {sorted(CATEGORIE_AMMESSE)}")
        # Tre livelli, e cambia COSA e' obbligatorio. 'opera' ha la coppia completa;
        # 'autore' e' il caso in cui l'accoppiata manca ma il richiamo e' forte e se ne
        # parla (i Pooh raccontati nell'infanzia), e li' l'obbligatorio e' l'autore;
        # 'tema' e' la riflessione culturale senza opera catalogata (la bellezza su
        # 2013-11-27), e li' l'obbligatorio e' il titolo del tema.
        livello = voce.get("livello")
        if livello not in LIVELLI_AMMESSI:
            errori.append(f"livello {livello!r} fuori da {sorted(LIVELLI_AMMESSI)}")
        elif livello == "opera" and not (voce.get("titolo") and voce.get("autore")):
            errori.append("livello 'opera' vuole titolo E autore: se ne manca uno il"
                          " livello e' 'autore'")
        elif livello == "autore" and not voce.get("autore"):
            errori.append("livello 'autore' senza autore: non resta niente da agganciare")
        elif livello == "tema" and not voce.get("titolo"):
            errori.append("livello 'tema' senza titolo: serve dire di che tema si tratta")
        if (cat == "tema") != (livello == "tema"):
            errori.append(f"categoria {cat!r} e livello {livello!r} non si accordano:"
                          " 'tema' vale per entrambi o per nessuno dei due")
    if insieme == "marginali" and not voce.get("motivo_esclusione"):
        errori.append("manca 'motivo_esclusione': un'esclusione senza motivo non e'"
                      " verificabile e si ridiscute ogni volta")

    citazione = voce.get("citazione")
    if not citazione:
        errori.append("manca la citazione: la voce non e' verificabile")
        return errori
    if "[...]" in citazione:
        errori.append("la citazione contiene '[...]': due punti distanti sono due voci"
                      " separate, non una prova con un buco dentro")
        return errori

    inizio, fine, err = intervallo(voce)
    if err:
        errori.append(err)
        return errori
    if inizio < 0 or fine >= len(segmenti):
        errori.append(f"segmenti {inizio}-{fine} fuori dalla puntata"
                      f" (0-{len(segmenti) - 1})")
        return errori

    atteso = norm(" ".join(segmenti[inizio:fine + 1]))
    trovato = norm(citazione)
    if not trovato:
        errori.append("citazione vuota una volta normalizzata")
    elif trovato not in atteso:
        dove = f"segmento {inizio}" if inizio == fine else f"segmenti {inizio}-{fine}"
        errori.append(f"la citazione NON e' nel testo del {dove}. "
                      f"Dichiarata: \"{trovato[:90]}\" | Nel testo: \"{atteso[:90]}\"")
    return errori


def verifica_copertura(episodio, n_segmenti):
    """La copertura deve piastrellare 0..N-1: niente buchi, niente sovrapposizioni.

    E' cio' che rende la lettura falsificabile. Non dimostra che l'attenzione ci sia
    stata ovunque - quello lo copre la seconda lettura indipendente - ma se una zona
    non e' stata guardata, qui si vede il buco."""
    errori = []
    blocchi = episodio.get("copertura")
    if not blocchi:
        return ["manca 'copertura': non si puo' sapere se la puntata e' stata letta tutta"]
    try:
        intervalli = sorted((int(b["da"]), int(b["a"])) for b in blocchi)
    except (KeyError, TypeError, ValueError):
        return ["'copertura' malformata: ogni blocco vuole 'da' e 'a' interi"]

    atteso = 0
    for a, b in intervalli:
        if b < a:
            errori.append(f"blocco rovesciato {a}-{b}")
            continue
        if a > atteso:
            errori.append(f"BUCO nella lettura: segmenti {atteso}-{a - 1} non coperti")
        elif a < atteso:
            errori.append(f"sovrapposizione sui segmenti {a}-{min(b, atteso - 1)}")
        atteso = max(atteso, b + 1)
    if atteso < n_segmenti:
        errori.append(f"BUCO in fondo: segmenti {atteso}-{n_segmenti - 1} non coperti")
    return errori


def controlla(percorso_gt):
    dati = json.loads(percorso_gt.read_text(encoding="utf-8"))
    episodi = dati.get("episodi", {})
    problemi, voci_ok, voci_tot = [], 0, 0

    for data_ep in sorted(episodi):
        ep = episodi[data_ep]
        segmenti = carica_segmenti(data_ep)
        if segmenti is None:
            problemi.append((data_ep, "(episodio)", ["trascrizione non trovata"]))
            continue
        for insieme in INSIEMI:
            for voce in ep.get(insieme, []):
                voci_tot += 1
                errori = verifica_voce(voce, segmenti, insieme)
                if errori:
                    titolo = voce.get("titolo", "?") if isinstance(voce, dict) else "?"
                    problemi.append((data_ep, f"{insieme}/{titolo}", errori))
                else:
                    voci_ok += 1
        errori_cop = verifica_copertura(ep, len(segmenti))
        if errori_cop:
            problemi.append((data_ep, "(copertura)", errori_cop))

    print(f"episodi controllati : {len(episodi)}")
    print(f"voci ancorate       : {voci_ok}/{voci_tot}")
    if not problemi:
        print("\nOK: ogni voce e' ancorata a un segmento reale e ogni citazione e'"
              " letterale.")
        return 0

    print(f"\n{len(problemi)} problemi:\n")
    for data_ep, etichetta, errori in problemi:
        print(f"[{data_ep}] {etichetta}")
        for e in errori:
            print(f"    - {e}")
    return 1


def diagnosi(percorso_gt):
    """Scomposizione del formato VECCHIO, che non ha 'segmento'.

    Serve da prova di correttezza dello script: sul file di oggi deve ritrovare
    esattamente cio' che e' stato misurato a mano il 2026-07-31 (45 senza citazione,
    6 citazioni inesistenti). Se non li ritrova, e' sbagliato lo script."""
    dati = json.loads(percorso_gt.read_text(encoding="utf-8"))
    senza, trovate, inesistenti = 0, 0, []

    for data_ep in sorted(dati.get("episodi", {})):
        ep = dati["episodi"][data_ep]
        segmenti = carica_segmenti(data_ep)
        if segmenti is None:
            continue
        testo = norm(" ".join(segmenti))
        for voce in ep.get("opere", []):
            citazione = voce.get("citazione")
            if not citazione:
                senza += 1
                continue
            # Il formato vecchio incolla punti distanti con "[...]": qui si spezza e
            # si cerca pezzo per pezzo, altrimenti si accuserebbe di essere inventata
            # una citazione che e' solo cucita.
            pezzi = [p for p in (norm(x) for x in citazione.split("[...]")) if p]
            mancanti = [p for p in pezzi if p not in testo]
            if mancanti:
                inesistenti.append((data_ep, voce.get("titolo", "?"), mancanti))
            else:
                trovate += 1

    tot = senza + trovate + len(inesistenti)
    print(f"opere totali                        : {tot}")
    print(f"  senza alcuna citazione            : {senza}   <- non verificabili")
    print(f"  citazione ritrovata nel testo     : {trovate}")
    print(f"  citazione NON esistente nel testo : {len(inesistenti)}   <- ricostruite")
    if inesistenti:
        print("\nCitazioni che il testo non contiene:")
        for data_ep, titolo, mancanti in inesistenti:
            print(f"  [{data_ep}] {titolo}")
            for m in mancanti:
                print(f"      manca: \"{m[:95]}\"")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", type=Path, default=GT_DEFAULT,
                   help="ground truth da controllare (default: insieme_riferimento.json)")
    p.add_argument("--diagnosi", action="store_true",
                   help="scomposizione del formato vecchio, senza pretendere 'segmento'")
    args = p.parse_args()

    if not args.file.exists():
        print(f"file non trovato: {args.file}")
        return 2
    print(f"file    : {args.file}")
    print(f"fonte   : {dati_root(ROOT) / 'trascrizioni'}\n")
    return diagnosi(args.file) if args.diagnosi else controlla(args.file)


if __name__ == "__main__":
    sys.exit(main())
