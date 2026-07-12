# Lettore della memoria condivisa di HWiNFO64 ("Compatibilita' con memoria condivisa"
# deve essere attiva in HWiNFO64 -> Ajustes -> Interfaz general/de usuario).
# Stampa le temperature (e opzionalmente altri readings) esposte in tempo reale,
# usato per monitorare il carico termico durante la trascrizione WhisperX su CPU.
#
# Uso: python scripts/hwinfo_temp.py [filtro_testo]
#      senza argomenti stampa tutte le letture di tipo temperatura
#      python scripts/hwinfo_temp.py --loop N [csv_path] [--kill-cpu SOGLIA]
#      registra "CPU Entera" ogni N secondi in un CSV (timestamp,valore,unit),
#      per avere una traccia persistente durante un batch lungo incustodito.
#      Con --kill-cpu SOGLIA: se la CPU resta >= SOGLIA per KILL_CONSECUTIVE
#      letture di fila (non una sola, per evitare falsi allarmi da glitch del
#      sensore), termina trascrivi_locale_episodi.py E il sottoprocesso
#      whisperx, scrive logs/OVERHEAT_STOP.flag (letto da
#      avvia_trascrizione_sicura.ps1 per mostrare l'allarme in terminale) e si
#      ferma anche lui.

import csv
import ctypes
from ctypes import wintypes
import datetime
from pathlib import Path
import sys
import time

KILL_CONSECUTIVE = 2
ALARM_FLAG = Path("logs/OVERHEAT_STOP.flag")

SHARED_MEM_NAME = "Global\\HWiNFO_SENS_SM2"
FILE_MAP_READ = 0x0004

kernel32 = ctypes.windll.kernel32
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
kernel32.UnmapViewOfFile.restype = wintypes.BOOL
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]

SENSOR_TYPE_LABELS = {
    0: "NONE", 1: "TEMP", 2: "VOLT", 3: "FAN",
    4: "CURRENT", 5: "POWER", 6: "CLOCK", 7: "USAGE", 8: "OTHER",
}


class SharedMemHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwVersion", ctypes.c_uint32),
        ("dwRevision", ctypes.c_uint32),
        ("poll_time", ctypes.c_int64),
        ("dwOffsetOfSensorSection", ctypes.c_uint32),
        ("dwSizeOfSensorElement", ctypes.c_uint32),
        ("dwNumSensorElements", ctypes.c_uint32),
        ("dwOffsetOfReadingSection", ctypes.c_uint32),
        ("dwSizeOfReadingElement", ctypes.c_uint32),
        ("dwNumReadingElements", ctypes.c_uint32),
    ]


class ReadingElement(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("tReading", ctypes.c_uint32),
        ("dwSensorIndex", ctypes.c_uint32),
        ("dwReadingID", ctypes.c_uint32),
        ("szLabelOrig", ctypes.c_char * 128),
        ("szLabelUser", ctypes.c_char * 128),
        ("szUnit", ctypes.c_char * 16),
        ("Value", ctypes.c_double),
        ("ValueMin", ctypes.c_double),
        ("ValueMax", ctypes.c_double),
        ("ValueAvg", ctypes.c_double),
    ]


