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
import subprocess
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enviar_alerta import enviar_alerta  # noqa: E402
from kill_coordinado import matar_trascrizione  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = REPO / "logs"
STOP_FLAG = REPO / "data" / "panel_stop_pendiente.flag"  # stesso file di FLAG_STOP_PENDIENTE in panel_control.py
# (nome allineato 2026-07-23: prima puntava a un file mai creato da nessuno script Linux,
# residuo della vecchia convenzione Windows STOP_BATCH_AFTER_EPISODE.flag - questo ramo
# di codice era di fatto morto, nessuna rete di sicurezza se il pannello grafico crashava)
CSV_TERMICO = LOGS_DIR / "trascrizioni_log_termico.csv"
SOGLIA_TEMP_C = 90.0
SOGLIA_TEMP_GPU_C = 88.0  # coerente con SOGLIA_EMERGENZA_GPU in avvia_trascrizione_sicura.sh
# Stessi parametri esatti con cui avvia_trascrizione_sicura.sh lancia il logger -
# se mai cambiano li', cambiarli anche qui.
SOGLIA_EMERGENZA_CPU = 93
SOGLIA_EMERGENZA_GPU = 88

# Crash-loop CUDA del 2026-07-25 (driver NVIDIA aggiornato da unattended-upgrade
# senza reboot): whisperx falliva l'init CUDA in <1s, il watchdog ne rilanciava
# subito un altro -- "processo vivo, CPU che si muove" era vero ad ogni singolo
# check per 7 ore filate, "anomalie=0" scritto ogni 15 min, zero episodi
# completati per davvero. Il controllo sopra (processo+CPU) misura "vivo", non
# "sta producendo qualcosa" -- serve un segnale di PROGRESSO REALE, non di vita.
CHECKPOINT_RITRASCRIZIONE = LOGS_DIR / "checkpoint_ritrascrizione.log"  # scritto SOLO al
# completamento riuscito di un episodio (non ad ogni tentativo) -- segnale diretto.
CONSOLA_BATCH = LOGS_DIR / "consola_batch.log"  # troncato ad ogni relancio di
# avvia_trascrizione_sicura.sh (">" non ">>"), quindi contarne gli errori dentro
# equivale a "errori dall'ultimo lancio", non serve filtrare per timestamp.
SOGLIA_PROGRESSO_MIN = 12  # margine ampio sopra i ~2 min/episodio reali su GPU,
# ma abbastanza stretto da scattare al PRIMO giro di check dopo uno stallo (il
# timer gira ogni 15 min) invece che dopo ore.
SOGLIA_ERRORI_CRASH_LOOP = 2  # occorrenze minime di errore fatale per parlare di
# crash-loop confermato (causa nota) invece di un generico "nessun progresso"
# (causa da indagare, es. episodio lunghissimo o rete NAS lenta).


def ultimo_progresso_reale():
    """Timestamp dell'ultimo episodio DAVVERO completato (ultima riga del
    checkpoint), o None se il file non esiste/e' vuoto/illeggibile."""
    if not CHECKPOINT_RITRASCRIZIONE.exists():
        return None
    try:
        ultima = CHECKPOINT_RITRASCRIZIONE.read_text(encoding="utf-8").strip().splitlines()[-1]
        return datetime.datetime.fromisoformat(ultima.split()[0])
    except (IndexError, ValueError, OSError):
        return None


