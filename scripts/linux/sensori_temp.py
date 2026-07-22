# Equivalente Linux di hwinfo_temp.py: legge le temperature via lm-sensors
# (psutil.sensors_temperatures(), richiede pacchetto lm-sensors + `sensors-detect --auto`
# gia' eseguito), nessun limite di licenza/relaunch a differenza di HWiNFO64 free.
#
# Uso: python3 scripts/linux/sensori_temp.py [filtro_testo]
#      senza argomenti stampa tutte le temperature disponibili
#      python3 scripts/linux/sensori_temp.py --loop N [csv_path] [--kill-cpu SOGLIA]
#      registra la temperatura CPU pacchetto ogni N secondi in un CSV
#      (timestamp,cpu_package_c,distanza_tjmax_c,throttling), stesso formato
#      del CSV prodotto su Windows da hwinfo_temp.py.
#      Con --kill-cpu SOGLIA: se la CPU resta >= SOGLIA per KILL_CONSECUTIVE letture
#      di fila, termina trascrivi_locale_episodi.py + whisperx e scrive
#      logs/OVERHEAT_STOP.flag — stesso meccanismo di hwinfo_temp.py (Windows),
#      QUI ANCORA PIU' IMPORTANTE: il K16 gira headless, senza nessuno che guarda
#      un popup o un terminale.
#      Con --kill-gpu SOGLIA: stessa logica ma sulla temperatura GPU (nvidia-smi),
#      aggiunta 2026-07-22 con l'arrivo della RTX 5070 — prima di allora non
#      esisteva nessuna sicurezza termica per la GPU durante il batch headless.

import csv
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enviar_alerta import enviar_alerta  # noqa: E402
from kill_coordinado import matar_trascrizione  # noqa: E402

TJMAX_DEFAULT = 100.0  # soglia tipica Ryzen mobile; distanza = TJMAX - temperatura attuale
THROTTLE_SOGLIA_C = 95.0  # coerente con la soglia di throttling osservata su Windows

KILL_CONSECUTIVE = 2  # stessa soglia/logica di hwinfo_temp.py, per coerenza cross-piattaforma
ALARM_FLAG = Path("logs/OVERHEAT_STOP.flag")


def _termina_trascrizione(motivo="surriscaldamento CPU") -> list[str]:
    """Uccide whisperx + trascrivi_locale_episodi.py (non il wrapper bash: vede
    OVERHEAT_STOP.flag e si ferma da solo senza passare all'anno successivo)."""
    _, riga = matar_trascrizione(
        origine="sensori_temp.py", motivo=motivo, aggressivo=False,
    )
    return [riga]


def temperatura_gpu():
    """Legge la temperatura GPU via nvidia-smi. None se non disponibile
    (nessuna GPU NVIDIA, driver non caricato, o comando assente) — non deve
    mai far fallire il resto del loop, la GPU e' un extra opzionale qui."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip().splitlines()[0])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def leggi_sensori(filtro=None):
    tutte = psutil.sensors_temperatures()
    if not tutte:
        print("Nessun sensore disponibile. Verifica che lm-sensors sia installato e che "
              "'sudo sensors-detect --auto' sia stato eseguito almeno una volta.")
        sys.exit(1)
    righe = []
    for nome_chip, letture in tutte.items():
        for r in letture:
            label = r.label or nome_chip
            if filtro and filtro.lower() not in label.lower() and filtro.lower() not in nome_chip.lower():
                continue
            righe.append((nome_chip, label, r.current, "C"))
    return righe


def temperatura_cpu_package():
    """Cerca la lettura piu' rappresentativa del pacchetto CPU (k10temp su Ryzen: 'Tctl'/'Tdie')."""
    tutte = psutil.sensors_temperatures()
    for nome_chip in ("k10temp", "zenpower"):
        for r in tutte.get(nome_chip, []):
            if r.label in ("Tctl", "Tdie", ""):
                return r.current
    # fallback: prima lettura disponibile di un chip qualsiasi
    for letture in tutte.values():
        if letture:
            return letture[0].current
    return None


def _escribir_fila(csv_path, fila):
    """Abre, escribe y cierra en cada llamada (en vez de mantener el archivo
    abierto durante todo el bucle). Un handle de larga duracion se queda
    'huerfano' y deja de escribir sin avisar si algo externo (ej. un
    'git pull --rebase' que toque este mismo archivo) recrea el inodo por
    debajo - vivido en produccion el 2026-07-16, varias horas de log termico
    parado sin que el proceso muriera ni diera ningun error."""
    nuevo = (not os.path.exists(csv_path)) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if nuevo:
            writer.writerow(["timestamp", "cpu_package_c", "distanza_tjmax_c", "throttling", "gpu_c"])
        writer.writerow(fila)


GPU_NONE_AVVISO_CONSECUTIVE = 5  # ~5 min con intervallo 60s: avvisa (non uccide) se nvidia-smi smette di rispondere


