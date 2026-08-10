#!/usr/bin/env python3
"""Le due letture cieche della stessa puntata: dove concordano e dove no.

Il tasso di disaccordo fra due letture indipendenti e' la misura di affidabilita'
della lettura stessa. E' il numero che sostituisce "l'ho letto tutto, fidati":
finche' non esisteva, il ground truth poteva solo essere creduto.

Attenzione a cosa dimostra e cosa no. Se A e B concordano, l'opera e' quasi
certamente li'. Se A e B CONCORDANO NEL NON VEDERE qualcosa, questo confronto non
se ne accorge: due modelli della stessa famiglia possono avere punti ciechi comuni,
ed e' per questo che i due passaggi girano su modelli diversi. Il tasso che esce di
qui e' quindi un limite INFERIORE all'errore, non una barra d'errore.

Prima di confrontare, ogni lettura passa dai controlli severi di
verifica_insieme_riferimento: una lettura le cui citazioni non stanno nei segmenti
dichiarati non e' un parere da pesare, e' un dato da buttare.

Confronta solo 'opere'. Decisione dell'utente 2026-08-01: la musica ha un ruolo
secondario in questo programma, il cantato non si insegue e non esiste piu' un
insieme 'da_cantato' da riconoscere a orecchio o arbitrare con la playlist.

Uso:
    python3 scripts/linux/confronta_letture.py                    # tutte le coppie A/B
    python3 scripts/linux/confronta_letture.py 2014-12-24
    python3 scripts/linux/confronta_letture.py --vecchio          # confronta anche col
                                                                  # ground truth attuale

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
# _stesso_titolo vive nel banco, in scripts/linux/. Dipendenza in UNA direzione sola:
# questa fase legge dal progetto, il progetto non importa niente da qui.
sys.path.insert(0, str(ROOT / "scripts" / "linux"))
from dati_root import logs_root  # noqa: E402
from test_qualita_identificazione import _stesso_titolo  # noqa: E402
from verifica_insieme_riferimento import (  # noqa: E402
    carica_segmenti, verifica_copertura, verifica_voce,
)

LAVORO = logs_root(ROOT) / "banco_verifica" / "lettura_integrale"
GT_VECCHIO = ROOT / "scripts" / "linux" / "insieme_riferimento.json"
# 'marginali' non entra nel tasso: e' documentazione di cio' che si e' scartato, e
# due letture possono legittimamente annotarne un numero diverso senza che nessuna
# delle due abbia sbagliato.
INSIEMI_CONFRONTATI = ("opere",)


def valida(lettura, segmenti, etichetta):
    """Errori strutturali di una lettura. Vuoto = si puo' usare per il confronto."""
    problemi = []
    for insieme in ("opere", "marginali"):
        for voce in lettura.get(insieme, []):
            errori = verifica_voce(voce, segmenti, insieme)
            if errori:
                titolo = voce.get("titolo", "?") if isinstance(voce, dict) else "?"
                problemi.append(f"{etichetta} {insieme}/{titolo}: {errori[0]}")
    for e in verifica_copertura(lettura, len(segmenti)):
        problemi.append(f"{etichetta} copertura: {e}")
    return problemi


def indicizza(lettura, livello=None):
    """Voci confrontabili, SENZA doppioni, filtrate per livello.

    Una stessa opera puo' essere nominata piu' volte nella puntata e una lettura puo'
    ancorarla a due segmenti diversi (2017-11-09: "La fattoria degli animali" ai
    segmenti 70 e 73, entrambi validi). Senza questa deduplica la seconda copia non
    trova corrispondenza nell'altra lettura e viene contata come DISACCORDO: il
    2026-08-02 gonfiava il tasso del blocco dal 10,5% al 15%. E' rumore della misura,
    non divergenza fra i lettori.

    I livelli si confrontano SEPARATI. Le voci 'autore' e 'tema' sono dove la
    conoscenza del lettore fa il lavoro che nessuno script potra' replicare:
    mescolarle alle 'opere' nasconderebbe proprio il divario che si vuole misurare."""
    fuori = []
    for insieme in INSIEMI_CONFRONTATI:
        for voce in lettura.get(insieme, []):
            if not isinstance(voce, dict):
                continue
            if livello and voce.get("livello") != livello:
                continue
            # A livello 'autore' il titolo e' vuoto per definizione: l'identita' della
            # voce e' l'autore.
            chiave = voce.get("titolo") or voce.get("autore")
            if not chiave:
                continue
            if any(_stesso_titolo(chiave, t) for t, _i, _v in fuori):
                continue
            fuori.append((chiave, insieme, voce))
    return fuori