def leggi_sensori(filtro=None):
    h = kernel32.OpenFileMappingW(FILE_MAP_READ, False, SHARED_MEM_NAME)
    if not h:
        print(f"Impossibile aprire la memoria condivisa '{SHARED_MEM_NAME}' (errore {ctypes.GetLastError()}).")
        print("Verifica che in HWiNFO64 la finestra Sensori sia aperta e 'Compatibilidad con memoria compartida' attivo.")
        sys.exit(1)

    # prima mappatura: solo l'header, per sapere quanto leggere in totale
    ptr = kernel32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, ctypes.sizeof(SharedMemHeader))
    if not ptr:
        print(f"MapViewOfFile fallita (errore {ctypes.GetLastError()}).")
        sys.exit(1)
    header = SharedMemHeader.from_buffer_copy(ctypes.string_at(ptr, ctypes.sizeof(SharedMemHeader)))
    kernel32.UnmapViewOfFile(ptr)

    if header.dwSignature != 0x53695748:  # 'SiWH'
        print("Firma memoria condivisa non valida — HWiNFO potrebbe non essere in esecuzione.")
        kernel32.CloseHandle(h)
        sys.exit(1)

    totale = header.dwOffsetOfReadingSection + header.dwNumReadingElements * header.dwSizeOfReadingElement
    ptr = kernel32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, totale)
    if not ptr:
        print(f"MapViewOfFile (full) fallita (errore {ctypes.GetLastError()}).")
        sys.exit(1)
    full = ctypes.string_at(ptr, totale)
    kernel32.UnmapViewOfFile(ptr)
    kernel32.CloseHandle(h)

    righe = []
    for i in range(header.dwNumReadingElements):
        offset = header.dwOffsetOfReadingSection + i * header.dwSizeOfReadingElement
        elem = ReadingElement.from_buffer_copy(full[offset:offset + ctypes.sizeof(ReadingElement)])
        label = elem.szLabelUser.decode("latin-1", errors="replace").strip("\x00") or \
            elem.szLabelOrig.decode("latin-1", errors="replace").strip("\x00")
        tipo = SENSOR_TYPE_LABELS.get(elem.tReading, str(elem.tReading))
        unit = elem.szUnit.decode("latin-1", errors="replace").strip("\x00")
        if filtro and filtro.lower() not in label.lower():
            continue
        if not filtro and tipo != "TEMP":
            continue
        righe.append((tipo, label, elem.Value, unit))
    return righe


def _termina_trascrizione():
    """Uccide prima il sottoprocesso whisperx (libera subito la CPU), poi
    trascrivi_locale_episodi.py (evita che passi all'episodio successivo).
    Stesso criterio di matching di check_batch_health.ps1 (CommandLine)."""
    import psutil
    uccisi = []
    for pattern in ("whisperx", "trascrivi_locale_episodi"):
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(p.info["cmdline"] or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if pattern in cmdline:
                try:
                    p.kill()
                    uccisi.append(f"{pattern} (PID {p.pid})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    return uccisi


def loop_log(intervallo_sec, csv_path, kill_cpu_threshold=None):
    """Scrive una riga CSV (timestamp, cpu_package, distanza_tjmax, throttling) ogni intervallo_sec."""
    nuovo = not __import__("os").path.exists(csv_path)
    consecutivi_pericolo = 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if nuovo:
            writer.writerow(["timestamp", "cpu_package_c", "distanza_tjmax_c", "throttling"])
        print(f"Log termico ogni {intervallo_sec}s -> {csv_path} (Ctrl+C per fermare)")
        if kill_cpu_threshold is not None:
            print(f"Soglia di emergenza attiva: {kill_cpu_threshold}C per {KILL_CONSECUTIVE} letture consecutive")
        while True:
            righe = leggi_sensori(None)
            cpu = next((v for t, l, v, u in righe if "CPU Entera" in l), None)
            dist = next((v for t, l, v, u in righe if "Core 0 Distancia" in l), None)
            throttling = "?"
            for t, l, v, u in leggi_sensori("Desaceleraci"):
                throttling = "SI" if v else "NO"
                break
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
                    ALARM_FLAG.write_text(
                        f"{ts} CPU a {cpu}C >= soglia {kill_cpu_threshold}C. Processi terminati: "
                        f"{', '.join(uccisi) if uccisi else 'nessuno trovato'}\n",
                        encoding="utf-8",
                    )
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
        csv_path = resto[1] if len(resto) > 1 else "hwinfo_temp_log.csv"
        loop_log(intervallo, csv_path, kill_cpu_threshold=kill_cpu)
    else:
        filtro = sys.argv[1] if len(sys.argv) > 1 else None
        for tipo, label, valore, unit in leggi_sensori(filtro):
            print(f"[{tipo}] {label}: {valore:.1f} {unit}")
