# Resumen diario por email de todo el trabajo cumplido: transcripcion K16,
# clasificacion Groq/Cerebras (HP14, via data/estado_clasificacion.json ya
# sincronizado por git), y alertas/errores. Reusa el mismo SMTP ya
# configurado y probado en enviar_alerta.py -- no se crea infraestructura
# nueva de email.
#
# Pensado para leerse SIN conocimientos tecnicos: frases completas, sin
# jerga, con el periodo exacto que cubre (para no confundir "poco trabajo
# hoy" con "el dia recien empezo"), y con el AVANCE TOTAL del proyecto
# ademas del delta del dia -- lo que de verdad importa para saber "como va
# el proceso", no solo cuantos episodios cayeron en las ultimas horas.
#
# Se ejecuta una vez al dia via timer systemd (ilvolo-resumen-diario.timer,
# ver SETUP.md para instalarlo). Uso manual: python3 scripts/linux/resumen_diario.py

import datetime
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enviar_alerta import enviar_alerta  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = REPO / "logs"
DATA_DIR = REPO / "data"
NAS_ROOT = Path("/mnt/ilvolo-audio-backup")
DURACION_MEDIA_MIN = 55


def _es_de_hoy(path: Path, hoy: datetime.date) -> bool:
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).date() == hoy
    except OSError:
        return False