def _span(voce):
    seg = voce.get("segmento")
    if isinstance(seg, list) and len(seg) == 2:
        return seg[0], seg[1]
    if isinstance(seg, int):
        return seg, seg
    return None


def confronta_temi(voci_a, voci_b):
    """I temi si accoppiano per SOVRAPPOSIZIONE DI SEGMENTI, non per titolo.

    Un tema non ha un titolo canonico: e' una descrizione che il lettore inventa. Il
    2026-08-05 lo stesso blocco di 2013-11-27 e' stato chiamato "il privilegio dei
    parcheggi riservati" da una lettura e "il privilegio e il malcostume di chi ha una
    divisa o una carica" dall'altra - stesso tema, due nomi, e il confronto per titolo
    li dava come divergenti. Cio' che conta e' se le due letture hanno individuato lo
    stesso TRATTO di puntata."""
    accordo, solo_a = [], []
    presi = set()
    for ta, _ia, va in voci_a:
        sa = _span(va)
        trovato = None
        for i, (tb, _ib, vb) in enumerate(voci_b):
            if i in presi:
                continue
            sb = _span(vb)
            if sa and sb and sa[0] <= sb[1] and sb[0] <= sa[1]:
                trovato = i
                break
        if trovato is None:
            solo_a.append((ta, _ia, va))
        else:
            presi.add(trovato)
            accordo.append((ta, _ia, va, voci_b[trovato][2]))
    solo_b = [v for i, v in enumerate(voci_b) if i not in presi]
    return accordo, [], solo_a, solo_b


def confronta(voci_a, voci_b):
    """Accordo, solo A, solo B, e i casi visti da entrambi ma classificati diverso."""
    accordo, discordi_insieme, solo_a = [], [], []
    presi_b = set()
    for titolo_a, ins_a, voce_a in voci_a:
        trovato = None
        for i, (titolo_b, ins_b, voce_b) in enumerate(voci_b):
            if i in presi_b:
                continue
            if _stesso_titolo(titolo_a, titolo_b):
                trovato = (i, titolo_b, ins_b, voce_b)
                break
        if trovato is None:
            solo_a.append((titolo_a, ins_a, voce_a))
            continue
        i, titolo_b, ins_b, voce_b = trovato
        presi_b.add(i)
        if ins_a == ins_b:
            accordo.append((titolo_a, ins_a, voce_a, voce_b))
        else:
            discordi_insieme.append((titolo_a, ins_a, titolo_b, ins_b))
    solo_b = [v for i, v in enumerate(voci_b) if i not in presi_b]
    return accordo, discordi_insieme, solo_a, solo_b


def titoli_vecchi(data_ep):
    if not GT_VECCHIO.exists():
        return []
    ep = json.loads(GT_VECCHIO.read_text(encoding="utf-8"))["episodi"].get(data_ep, {})
    return [o["titolo"] for o in ep.get("opere", []) if isinstance(o, dict)]