def conta_errori_fatali_recenti() -> int:
    if not CONSOLA_BATCH.exists():
        return 0
    try:
        testo = CONSOLA_BATCH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return testo.count("CUDA failed") + testo.count("ERRORE trascrizione")


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

    logger_riavviato = False
    if not logger:
        if batch:
            # Il batch e' vivo ma il logger termico e' morto: senza di lui NESSUNA
            # soglia di emergenza puo' scattare (ne' il kill diretto di sensori_temp.py
            # ne' questo stesso check, che legge il CSV che lui scrive). Prima si
            # segnalava solo via email - un umano doveva accorgersene e riavviarlo a
            # mano. Riavviarlo qui, subito, senza aspettare un intervento esterno:
            # l'utente ha chiesto esplicitamente una garanzia "senza se e senza ma"
            # per tutta la durata del processo, non solo quando qualcuno controlla.
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parent / "sensori_temp.py"),
                 "--loop", "60", str(CSV_TERMICO),
                 "--kill-cpu", str(SOGLIA_EMERGENZA_CPU), "--kill-gpu", str(SOGLIA_EMERGENZA_GPU)],
                cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(2)
            logger_riavviato = trova_processo("sensori_temp") is not None
            if logger_riavviato:
                anomalie.append("logger sensori_temp.py era MORTO — riavviato automaticamente ORA")
            else:
                anomalie.append("logger sensori_temp.py MORTO e il riavvio automatico e' FALLITO — nessuna protezione termica attiva")
        else:
            anomalie.append("logger sensori_temp.py NON in esecuzione")

    if whisperx:
        try:
            cpu1 = sum(whisperx.cpu_times()[:2])
            time.sleep(8)
            cpu2 = sum(whisperx.cpu_times()[:2])
            if cpu2 - cpu1 <= 0:
                anomalie.append(f"whisperx PID {whisperx.pid} vivo ma CPU ferma (possibile hang)")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Il PID monitorato puo' sparire durante gli 8s di attesa semplicemente perche'
            # l'episodio e' finito e ne e' partito uno nuovo con un PID diverso (con GPU un
            # episodio dura ~1m35s-2m10s, quindi capita spesso, non e' un'anomalia). Controllo
            # diretto (CLAUDE.md "mai euristiche quando esiste un controllo diretto"): verificare
            # se un whisperx e' vivo ADESSO, non se il vecchio riferimento PID lo e' ancora.
            if not trova_processo("whisperx"):
                anomalie.append(f"whisperx PID {whisperx.pid} sparito durante il check, nessun nuovo whisperx trovato")
    elif batch:
        anomalie.append("batch vivo ma nessun sottoprocesso whisperx trovato (tra un episodio e l'altro puo' essere normale per pochi secondi)")

    # Progresso REALE (non solo "vivo") -- fix 2026-07-25 dopo il crash-loop CUDA
    # di 7 ore che questo check non aveva mai rilevato (vedi commento su
    # SOGLIA_PROGRESSO_MIN sopra).
    crash_loop_confermato = False
    if batch:
        progresso_ts = ultimo_progresso_reale()
        if progresso_ts:
            minuti_da_ultimo = (datetime.datetime.now() - progresso_ts).total_seconds() / 60
            if minuti_da_ultimo > SOGLIA_PROGRESSO_MIN:
                n_errori = conta_errori_fatali_recenti()
                if n_errori >= SOGLIA_ERRORI_CRASH_LOOP:
                    crash_loop_confermato = True
                    anomalie.append(
                        f"CRASH-LOOP CONFERMATO: nessun episodio completato da {minuti_da_ultimo:.0f} min, "
                        f"{n_errori} errori CUDA/traceback in consola_batch.log dall'ultimo lancio"
                    )
                else:
                    anomalie.append(
                        f"batch vivo ma nessun episodio completato da {minuti_da_ultimo:.0f} min "
                        f"(nessun errore CUDA rilevato -- causa da verificare, non necessariamente un crash-loop)"
                    )

    # Primo livello di auto-correzione (richiesto esplicitamente dall'utente
    # 2026-07-25): su un crash-loop CONFERMATO (causa nota, non solo "lento"),
    # fermare il batch E il watchdog -- altrimenti il watchdog lo rilancerebbe
    # da solo entro pochi minuti, ricreando lo stesso crash-loop. Non si tenta
    # di indovinare/riparare la causa reale (oggi era il driver, potrebbe non
    # esserlo la prossima volta) -- solo fermare lo spreco, poi notificare e
    # aspettare una decisione umana.
    auto_correzione_esito = None
    if crash_loop_confermato:
        detenuto, riga_kill = matar_trascrizione(
            origine="check_batch_health.py",
            motivo="crash-loop CUDA/traceback confermato (auto-correzione di primo livello)",
            aggressivo=True,  # ferma anche tmux + ilvolo-watchdog-nas.timer
        )
        if detenuto:
            auto_correzione_esito = (
                "AUTO-CORREZIONE ESEGUITA: batch e watchdog fermati. Dopo aver risolto la causa "
                "reale, riprendere con: systemctl --user start ilvolo-watchdog-nas.timer"
            )
        else:
            # Verifica reale del risultato, non assunto: se matar_trascrizione()
            # non conferma la morte dei processi, dirlo esplicitamente.
            auto_correzione_esito = f"AUTO-CORREZIONE FALLITA (vedi {riga_kill}) -- il batch potrebbe essere ancora in crash-loop, serve intervento manuale ORA"
        anomalie.append(auto_correzione_esito)
        # Ri-verifica reale (non le variabili catturate prima del kill) cosi' la riga
        # di heartbeat sotto riflette lo stato VERO dopo l'auto-correzione, non quello
        # di qualche istante prima.
        batch = trova_processo("trascrivi_locale_episodi")
        whisperx = trova_processo("whisperx")

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
            if len(campi) >= 5:
                try:
                    if float(campi[4]) > SOGLIA_TEMP_GPU_C:
                        anomalie.append(f"GPU a {campi[4]}C, sopra soglia {SOGLIA_TEMP_GPU_C}C")
                except ValueError:
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