def estado_en_vivo() -> list[str]:
    """Que esta pasando AHORA MISMO, en el momento de mandar el email --
    corre localmente en el K16 (sin SSH), no hay riesgo de auto-matcheo del
    propio pgrep como en el panel del HP14."""
    lineas = []
    r = subprocess.run(
        ["pgrep", "-af", "whisperx"], capture_output=True, text=True, check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        primera = r.stdout.strip().splitlines()[0]
        partes_mp3 = [tok for tok in primera.split() if tok.endswith(".mp3")]
        episodio = Path(partes_mp3[0]).name if partes_mp3 else "?"
        lineas.append(f"Ahora mismo: transcribiendo el episodio {episodio}.")
    else:
        lineas.append("Ahora mismo: NO hay ninguna transcripción en curso.")
    return lineas


def resumen_transcripcion_hoy(hoy: datetime.date, ahora: datetime.datetime) -> list[str]:
    lineas = [
        f"— Hoy ({hoy.isoformat()}, desde medianoche hasta las {ahora.strftime('%H:%M')}) —"
    ]
    trascrizioni_dir = DATA_DIR / "trascrizioni"
    episodios_hoy = sorted(
        p.stem for p in trascrizioni_dir.glob("*.json") if _es_de_hoy(p, hoy)
    ) if trascrizioni_dir.exists() else []

    if episodios_hoy:
        horas_aprox = len(episodios_hoy) * DURACION_MEDIA_MIN / 60
        lineas.append(
            f"Se terminaron {len(episodios_hoy)} episodio(s) ({', '.join(episodios_hoy)}), "
            f"unas {horas_aprox:.1f} horas de trabajo de transcripción."
        )
    else:
        lineas.append("Todavía no se ha terminado ningún episodio hoy.")

    csv_termico = LOGS_DIR / "trascrizioni_log_termico.csv"
    if csv_termico.exists():
        temps_hoy = []
        throttling_hoy = 0
        for linea in csv_termico.read_text(encoding="utf-8", errors="replace").splitlines():
            campos = linea.strip().split(",")
            if len(campos) < 4:
                continue
            try:
                ts = datetime.datetime.fromisoformat(campos[0])
            except ValueError:
                continue
            if ts.date() != hoy:
                continue
            try:
                temps_hoy.append(float(campos[1]))
            except ValueError:
                pass
            if campos[3].strip().upper() == "SI":
                throttling_hoy += 1
        if temps_hoy:
            aviso_calor = (
                f" (con throttling por calor en {throttling_hoy} lectura(s) — conviene vigilarlo)"
                if throttling_hoy else ""
            )
            lineas.append(
                f"La temperatura del procesador se mantuvo entre {min(temps_hoy):.0f}°C y "
                f"{max(temps_hoy):.0f}°C{aviso_calor}."
            )

    return lineas


def progreso_total() -> list[str]:
    """A diferencia de la seccion 'Hoy', esto responde 'como va el proyecto
    en total', que es lo que de verdad indica si el proceso avanza bien."""
    lineas = ["— Avance total del proyecto —"]
    trascrizioni_dir = DATA_DIR / "trascrizioni"
    completados = len(list(trascrizioni_dir.glob("*.json"))) if trascrizioni_dir.exists() else 0

    pendientes = None
    if NAS_ROOT.exists():
        r = subprocess.run(
            ["find", str(NAS_ROOT), "-mindepth", "2", "-maxdepth", "2", "-iname", "*.mp3"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            pendientes = len([l for l in r.stdout.splitlines() if l.strip()])

    if pendientes is not None:
        total = completados + pendientes
        porcentaje = (completados / total * 100) if total else 0
        lineas.append(
            f"Van {completados} de {total} episodios transcritos ({porcentaje:.0f}% completado), "
            f"quedan {pendientes} por hacer."
        )

        # Ritmo real de los ultimos 7 dias (no una media teorica vieja) para
        # dar una estimacion honesta, incluyendo pausas/interrupciones reales.
        hace_7_dias = datetime.datetime.now() - datetime.timedelta(days=7)
        recientes = [
            p for p in trascrizioni_dir.glob("*.json")
            if datetime.datetime.fromtimestamp(p.stat().st_mtime) >= hace_7_dias
        ] if trascrizioni_dir.exists() else []
        ritmo_diario = len(recientes) / 7
        if ritmo_diario > 0:
            dias_restantes = pendientes / ritmo_diario
            lineas.append(
                f"Al ritmo real de esta última semana (~{ritmo_diario:.1f} episodios/día en promedio, "
                f"incluyendo pausas), quedan aproximadamente {dias_restantes:.0f} días de trabajo."
            )
        else:
            lineas.append(
                "No se completó ningún episodio en la última semana — no se puede estimar cuánto falta."
            )
    else:
        lineas.append(
            f"Van {completados} episodios transcritos en total "
            "(no se pudo consultar cuántos quedan: el disco del NAS no está accesible ahora mismo)."
        )

    return lineas


def resumen_clasificacion(hoy: datetime.date) -> list[str]:
    lineas = ["— Clasificación de contenidos (Groq/Cerebras, en el HP14) —"]
    estado_path = DATA_DIR / "estado_clasificacion.json"
    if not estado_path.exists():
        lineas.append("Todavía no hay ningún dato de clasificación sincronizado desde el HP14.")
        return lineas
    try:
        estado = json.loads(estado_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        lineas.append("No se pudo leer el estado de clasificación (archivo dañado o ilegible).")
        return lineas

    ultima = estado.get("ultima_ejecucion", "")
    resultado = estado.get("resultado")
    archivos = estado.get("archivos_clasificados", "?")
    es_de_hoy = ultima.startswith(hoy.isoformat())
    nota_fecha = "" if es_de_hoy else f" (el dato más reciente es de {ultima[:10] or 'fecha desconocida'}, todavía no se actualizó hoy)"

    if resultado == "ok":
        lineas.append(f"Funcionó bien{nota_fecha}: se clasificaron {archivos} archivo(s) nuevos.")
    else:
        mensaje = estado.get("mensaje", "sin más detalles")
        lineas.append(
            f"Tuvo un problema{nota_fecha}: {mensaje}. Puede necesitar revisión manual."
        )
    return lineas


def resumen_alertas(hoy: datetime.date) -> list[str]:
    lineas = ["— Alertas y problemas técnicos —"]
    avisos = []

    alert_path = LOGS_DIR / "batch_health_ALERT.txt"
    if alert_path.exists():
        entradas_hoy = [
            l for l in alert_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.startswith(hoy.isoformat())
        ]
        if entradas_hoy:
            avisos.append(f"Se detectaron {len(entradas_hoy)} anomalía(s) en el chequeo de salud del batch hoy.")

    if (LOGS_DIR / "OVERHEAT_STOP.flag").exists():
        avisos.append(
            "IMPORTANTE: hubo una parada de emergencia por sobrecalentamiento y todavía no se resolvió."
        )

    watchdog_log = LOGS_DIR / "watchdog_nas.log"
    if watchdog_log.exists():
        reparaciones_hoy = [
            l for l in watchdog_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.startswith(hoy.isoformat()) and ("riparat" in l or "ERRORE" in l or "rilancio" in l)
        ]
        if reparaciones_hoy:
            avisos.append(
                f"El conexión al NAS se cayó y se reparó sola (o el proceso se tuvo que relanzar) "
                f"{len(reparaciones_hoy)} vez/veces hoy."
            )

    autocommit_log = LOGS_DIR / "autocommit_dati.log"
    if autocommit_log.exists():
        errores_hoy = [
            l for l in autocommit_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.startswith(hoy.isoformat()) and "ERRORE" in l
        ]
        if errores_hoy:
            avisos.append(f"Hubo {len(errores_hoy)} error(es) al guardar los datos en git hoy.")

    if avisos:
        lineas.extend(avisos)
    else:
        lineas.append("Ninguno. Todo funcionó sin problemas técnicos hoy.")

    return lineas


def main() -> None:
    ahora = datetime.datetime.now()
    hoy = ahora.date()

    cuerpo = [f"Resumen de 'Il volo del mattino' — {hoy.isoformat()} {ahora.strftime('%H:%M')}", ""]
    cuerpo += estado_en_vivo()
    cuerpo.append("")
    cuerpo += resumen_transcripcion_hoy(hoy, ahora)
    cuerpo.append("")
    cuerpo += progreso_total()
    cuerpo.append("")
    cuerpo += resumen_clasificacion(hoy)
    cuerpo.append("")
    cuerpo += resumen_alertas(hoy)

    texto = "\n".join(cuerpo)
    print(texto)
    ok = enviar_alerta(f"Resumen diario {hoy.isoformat()}", texto)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