def una_puntata(data_ep, con_vecchio):
    fa, fb = LAVORO / "A" / f"{data_ep}.json", LAVORO / "B" / f"{data_ep}.json"
    if not fa.exists() or not fb.exists():
        manca = "A" if not fa.exists() else "B"
        print(f"[{data_ep}] lettura {manca} assente, salto\n")
        return None

    a = json.loads(fa.read_text(encoding="utf-8"))
    b = json.loads(fb.read_text(encoding="utf-8"))
    segmenti = carica_segmenti(data_ep)

    problemi = valida(a, segmenti, "A") + valida(b, segmenti, "B")
    # La SOGLIA vale sulle voci 'opera': sono le uniche che il processo su larga scala
    # puo' produrre come coppia titolo+autore, quindi le uniche confrontabili con lui.
    voci_a, voci_b = indicizza(a, "opera"), indicizza(b, "opera")
    accordo, discordi_insieme, solo_a, solo_b = confronta(voci_a, voci_b)

    unione = len(accordo) + len(discordi_insieme) + len(solo_a) + len(solo_b)
    divergenti = len(discordi_insieme) + len(solo_a) + len(solo_b)
    tasso = divergenti / unione if unione else 0.0

    print(f"[{data_ep}]  {len(segmenti)} segmenti")
    print(f"   A: {len(a.get('opere', []))} voci   |   B: {len(b.get('opere', []))} voci")
    print(f"   OPERA   accordo {len(accordo)}/{unione}   disaccordo {divergenti}"
          f" ({tasso:.0%})")
    # Riportati a parte, non sommati: qui l'intelligenza del lettore fa il lavoro che
    # lo script non puo' fare, ed e' proprio quel divario che si vuole vedere.
    for liv in ("autore", "tema"):
        la, lb = indicizza(a, liv), indicizza(b, liv)
        if not (la or lb):
            continue
        motore = confronta_temi if liv == "tema" else confronta
        acc, _d, sa, sb = motore(la, lb)
        u = len(acc) + len(_d) + len(sa) + len(sb)
        print(f"   {liv.upper():7s} accordo {len(acc)}/{u}"
              f"   disaccordo {u - len(acc)} ({(u - len(acc)) / u:.0%})"
              f"   [fuori soglia, riportato a parte]")
        for t, _i, v in sa:
            print(f"      A> {t!r} (seg {v.get('segmento')})")
        for t, _i, v in sb:
            print(f"      B> {t!r} (seg {v.get('segmento')})")
    if problemi:
        print(f"   !! {len(problemi)} voci non valide (citazione o copertura):")
        for p in problemi[:8]:
            print(f"      - {p}")
        if len(problemi) > 8:
            print(f"      ... e altre {len(problemi) - 8}")
    for titolo, ins_a, titolo_b, ins_b in discordi_insieme:
        print(f"   ~ {titolo!r}: A dice {ins_a}, B dice {ins_b}")
    for titolo, ins, voce in solo_a:
        print(f"   A> {titolo!r} ({ins}, seg {voce.get('segmento')})")
    for titolo, ins, voce in solo_b:
        print(f"   B> {titolo!r} ({ins}, seg {voce.get('segmento')})")

    if con_vecchio:
        vecchi = titoli_vecchi(data_ep)
        noti = [t for t, _, _ in voci_a] + [t for t, _, _ in voci_b]
        persi = [v for v in vecchi if not any(_stesso_titolo(v, n) for n in noti)]
        print(f"   vecchio GT: {len(vecchi)} opere, di cui {len(persi)} che nessuna"
              f" delle due letture ha visto")
        for v in persi:
            print(f"   V> {v!r}")

    (LAVORO / "disaccordi").mkdir(parents=True, exist_ok=True)
    (LAVORO / "disaccordi" / f"{data_ep}.json").write_text(json.dumps({
        "episodio": data_ep,
        "voci_non_valide": problemi,
        "accordo": [{"titolo": t, "insieme": i} for t, i, _, _ in accordo],
        "insieme_discorde": [{"titolo": t, "A": ia, "B": ib}
                             for t, ia, _, ib in discordi_insieme],
        "solo_A": [{"titolo": t, "insieme": i, "segmento": v.get("segmento"),
                    "citazione": v.get("citazione")} for t, i, v in solo_a],
        "solo_B": [{"titolo": t, "insieme": i, "segmento": v.get("segmento"),
                    "citazione": v.get("citazione")} for t, i, v in solo_b],
        "unione": unione, "divergenti": divergenti, "tasso_disaccordo": tasso,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print()
    return unione, divergenti, len(problemi)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("date", nargs="*", help="date da confrontare (default: tutte)")
    p.add_argument("--vecchio", action="store_true",
                   help="mostra anche le opere del ground truth attuale che nessuna"
                        " delle due letture ha visto")
    args = p.parse_args()

    date = args.date or sorted(f.stem for f in (LAVORO / "A").glob("*.json"))
    if not date:
        print(f"nessuna lettura in {LAVORO / 'A'}")
        return 2

    tot_unione = tot_div = tot_prob = 0
    for data_ep in date:
        esito = una_puntata(data_ep, args.vecchio)
        if esito:
            u, d, pr = esito
            tot_unione += u
            tot_div += d
            tot_prob += pr

    if tot_unione:
        tasso = tot_div / tot_unione
        print("=" * 62)
        print(f"COMPLESSIVO  accordo {tot_unione - tot_div}/{tot_unione}"
              f"   disaccordo {tot_div} ({tasso:.1%})")
        print(f"voci non valide: {tot_prob}")
        # Impegno preso il 2026-08-01 PRIMA di vedere i numeri, per non poter poi
        # scegliere l'interpretazione comoda: sopra il 5% non si dichiara finito.
        if tasso > 0.05:
            print(f"\nSOPRA LA SOGLIA DEL 5%: i criteri non bastano, si rilegge.")
            return 1
        print("\nsotto la soglia del 5%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
