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

import csv
import datetime
import os
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enviar_alerta import enviar_alerta  # noqa: E402

TJMAX_DEFAULT = 100.0  # soglia tipica Ryzen mobile; distanza = TJMAX - temperatura attuale
THROTTLE_SOGLIA_C = 95.0  # coerente con la soglia di throttling osservata su Windows

KILL_CONSECUTIVE = 2  # stessa soglia/logica di hwinfo_temp.py, per coerenza cross-piattaforma
ALARM_FLAG = Path("logs/OVERHEAT_STOP.flag")


def _termina_trascrizione() -> list[str]:
    """Uccide prima whisperx (libera subito la CPU), poi trascrivi_locale_episodi.py
    (evita che passi all'episodio successivo). Stesso criterio di check_batch_health.py."""
    import psutil as _psutil
    uccisi = []
    for pattern in ("whisperx", "trascrivi_locale_episodi"):
        for p in _psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(p.info["cmdline"] or [])
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                continue
            if pattern in cmdline:
                try:
                    p.kill()
                    uccisi.append(f"{pattern} (PID {p.pid})")
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    pass
    return uccisi


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


def loop_log(intervallo_sec, csv_path, kill_cpu_threshold=None):
    nuovo = not os.path.exists(csv_path)
    consecutivi_pericolo = 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if nuovo:
            writer.writerow(["timestamp", "cpu_package_c", "distanza_tjmax_c", "throttling"])
        print(f"Log termico ogni {intervallo_sec}s -> {csv_path} (Ctrl+C per fermare)")
        if kill_cpu_threshold is not None:
            print(f"Soglia di emergenza attiva: {kill_cpu_threshold}C per {KILL_CONSECUTIVE} letture consecutive")
        while True:
            cpu = temperatura_cpu_package()
            dist = (TJMAX_DEFAULT - cpu) if cpu is not None else None
            throttling = "SI" if (cpu is not None and cpu >= THROTTLE_SOGLIA_C) else "NO"
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            writer.writerow([ts, cpu, dist, throttling])
            f.flush()
            print(f"{ts}  CPU: {cpu} C  throttling: {throttling}")

            if kill_cpu_threshold is not None and cpu is not None:
                if cpu >= kill_cpu_threshold:
                    consecutivi_pericolo += 1
                else:
                    consecutivi_pericolo = 0
                if consecutivi_pericolo >= KILL_CONSECUTIVE:
                    print(f"ALLARME TEMPERATURA: CPU a {cpu}C >= {kill_cpu_threshold}C per "
                          f"{KILL_CONSECUTIVE} letture di fila. Fermo la trascrizione.")
                    uccisi = _termina_trascrizione()
                    ALARM_FLAG.parent.mkdir(parents=True, exist_ok=True)
                    testo_alarm = (
                        f"{ts} CPU a {cpu}C >= soglia {kill_cpu_threshold}C. Processi terminati: "
                        f"{', '.join(uccisi) if uccisi else 'nessuno trovato'}\n"
                    )
                    ALARM_FLAG.write_text(testo_alarm, encoding="utf-8")
                    enviar_alerta("SOBRECALENTAMIENTO - transcripcion detenida", testo_alarm)
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
        intervallo = int(resto[0]) if len(resto) > 0 else 60
        csv_path = resto[1] if len(resto) > 1 else "logs/trascrizioni_log_termico.csv"
        loop_log(intervallo, csv_path, kill_cpu_threshold=kill_cpu)
    else:
        filtro = sys.argv[1] if len(sys.argv) > 1 else None
        for chip, label, valore, unit in leggi_sensori(filtro):
            print(f"[{chip}] {label}: {valore:.1f} {unit}")
