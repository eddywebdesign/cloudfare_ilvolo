# Equivalente Linux di hwinfo_temp.py: legge le temperature via lm-sensors
# (psutil.sensors_temperatures(), richiede pacchetto lm-sensors + `sensors-detect --auto`
# gia' eseguito), nessun limite di licenza/relaunch a differenza di HWiNFO64 free.
#
# Uso: python3 scripts/linux/sensori_temp.py [filtro_testo]
#      senza argomenti stampa tutte le temperature disponibili
#      python3 scripts/linux/sensori_temp.py --loop N [csv_path]
#      registra la temperatura CPU pacchetto ogni N secondi in un CSV
#      (timestamp,cpu_package_c,distanza_tjmax_c,throttling), stesso formato
#      del CSV prodotto su Windows da hwinfo_temp.py.

import csv
import datetime
import os
import sys
import time

import psutil

TJMAX_DEFAULT = 100.0  # soglia tipica Ryzen mobile; distanza = TJMAX - temperatura attuale
THROTTLE_SOGLIA_C = 95.0  # coerente con la soglia di throttling osservata su Windows


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


def loop_log(intervallo_sec, csv_path):
    nuovo = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if nuovo:
            writer.writerow(["timestamp", "cpu_package_c", "distanza_tjmax_c", "throttling"])
        print(f"Log termico ogni {intervallo_sec}s -> {csv_path} (Ctrl+C per fermare)")
        while True:
            cpu = temperatura_cpu_package()
            dist = (TJMAX_DEFAULT - cpu) if cpu is not None else None
            throttling = "SI" if (cpu is not None and cpu >= THROTTLE_SOGLIA_C) else "NO"
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            writer.writerow([ts, cpu, dist, throttling])
            f.flush()
            print(f"{ts}  CPU: {cpu} C  throttling: {throttling}")
            time.sleep(intervallo_sec)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        intervallo = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        csv_path = sys.argv[3] if len(sys.argv) > 3 else "logs/trascrizioni_log_termico.csv"
        loop_log(intervallo, csv_path)
    else:
        filtro = sys.argv[1] if len(sys.argv) > 1 else None
        for chip, label, valore, unit in leggi_sensori(filtro):
            print(f"[{chip}] {label}: {valore:.1f} {unit}")
