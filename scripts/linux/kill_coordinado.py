# Punto unico per uccidere la trascrizione in corso sul K16. Prima di questo,
# panel_control.py (locale), panel_estado_hp14.py (remoto via SSH, comandi
# pkill scritti a mano), sensori_temp.py (overheat) e check_batch_health.py
# avevano ognuno la propria logica di kill indipendente, senza sapere gli uni
# degli altri -- funzionava solo per idempotenza accidentale (pkill su un
# processo gia' morto non fa danno), ma nessuno registrava CHI aveva fermato
# COSA e PERCHE', rendendo impossibile ricostruire un incidente a posteriori
# (vedi la notte del 17-18/07/2026).
#
# Uso come libreria:
#   from kill_coordinado import matar_trascrizione
#   detenuto, riga_log = matar_trascrizione(origine="pannello K16", motivo="Detener AHORA")
#
# Uso da riga di comando (es. da SSH remoto, sostituisce gli script bash ad-hoc):
#   python3 scripts/linux/kill_coordinado.py --origine "pannello HP14" --motivo "..."

import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parent.parent.parent
LOG_FILE = REPO / "logs" / "kill_events.log"
PATTERN_PROCESSI = ("whisperx", "trascrivi_locale_episodi", "avvia_trascrizione_sicura")


def _registra(origine: str, motivo: str, uccisi: str) -> str:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    riga = f"{ts} | origine={origine} | motivo={motivo} | uccisi={uccisi or 'nessuno'}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(riga + "\n")
    return riga


def matar_trascrizione(
    origine: str,
    motivo: str,
    fermare_watchdog: bool = True,
    aggressivo: bool = True,
) -> tuple[bool, str]:
    """Uccide whisperx + trascrivi_locale_episodi.py (sempre) e, in modalita'
    aggressiva (default), anche avvia_trascrizione_sicura.sh + la sessione
    tmux 'trascrizione' + il watchdog -- uno stop "totale" voluto dall'utente
    (bottoni dei pannelli). In modalita' NON aggressiva (usata da sensori_temp.py
    per il kill da surriscaldamento) si ferma solo il lavoro CPU-bound e si
    lascia vivo il wrapper bash, che vede logs/OVERHEAT_STOP.flag e si ferma
    da solo in modo pulito senza passare all'anno successivo -- comportamento
    intenzionalmente diverso, non un bug: NON unificare senza motivo.

    Verifica con psutil che sia morto per davvero (non solo assunto), e
    registra SEMPRE l'evento con chi e perche' in logs/kill_events.log.

    Ritorna (detenuto: bool, riga_log: str)."""
    pattern_da_uccidere = PATTERN_PROCESSI if aggressivo else ("whisperx", "trascrivi_locale_episodi")
    uccisi = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for pattern in pattern_da_uccidere:
            if pattern in cmdline:
                try:
                    p.kill()
                    uccisi.append(f"{pattern}(PID {p.pid})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                break

    if aggressivo:
        subprocess.run(["tmux", "kill-session", "-t", "trascrizione"], check=False,
                        capture_output=True)
        if fermare_watchdog:
            subprocess.run(["systemctl", "--user", "stop", "ilvolo-watchdog-nas.timer"],
                            check=False, capture_output=True)

    time.sleep(1)
    detenuto = not any(pattern in cmd for cmd in _cmdline_vive() for pattern in pattern_da_uccidere)
    riga = _registra(origine, motivo, ", ".join(uccisi))
    return detenuto, riga


def _cmdline_vive():
    for p in psutil.process_iter(["cmdline"]):
        try:
            yield " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origine", required=True)
    parser.add_argument("--motivo", required=True)
    parser.add_argument("--no-watchdog", action="store_true",
                         help="non fermare il timer ilvolo-watchdog-nas")
    args = parser.parse_args()
    detenuto, riga = matar_trascrizione(
        args.origine, args.motivo, fermare_watchdog=not args.no_watchdog,
    )
    print(riga)
    print("detenuto" if detenuto else "ATTENZIONE: qualcosa e' ancora vivo dopo il kill")
    sys.exit(0 if detenuto else 1)