def loop_log(intervallo_sec, csv_path, kill_cpu_threshold=None, kill_gpu_threshold=None):
    consecutivi_pericolo_cpu = 0
    consecutivi_pericolo_gpu = 0
    consecutivi_gpu_none = 0
    avviso_gpu_none_inviato = False
    print(f"Log termico ogni {intervallo_sec}s -> {csv_path} (Ctrl+C per fermare)")
    if kill_cpu_threshold is not None:
        print(f"Soglia di emergenza CPU attiva: {kill_cpu_threshold}C per {KILL_CONSECUTIVE} letture consecutive")
    if kill_gpu_threshold is not None:
        print(f"Soglia di emergenza GPU attiva: {kill_gpu_threshold}C per {KILL_CONSECUTIVE} letture consecutive")
    while True:
        cpu = temperatura_cpu_package()
        gpu = temperatura_gpu()
        dist = (TJMAX_DEFAULT - cpu) if cpu is not None else None
        throttling = "SI" if (cpu is not None and cpu >= THROTTLE_SOGLIA_C) else "NO"
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        _escribir_fila(csv_path, [ts, cpu, dist, throttling, gpu])
        print(f"{ts}  CPU: {cpu} C  throttling: {throttling}  GPU: {gpu} C")

        if kill_gpu_threshold is not None:
            if gpu is None:
                consecutivi_gpu_none += 1
                if consecutivi_gpu_none >= GPU_NONE_AVVISO_CONSECUTIVE and not avviso_gpu_none_inviato:
                    testo = (
                        f"{ts} nvidia-smi non risponde da {consecutivi_gpu_none} letture consecutive "
                        f"(~{consecutivi_gpu_none * intervallo_sec // 60} min). La soglia di emergenza GPU "
                        f"({kill_gpu_threshold}C) e' DISATTIVATA di fatto finche' non torna a rispondere — "
                        f"la trascrizione continua SENZA sicurezza termica GPU. Controllare il driver/hardware."
                    )
                    print(f"AVVISO: {testo}")
                    enviar_alerta("ATTENZIONE - monitoraggio GPU non risponde (nessun kill)", testo)
                    avviso_gpu_none_inviato = True
            else:
                consecutivi_gpu_none = 0
                avviso_gpu_none_inviato = False

        if kill_cpu_threshold is not None and cpu is not None:
            if cpu >= kill_cpu_threshold:
                consecutivi_pericolo_cpu += 1
            else:
                consecutivi_pericolo_cpu = 0
            if consecutivi_pericolo_cpu >= KILL_CONSECUTIVE:
                print(f"ALLARME TEMPERATURA CPU: {cpu}C >= {kill_cpu_threshold}C per "
                      f"{KILL_CONSECUTIVE} letture di fila. Fermo la trascrizione.")
                uccisi = _termina_trascrizione(motivo="surriscaldamento CPU")
                ALARM_FLAG.parent.mkdir(parents=True, exist_ok=True)
                testo_alarm = (
                    f"{ts} CPU a {cpu}C >= soglia {kill_cpu_threshold}C. Processi terminati: "
                    f"{', '.join(uccisi) if uccisi else 'nessuno trovato'}\n"
                )
                ALARM_FLAG.write_text(testo_alarm, encoding="utf-8")
                enviar_alerta("SOBRECALENTAMIENTO CPU - transcripcion detenida", testo_alarm)
                return

        if kill_gpu_threshold is not None and gpu is not None:
            if gpu >= kill_gpu_threshold:
                consecutivi_pericolo_gpu += 1
            else:
                consecutivi_pericolo_gpu = 0
            if consecutivi_pericolo_gpu >= KILL_CONSECUTIVE:
                print(f"ALLARME TEMPERATURA GPU: {gpu}C >= {kill_gpu_threshold}C per "
                      f"{KILL_CONSECUTIVE} letture di fila. Fermo la trascrizione.")
                uccisi = _termina_trascrizione(motivo="surriscaldamento GPU")
                ALARM_FLAG.parent.mkdir(parents=True, exist_ok=True)
                testo_alarm = (
                    f"{ts} GPU a {gpu}C >= soglia {kill_gpu_threshold}C. Processi terminati: "
                    f"{', '.join(uccisi) if uccisi else 'nessuno trovato'}\n"
                )
                ALARM_FLAG.write_text(testo_alarm, encoding="utf-8")
                enviar_alerta("SOBRECALENTAMIENTO GPU - transcripcion detenida", testo_alarm)
                return

        time.sleep(intervallo_sec)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        resto = sys.argv[2:]
        kill_cpu = None
        if "--kill-cpu" in resto:
            i = resto.index("--kill-cpu")
            kill_cpu = float(resto[i + 1])
            del resto[i:i + 2]
        kill_gpu = None
        if "--kill-gpu" in resto:
            i = resto.index("--kill-gpu")
            kill_gpu = float(resto[i + 1])
            del resto[i:i + 2]
        intervallo = int(resto[0]) if len(resto) > 0 else 60
        csv_path = resto[1] if len(resto) > 1 else "logs/trascrizioni_log_termico.csv"
        loop_log(intervallo, csv_path, kill_cpu_threshold=kill_cpu, kill_gpu_threshold=kill_gpu)
    else:
        filtro = sys.argv[1] if len(sys.argv) > 1 else None
        for chip, label, valore, unit in leggi_sensori(filtro):
            print(f"[{chip}] {label}: {valore:.1f} {unit}")
