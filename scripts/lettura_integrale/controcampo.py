#!/usr/bin/env python3
"""Cosa hanno visto gli ALTRI che le due letture non hanno visto.

PERCHE' ESISTE
Le due letture cieche girano entrambe su Opus: sono la coppia piu' correlata
possibile, e possono sbagliare INSIEME. Quando concordano nel non vedere qualcosa,
il confronto A/B non se ne accorge per costruzione - e quel silenzio verrebbe letto
come accordo. Non e' un rischio teorico: il 2026-08-01, su 2015-10-06, entrambe le
letture hanno perso "50 sfumature di grigio", che al segmento 23 e' pronunciato in
chiaro ("50 sfumature di grigio e' una trilogia"). L'ha ripescato il ground truth
vecchio.

Serve quindi un terzo canale, indipendente da entrambe le letture:
  - il ground truth VECCHIO (costruito in altre sessioni, con altri criteri);
  - le voci PRODOTTE dalla pipeline (altro modello, altro metodo: chunk + prompt).

Nessuno dei due e' un'autorita'. Le loro voci sono CANDIDATI da aprire e verificare
a mano contro la trascrizione, con le stesse regole di sempre. Sopravvive -> era una
perdita delle letture, entra nel campione. Non sopravvive -> si documenta perche', e
diventa un falso positivo ACCERTATO invece che presunto.

⚠️ Non e' l'output della pipeline a definire la verita': sarebbe l'errore circolare
gia' documentato ("un test che misura se stesso"). E' la pipeline che PROPONE e la
trascrizione che DISPONE. La distinzione regge solo se la verifica resta severa:
per questo lo script non promuove niente da solo, prepara solo il lavoro da fare.

Uso:
    python3 scripts/lettura_integrale/controcampo.py
    python3 scripts/lettura_integrale/controcampo.py 2015-10-06

NIENTE caratteri fuori ASCII nell'output.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(QUI))
sys.path.insert(0, str(ROOT / "scripts" / "linux"))
from dati_root import logs_root  # noqa: E402
from test_qualita_identificazione import _stesso_titolo  # noqa: E402
from verifica_insieme_riferimento import carica_segmenti, norm  # noqa: E402

LAVORO = logs_root(ROOT) / "banco_verifica" / "lettura_integrale"
GT_VECCHIO = ROOT / "scripts" / "linux" / "insieme_riferimento.json"
RUN_PIPELINE = (logs_root(ROOT) / "banco_prova"
                / "2026-07-31_0310_ollama_qwen3:14b.json")


def titoli_letture(data_ep):
    """Tutto cio' che le due letture hanno visto: opere E marginali.

    I marginali contano: se una lettura ha VISTO qualcosa e l'ha scartata col
    motivo, non e' una perdita ed e' gia' documentata. Il controcampo deve
    segnalare solo cio' che nessuno ha proprio guardato."""
    visti = []
    for lato in ("A", "B"):
        f = LAVORO / lato / f"{data_ep}.json"
        if not f.exists():
            continue
        lettura = json.loads(f.read_text(encoding="utf-8"))
        for insieme in ("opere", "marginali"):
            for voce in lettura.get(insieme, []):
                if isinstance(voce, dict) and voce.get("titolo"):
                    visti.append(voce["titolo"])
    return visti


def candidati(data_ep):
    """{titolo: [fonti]} da ground truth vecchio e output pipeline."""
    fuori = {}
    if GT_VECCHIO.exists():
        ep = json.loads(GT_VECCHIO.read_text(encoding="utf-8"))["episodi"].get(data_ep, {})
        for o in ep.get("opere", []):
            if isinstance(o, dict) and o.get("titolo"):
                fuori.setdefault(o["titolo"], []).append("GT vecchio")
    if RUN_PIPELINE.exists():
        run = json.loads(RUN_PIPELINE.read_text(encoding="utf-8"))
        for v in run.get("voci", []):
            if v.get("episodio") == data_ep and v.get("titolo"):
                fonti = fuori.setdefault(v["titolo"], [])
                if "pipeline" not in fonti:
                    fonti.append("pipeline")
    return fuori


def dove_nel_testo(titolo, segmenti):
    """Segmenti in cui il titolo compare alla lettera.

    Qui la ricerca di stringa e' LEGITTIMA e non sostituisce la lettura: si sta
    verificando un'ipotesi precisa su un titolo preciso, non concludendo un'assenza.
    Il "non trovato" infatti non chiude il caso da solo - dice solo che il titolo non
    compare in quella forma, e la voce va comunque guardata a mano."""
    ago = norm(titolo)
    if not ago:
        return []
    return [i for i, s in enumerate(segmenti) if ago in norm(s)]


def una_puntata(data_ep):
    segmenti = carica_segmenti(data_ep)
    if segmenti is None:
        print(f"[{data_ep}] trascrizione non trovata\n")
        return None
    visti = titoli_letture(data_ep)
    if not visti:
        print(f"[{data_ep}] nessuna lettura presente, salto\n")
        return None

    orfani = {t: f for t, f in candidati(data_ep).items()
              if not any(_stesso_titolo(t, v) for v in visti)}

    print(f"[{data_ep}]  {len(visti)} voci viste dalle letture,"
          f" {len(orfani)} candidati orfani DA APRIRE TUTTI")
    con_riscontro = 0
    for titolo, fonti in sorted(orfani.items()):
        dove = dove_nel_testo(titolo, segmenti)
        # ⚠️ "non trovato" NON archivia la voce, e' solo un indizio debole. La ricerca
        # letterale fallisce sulle varianti, e il 2026-08-01 ha quasi nascosto l'unica
        # perdita vera del blocco: il GT vecchio scrive "Cinquanta sfumature di grigio",
        # la trascrizione dice "50 sfumature di grigio" in cifre, e la voce risultava
        # "assente dal testo". Stessa cosa fra titolo originale e italiano ("When Harry
        # Met Sally" contro "Harry ti presento Sally"). Per questo si aprono TUTTI.
        if dove:
            con_riscontro += 1
            print(f"   [{', '.join(fonti)}] {titolo!r}  -> seg {dove[0]}:"
                  f" \"{segmenti[dove[0]].strip()[:100]}\"")
        else:
            print(f"   [{', '.join(fonti)}] {titolo!r}  -> nessun riscontro letterale"
                  f" (CERCARE VARIANTI: cifre/lettere, titolo originale/italiano)")
    print()
    return len(orfani), con_riscontro


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("date", nargs="*", help="date da controllare (default: tutte quelle lette)")
    args = p.parse_args()

    date = args.date or sorted(f.stem for f in (LAVORO / "A").glob("*.json"))
    if not date:
        print(f"nessuna lettura in {LAVORO / 'A'}")
        return 2

    tot_orfani = tot_guardare = 0
    for data_ep in date:
        esito = una_puntata(data_ep)
        if esito:
            tot_orfani += esito[0]
            tot_guardare += esito[1]
    print("=" * 62)
    print(f"candidati orfani DA APRIRE: {tot_orfani}"
          f"   (con riscontro letterale: {tot_guardare})")
    print("\nVanno aperti TUTTI e decisi a mano leggendo il contesto. Un riscontro")
    print("letterale non basta per promuovere; la sua assenza non basta per archiviare.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
