# Equivalente Linux di check_batch_health.ps1: check fattuale, standalone,
# indipendente da Claude/app aperta. Eseguito da un timer systemd ogni 15 min.
# Scrive SEMPRE una riga di heartbeat in logs/batch_health_log.txt (prova che il
# check e' girato davvero) e, solo in caso di anomalia, anche logs/batch_health_ALERT.txt.
#
# Stessa logica di controlli fattuali (non euristici) dello script Windows originale:
# processi vivi via psutil, CPU del sottoprocesso whisperx in crescita in una finestra
# di 8s (prova che non e' bloccato), JSON trascritto con segmenti validi, soglia
# temperatura 90C dall'ultima riga del CSV termico (operazione normale osservata:
# 72-84C: 78C generava falsi allarmi costanti).
#
# In caso di anomalia, invia anche un'email (scripts/linux/enviar_alerta.py) -
# nessuno guarda logs/batch_health_ALERT.txt in tempo reale su un K16 headless.

import datetime
import json
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enviar_alerta import enviar_alerta  # noqa: E402
from kill_coordinado import matar_trascrizione  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = REPO / "logs"
STOP_FLAG = REPO / "data" / "STOP_BATCH_AFTER_EPISODE.flag"
CSV_TERMICO = LOGS_DIR / "trascrizioni_log_termico.csv"
SOGLIA_TEMP_C = 90.0


def trova_processo(match_in_cmdline: str):
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if match_in_cmdline in cmdline:
            return p
    return None


def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    anomalie = []

    batch = trova_processo("trascrivi_locale_episodi")
    logger = trova_processo("sensori_temp")
    whisperx = trova_processo("whisperx")

    if not batch:
        anomalie.append("batch trascrivi_locale_episodi.py NON in esecuzione")
    if not logger:
        anomalie.append("logger sensori_temp.py NON in esecuzione")

    if whisperx:
        try:
            cpu1 = sum(whisperx.cpu_times()[:2])
            time.sleep(8)
            cpu2 = sum(whisperx.cpu_times()[:2])
            if cpu2 - cpu1 <= 0:
                anomalie.append(f"whisperx PID {whisperx.pid} vivo ma CPU ferma (possibile hang)")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            anomalie.append(f"whisperx PID {whisperx.pid} sparito durante il check")
    elif batch:
        anomalie.append("batch vivo ma nessun sottoprocesso whisperx trovato (tra un episodio e l'altro puo' essere normale per pochi secondi)")

    trascrizioni_dir = REPO / "data" / "trascrizioni"
    json_recenti = sorted(trascrizioni_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if json_recenti:
        try:
            d = json.loads(json_recenti[0].read_text(encoding="utf-8"))
            if not d.get("segments"):
                anomalie.append(f"ultimo JSON trascrizione ({json_recenti[0].name}) senza segmenti validi")
        except Exception as e:
            anomalie.append(f"ultimo JSON trascrizione ({json_recenti[0].name}) illeggibile: {e}")

    if CSV_TERMICO.exists():
        ultima_riga = CSV_TERMICO.read_text(encoding="utf-8").strip().splitlines()[-1]
        campi = ultima_riga.split(",")
        if len(campi) >= 4:
            try:
                temp_csv = datetime.datetime.fromisoformat(campi[0])
                if (datetime.datetime.now() - temp_csv).total_seconds() > 20 * 60:
                    anomalie.append("log termico fermo da oltre 20 min")
                if float(campi[1]) > SOGLIA_TEMP_C:
                    anomalie.append(f"CPU a {campi[1]}C, sopra soglia {SOGLIA_TEMP_C}C")
            except (ValueError, IndexError):
                pass

    stop_eseguito = False
    if STOP_FLAG.exists() and batch and not whisperx:
        # aggressivo=False: qui va fermato solo il wrapper trascrivi_locale_episodi.py
        # (whisperx non e' in esecuzione per definizione, siamo nella pausa tra
        # episodi), non l'intera sessione tmux/wrapper bash. Verifica reale con
        # psutil prima di consumare il flag, non solo assunta.
        detenuto, riga = matar_trascrizione(
            origine="check_batch_health.py", motivo="STOP_BATCH_AFTER_EPISODE.flag",
            aggressivo=False,
        )
        if detenuto:
            STOP_FLAG.unlink()
            stop_eseguito = True
        else:
            anomalie.append(f"STOP_FLAG presente ma il batch non si e' fermato: {riga}")

    stato_riga = (f"{ts} | batch={bool(batch)} logger={bool(logger)} whisperx={bool(whisperx)} "
                  f"anomalie={len(anomalie)} stopEseguito={stop_eseguito}")
    with open(LOGS_DIR / "batch_health_log.txt", "a", encoding="utf-8") as f:
        f.write(stato_riga + "\n")

    if anomalie:
        msg = f"{ts} ANOMALIA:\n" + "\n".join(anomalie)
        with open(LOGS_DIR / "batch_health_ALERT.txt", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        print(msg)
        enviar_alerta("Anomalia detectada", msg)


if __name__ == "__main__":
    main()
