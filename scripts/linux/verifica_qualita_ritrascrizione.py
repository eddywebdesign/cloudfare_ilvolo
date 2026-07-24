# Verifica REALE (non solo lettura di log) di un campione di episodi ritrascritti
# con --forza: apre i file data/trascrizioni/<data>.json e data/frammenti/<data>.json
# effettivi e controlla che siano corretti, invece di fidarsi che "il processo non ha
# dato errori" o che "il log dice completato".
#
# Nato il 2026-07-24 dopo un richiamo esplicito dell'utente: i controlli precedenti
# (Monitor lanciati dalla sessione Claude) giravano SOLO nella sessione dell'agente,
# non su K16 - quando la sessione si riavviava (successo piu' volte in una notte per
# disconnessioni MCP), i controlli sparivano senza che nessuno se ne accorgesse.
# Questa verifica va invocata DENTRO trascrivi_locale_episodi.py stesso (vedi
# _verifica_milestone_se_serve()), cosi' scatta indipendentemente da qualunque
# sessione esterna, esattamente come ilvolo-batch-health.timer (systemd) gia' fa
# per il controllo di base "il processo e' vivo".
#
# Uso standalone (debug):
#   python3 scripts/linux/verifica_qualita_ritrascrizione.py [N_campione]

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from dati_root import dati_root  # noqa: E402
from transcribe_utils import CONFIG_VERSIONE  # noqa: E402

CHECKPOINT = REPO / "logs" / "checkpoint_ritrascrizione.log"
REPORT = REPO / "logs" / "verifica_qualita_ritrascrizione.log"


def verifica_campione(n_campione: int = 15) -> tuple[int, int, list[str]]:
    """Ritorna (ok, totale_campione, righe_report) verificando gli ultimi
    n_campione episodi elencati nel checkpoint. Ogni file viene APERTO e
    controllato per davvero (marcatore config, segmenti non vuoti, niente
    eco del vecchio prompt, numero speaker plausibile, frammenti non vuoti)."""
    trascrizioni_dir = dati_root(REPO) / "trascrizioni"
    frammenti_dir = dati_root(REPO) / "frammenti"

    if not CHECKPOINT.exists():
        return 0, 0, ["checkpoint non trovato, nessuna verifica possibile"]

    righe_checkpoint = CHECKPOINT.read_text(encoding="utf-8").strip().splitlines()
    campione = righe_checkpoint[-n_campione:]
    righe_report = [f"=== Verifica su ultimi {len(campione)} episodi (di {len(righe_checkpoint)} nel checkpoint) ==="]
    ok_tot = 0

    for riga in campione:
        if " " not in riga:
            continue
        _, data_str = riga.split(" ", 1)
        problemi = []
        tp = trascrizioni_dir / f"{data_str}.json"
        fp = frammenti_dir / f"{data_str}.json"

        if not tp.exists():
            problemi.append("trascrizioni MANCANTE")
        else:
            try:
                d = json.loads(tp.read_text(encoding="utf-8"))
            except Exception as e:
                problemi.append(f"trascrizioni JSON invalido: {e}")
                d = None
            if d is not None:
                if d.get("_config_versione") != CONFIG_VERSIONE:
                    problemi.append(f"marcatore mancante/sbagliato: {d.get('_config_versione')!r}")
                segs = d.get("segments", [])
                if not segs:
                    problemi.append("nessun segmento")
                else:
                    testo_tot = " ".join(s.get("text", "") for s in segs).lower()
                    if "volo, maurizio, viola" in testo_tot:
                        problemi.append("ECO DEL VECCHIO PROMPT ANCORA PRESENTE")
                    speaker = set(s.get("speaker") for s in segs if s.get("speaker"))
                    if not (1 <= len(speaker) <= 8):
                        problemi.append(f"speaker fuori range plausibile: {len(speaker)}")
                    parole = sum(len(s.get("text", "").split()) for s in segs)
                    if parole < 200:
                        problemi.append(f"poche parole totali ({parole}), episodio troppo corto o troncato?")

        if not fp.exists():
            problemi.append("frammenti MANCANTE")
        else:
            try:
                fr = json.loads(fp.read_text(encoding="utf-8"))
                if not fr:
                    problemi.append("frammenti VUOTO")
            except Exception as e:
                problemi.append(f"frammenti JSON invalido: {e}")

        if problemi:
            righe_report.append(f"  [{data_str}] PROBLEMI: {'; '.join(problemi)}")
        else:
            ok_tot += 1
            righe_report.append(f"  [{data_str}] OK")

    righe_report.append(f"=== RISULTATO: {ok_tot}/{len(campione)} episodi verificati senza problemi ===")
    return ok_tot, len(campione), righe_report


def esegui_e_registra(motivo: str, n_campione: int = 15) -> bool:
    """Esegue la verifica, scrive SEMPRE un report su disco (non solo se c'e' un
    problema - serve anche la prova positiva che il controllo e' girato davvero),
    ritorna True se tutto ok. Chiamare questa, non verifica_campione() da sola,
    da dentro trascrivi_locale_episodi.py."""
    from datetime import datetime
    ok, tot, righe = verifica_campione(n_campione)
    ts = datetime.now().isoformat(timespec="seconds")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] Trigger: {motivo}\n")
        f.write("\n".join(righe) + "\n")
    return ok == tot


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    ok, tot, righe = verifica_campione(n)
    print("\n".join(righe))
    sys.exit(0 if ok == tot else 1)
