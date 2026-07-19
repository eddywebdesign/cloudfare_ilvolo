# Panel unico del HP14: estado (clasificacion + tareas Windows) y ahora
# tambien la transcripcion del K16 EN VIVO + control remoto, por SSH por
# clave ya configurado (eddy@192.168.8.132, sin contrasena). Sustituye a la
# version solo-lectura anterior: el usuario pidio un unico panel porque no
# tiene pantalla fisica en el K16 habitualmente.
#
# El panel del K16 (scripts/linux/panel_control.py) NO se toca -- se deja
# tal cual esta, a pedido explicito del usuario. "Detener al finalizar" se
# implementa por completo aqui (vigilando el PID remoto cada refresco), sin
# depender del estado interno de ese otro panel.
#
# Uso: python scripts\panel_estado_hp14.py
# (o doble clic en abrir_panel_estado.bat)

import base64
import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from dati_root import logs_root  # noqa: E402

ESTADO_PATH = logs_root(REPO) / "estado_clasificacion.json"
# Fallback esplicito al path di rete noto: se ILVOLO_DATA_DIR non e' visibile
# nel processo corrente (setx non si propaga a sessioni/processi gia' aperti
# finche' non c'e' un logout/login o un riavvio di explorer.exe - capitato
# davvero il 18/07/2026, pannello bloccato su "Sin datos" con dati freschi e
# validi sullo share), non arrenderti al path locale del repo (che non ha mai
# questo file) se il path di rete e' comunque raggiungibile.
ESTADO_PATH_FALLBACK = Path(r"\\192.168.8.80\Media\ilvolodellasera\logs\estado_clasificacion.json")
LOG_SYNC_PATH = REPO / "logs" / "sync_snapshot_data.log"
TAREA_SYNC = "ilvolo-sync-snapshot-data"
# Persistido en disco local del HP14 (no en el share): mismo motivo que en
# panel_control.py (K16) - si este panel se cierra/crashea con una parada
# programada pendiente, no debe perderse en silencio.
FLAG_STOP_PENDIENTE = REPO / "data" / "panel_hp14_stop_pendiente.flag"
INTERVALO_MS = 15000

K16_HOST = "eddy@192.168.8.132"  # Ethernet fija (IP fija por regla DHCP), antes .130 por WiFi
SSH_TIMEOUT_S = 8
DURACION_MEDIA_MIN = 55  # misma media usada en scripts/linux/panel_control.py
RETRASO_REANUDAR_MIN = 30  # ver nota en k16_reanudar()

# En Windows, subprocess abre una consola visible por cada llamada (ssh.exe,
# powershell.exe) aunque el panel se lance con pythonw -- con el refresco
# cada 15s eso se traduce en ventanas parpadeando sin parar. CREATE_NO_WINDOW
# las suprime.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

COLOR_FONDO = "#1e2530"
COLOR_TARJETA = "#2a3342"
COLOR_TEXTO = "#e6e9ef"
COLOR_TEXTO_SUAVE = "#9aa5b1"
COLOR_VERDE = "#3ba776"
COLOR_ROJO = "#c0392b"
COLOR_NARANJA = "#c9822a"


def leer_estado_clasificacion():
    path = ESTADO_PATH if ESTADO_PATH.exists() else ESTADO_PATH_FALLBACK
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def leer_ultimo_push():
    """Ultima riga rilevante (PUSH OK / ERRORE / Nessuna modifica) del log di
    sync_snapshot_data.ps1 -- dice se l'ultimo giro schedulato e' arrivato
    fino al push o si e' fermato prima (es. working tree sporco, vedi
    scripts/sync_snapshot_data.ps1). Le righe iniziano con un timestamp ISO
    scritto da Scrivi(), es. '2026-07-19T12:04:58 PUSH OK: 3 file...'."""
    if not LOG_SYNC_PATH.exists():
        return None
    try:
        righe = LOG_SYNC_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for riga in reversed(righe):
        if riga.startswith("20") and any(
            m in riga for m in ("PUSH OK", "ERRORE", "Nessuna modifica")
        ):
            return riga.strip()
    return None


def leer_tarea_programada(nombre):
    """Devuelve (ultima_ejecucion, resultado, proxima_ejecucion) o None si la
    tarea no existe. Usa Get-ScheduledTaskInfo, solo lectura."""
    ps = (
        f"$i = Get-ScheduledTask -TaskName '{nombre}' -ErrorAction SilentlyContinue "
        f"| Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; "
        f"if ($i) {{ '{{0}}|{{1}}|{{2}}' -f $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime }}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=10, check=False,
            creationflags=_NO_WINDOW,
        )
        salida = r.stdout.strip()
        if not salida or "|" not in salida:
            return None
        ultima, resultado, proxima = salida.split("|")
        return ultima, resultado, proxima
    except (subprocess.TimeoutExpired, OSError):
        return None


def ssh_run(comando_remoto, timeout=SSH_TIMEOUT_S):
    """Ejecuta un comando en el K16 por SSH (clave ya configurada, sin
    contrasena). BatchMode=yes: si la clave fallara, falla rapido en vez de
    pedir contrasena y colgar la UI. Devuelve (ok, stdout) -- ok=False si
    hubo timeout, error de conexion o codigo de salida != 0."""
    try:
        r = subprocess.run(
            [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new",
                K16_HOST, comando_remoto,
            ],
            capture_output=True, text=True, timeout=timeout, check=False,
            creationflags=_NO_WINDOW,
        )
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)


def ssh_run_script(script_bash, timeout=SSH_TIMEOUT_S):
    """Como ssh_run, pero para scripts bash con comillas/backslashes. Los
    manda en base64 (echo <b64> | base64 -d | bash) para evitar que el
    doble nivel de comillas (Windows subprocess -> ssh.exe -> bash remoto)
    corrompa el comando -- se detecto en la practica que las comillas
    anidadas se rompian al pasar por list2cmdline de Windows."""
    encoded = base64.b64encode(script_bash.encode("utf-8")).decode("ascii")
    return ssh_run(f"echo {encoded} | base64 -d | bash", timeout=timeout)


def leer_estado_k16():
    """Un solo comando remoto que junta: proceso whisperx (pid + nombre
    episodio + segundos transcurridos), ultima temperatura del CSV termico, y
    si el watchdog NAS esta activo. Formato de salida:
    'PID|EPISODIO|ETIME_S|TERM_LINE|WATCHDOG' (campos vacios si no aplica).

    Patron '[w]hisperx' (no 'whisperx' a secas): pgrep -f matchea contra la
    linea de comandos COMPLETA del proceso que lo invoca via ssh, que
    contiene literalmente el texto del patron -- sin el truco del corchete
    pgrep se detecta a si mismo como si fuera una transcripcion en curso
    (mismo gotcha ya documentado con pkill -f en la sesion RDP del K16)."""
    script = r"""
cd ~/ilvolodelmattino 2>/dev/null || cd ~/Documenti/ilvolodelmattino 2>/dev/null
PID=$(pgrep -f '[w]hisperx' | head -1)
EP=""
ETIME=""
if [ -n "$PID" ]; then
  EP=$(ps -p "$PID" -o args= | grep -oE '[^ /]*\.mp3' | head -1)
  ETIME=$(ps -p "$PID" -o etimes= | tr -d ' ')
fi
TERM_LINE=$(tail -1 logs/trascrizioni_log_termico.csv 2>/dev/null)
WD=$(systemctl --user is-active ilvolo-watchdog-nas.timer 2>/dev/null)
echo "${PID}|${EP}|${ETIME}|${TERM_LINE}|${WD}"
"""
    ok, salida = ssh_run_script(script)
    if not ok:
        return None, salida
    campos = salida.split("|")
    if len(campos) < 5:
        return None, "respuesta inesperada del K16"
    pid = campos[0].strip()
    episodio = campos[1].strip()
    etime_s = campos[2].strip()
    term_line = campos[3].strip()  # "ts,temp,otro,throttling" (CSV, no pipes)
    watchdog = campos[4].strip()
    term_campos = term_line.split(",")
    term_ts = term_campos[0].strip() if len(term_campos) > 0 and term_campos[0] else None
    term_c = term_campos[1].strip() if len(term_campos) > 1 else None
    return {
        "pid": pid or None,
        "episodio": episodio or None,
        "etime_s": int(etime_s) if etime_s.isdigit() else None,
        "temp_ts": term_ts or None,
        "temp_c": term_c or None,
        "watchdog": watchdog,
    }, None


def k16_detener_ahora():
    # Antes: pkill/tmux kill-session escritos a mano aqui, un cuarto mecanismo
    # de kill independiente del panel local (panel_control.py), sensori_temp.py
    # y check_batch_health.py, sin registro compartido de quien mato que y por
    # que. Ahora invoca el mismo punto unico (kill_coordinado.py) que usan los
    # otros tres, via SSH -- un solo lugar donde se decide como matar la
    # transcripcion, un solo log (logs/kill_events.log) para reconstruir
    # cualquier incidente futuro.
    script = (
        "cd ~/ilvolodelmattino && python3 scripts/linux/kill_coordinado.py "
        "--origine 'pannello HP14' --motivo 'bottone Detener AHORA (remoto)'"
    )
    ok, salida = ssh_run_script(script)
    detenido = ok  # kill_coordinado.py sale con 0 si detenido, 1 si no
    return detenido, salida


def k16_reanudar(retraso_min=RETRASO_REANUDAR_MIN):
    """NO reactiva el timer al instante. `ilvolo-watchdog-nas.timer` tiene
    Persistent=true (ver scripts/linux/ilvolo-watchdog-nas.timer): al
    reactivarlo con `systemctl start` tras haber estado parado, systemd lo
    considera un intervalo "perdido" y dispara una ejecucion de catch-up
    INMEDIATA -- eso fue lo que relanzo el batch sin aviso la vez anterior.
    En su lugar, se programa la reactivacion con `systemd-run --on-active`
    (transient timer con nombre unico), dando margen real antes de que
    vuelva a arrancar la transcripcion. Devuelve (ok, nombre_unidad,
    hora_prevista) para poder cancelarla despues si hace falta."""
    unidad = f"ilvolo-watchdog-nas-resume-{int(time.time())}"
    script = f"""
systemd-run --user --unit={unidad} --on-active={retraso_min}min \
  bash -c "systemctl --user start ilvolo-watchdog-nas.timer"
"""
    ok, salida = ssh_run_script(script)
    return ok, unidad, salida


def k16_cancelar_reanudar_programado(unidad):
    if not unidad:
        return True
    script = f"""
systemctl --user stop {unidad}.timer 2>/dev/null
systemctl --user reset-failed {unidad}.timer {unidad}.service 2>/dev/null
"""
    ok, _ = ssh_run_script(script)
    return True  # stop/reset-failed devuelven != 0 si la unidad ya no existe, no es un fallo real


class PanelEstado:
    def __init__(self, root):
        self.root = root
        self.root.title("Il volo del mattino — estado y control")
        self.root.geometry("640x860")
        self.root.configure(bg=COLOR_FONDO)

        self.pid_k16_visto = None
        self.detener_al_finalizar = FLAG_STOP_PENDIENTE.exists()
        self._consultando_k16 = False
        self.reanudar_unidad = None  # nombre de la unidad systemd-run pendiente, si hay una

        self._estilo()
        self._construir_ui()
        self.actualizar()

    def _estilo(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Fondo.TFrame", background=COLOR_FONDO)
        style.configure("Tarjeta.TFrame", background=COLOR_TARJETA)
        style.configure(
            "Titulo.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO,
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "Info.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO_SUAVE,
            font=("Segoe UI", 9), wraplength=580, justify="left",
        )
        style.configure(
            "Estado.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO,
            font=("Segoe UI", 13, "bold"),
        )
        style.configure(
            "Nota.TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO_SUAVE,
            font=("Segoe UI", 8), wraplength=600, justify="center",
        )
        style.configure(
            "Aviso.TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO_SUAVE,
            font=("Segoe UI", 9), wraplength=600, justify="left",
        )
        for nombre, color in (
            ("Rojo.TButton", COLOR_ROJO), ("Naranja.TButton", COLOR_NARANJA),
            ("Verde.TButton", COLOR_VERDE),
        ):
            style.configure(
                nombre, background=color, foreground="white",
                font=("Segoe UI", 9, "bold"), padding=8, borderwidth=0,
            )
            style.map(nombre, background=[("active", color)])

    def _tarjeta(self, cont, titulo):
        tarjeta = ttk.Frame(cont, style="Tarjeta.TFrame", padding=14)
        tarjeta.pack(fill="x", pady=(0, 10))
        ttk.Label(tarjeta, text=titulo, style="Titulo.TLabel").pack(anchor="w")
        return tarjeta

    def _construir_ui(self):
        cont = ttk.Frame(self.root, style="Fondo.TFrame", padding=16)
        cont.pack(fill="both", expand=True)

        # Le 3 tarjetas centrales siguen el orden real del pipeline: primero se
        # transcribe (K16), luego se identifica (OMV), luego HP14 sincroniza
        # el resultado con GitHub. Sustituyen a las 2 tarjetas de tareas
        # Windows deshabilitadas desde 2026-07-18 (clasificacion se mudo' a
        # OMV) que antes ocupaban este lugar y ya no reportaban nada util.
        t1 = self._tarjeta(cont, "1. Transcripción (K16, en vivo vía SSH)")
        self.lbl_k16_estado = ttk.Label(t1, text="Consultando K16...", style="Estado.TLabel")
        self.lbl_k16_estado.pack(anchor="w", pady=(6, 0))
        self.lbl_k16 = ttk.Label(t1, text="", style="Info.TLabel")
        self.lbl_k16.pack(anchor="w", pady=(4, 0))

        t2 = self._tarjeta(cont, "2. Identificación (OMV, Groq/Cerebras/Gemini)")
        self.lbl_clas = ttk.Label(t2, text="Cargando...", style="Info.TLabel")
        self.lbl_clas.pack(anchor="w", pady=(6, 0))

        t3 = self._tarjeta(cont, "3. Commit/Push (HP14 → GitHub)")
        self.lbl_push = ttk.Label(t3, text="Cargando...", style="Info.TLabel")
        self.lbl_push.pack(anchor="w", pady=(6, 0))

        t5 = self._tarjeta(cont, "Control remoto K16")
        self.lbl_control_estado = ttk.Label(t5, text="", style="Info.TLabel")
        self.lbl_control_estado.pack(anchor="w", pady=(0, 8))

        frame_botones = ttk.Frame(t5, style="Tarjeta.TFrame")
        frame_botones.pack(fill="x")
        frame_botones.columnconfigure((0, 1), weight=1)

        self.btn_ahora = ttk.Button(
            frame_botones, text="Detener AHORA", style="Rojo.TButton",
            command=self.detener_ahora,
        )
        self.btn_ahora.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 4))

        self.btn_proximo = ttk.Button(
            frame_botones, text="Detener al finalizar", style="Naranja.TButton",
            command=self.toggle_detener_proximo,
        )
        self.btn_proximo.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 4))

        self.btn_reanudar = ttk.Button(
            t5, text=f"Reanudar (en {RETRASO_REANUDAR_MIN} min)", style="Verde.TButton",
            command=self.reanudar,
        )
        self.btn_reanudar.pack(fill="x", pady=(4, 0))

        self.lbl_aviso = ttk.Label(t5, text="", style="Aviso.TLabel")
        self.lbl_aviso.pack(fill="x", pady=(8, 0))

        ttk.Label(
            cont,
            text=(
                "Tarjetas 2-3: solo lectura. Tarjeta 1 y control remoto: en vivo vía SSH "
                "al K16 (eddy@192.168.8.132). Sin conexión al K16, lo indican."
            ),
            style="Nota.TLabel",
        ).pack(pady=(4, 0))

    # -- tarjetas 1-3 (igual que antes) -----------------------------------

    def _actualizar_clasificacion(self):
        estado = leer_estado_clasificacion()
        if estado:
            color = COLOR_VERDE if estado.get("resultado") == "ok" else COLOR_ROJO
            self.lbl_clas.config(
                text=(
                    f"Última ejecución: {estado.get('ultima_ejecucion', '?')}\n"
                    f"Resultado: {estado.get('resultado', '?')}\n"
                    f"Archivos clasificados: {estado.get('archivos_clasificados', '?')}\n"
                    f"{estado.get('mensaje', '')}"
                ),
                foreground=color,
            )
        else:
            self.lbl_clas.config(text="Sin datos todavía.", foreground=COLOR_TEXTO_SUAVE)

    def _actualizar_push(self):
        info = leer_tarea_programada(TAREA_SYNC)
        ultima_linea = leer_ultimo_push()

        if ultima_linea and "PUSH OK" in ultima_linea:
            color, resumen = COLOR_VERDE, "✓ Último giro: pushed correctamente"
        elif ultima_linea and "Nessuna modifica" in ultima_linea:
            color, resumen = COLOR_VERDE, "✓ Último giro: nada que sincronizar"
        elif ultima_linea and "ERRORE" in ultima_linea:
            color, resumen = COLOR_ROJO, "✗ Último giro: fallido, revisar detalle"
        else:
            color, resumen = COLOR_TEXTO_SUAVE, "Sin datos del log todavía"

        partes = [resumen]
        if ultima_linea:
            partes.append(ultima_linea)
        if info:
            ultima, resultado, proxima = info
            partes.append(f"Tarea programada — última: {ultima}, código: {resultado}, próxima: {proxima}")
        else:
            partes.append("No se pudo leer la tarea programada.")

        self.lbl_push.config(text="\n".join(partes), foreground=color)

    # -- tarjetas 4-5 (K16 en vivo, en un hilo aparte) ---------------------

    def actualizar(self):
        self._actualizar_clasificacion()
        self._actualizar_push()
        if not self._consultando_k16:
            self._consultando_k16 = True
            threading.Thread(target=self._consultar_k16_en_hilo, daemon=True).start()
        self.root.after(INTERVALO_MS, self.actualizar)

    def _consultar_k16_en_hilo(self):
        estado, error = leer_estado_k16()
        self.root.after(0, lambda: self._aplicar_estado_k16(estado, error))

    def _aplicar_estado_k16(self, estado, error):
        self._consultando_k16 = False

        if estado is None:
            self.lbl_k16_estado.config(text="⚠ K16 no accesible", foreground=COLOR_NARANJA)
            self.lbl_k16.config(text=f"Detalle: {error}", foreground=COLOR_NARANJA)
            self.lbl_control_estado.config(
                text="Sin conexión al K16 — los botones no tendrán efecto.",
                foreground=COLOR_NARANJA,
            )
            return

        pid_actual = estado["pid"]

        partes = []
        if pid_actual:
            episodio = estado["episodio"] or "?"
            self.lbl_k16_estado.config(text="● Transcribiendo", foreground=COLOR_VERDE)
            partes.append(f"Episodio: {episodio}")
            etime_s = estado.get("etime_s")
            if etime_s is not None:
                transcurrido_min = etime_s / 60
                restante_min = max(0, DURACION_MEDIA_MIN - transcurrido_min)
                partes.append(f"Empezado hace: {transcurrido_min:.0f} min")
                partes.append(
                    f"Estimado restante: ~{restante_min:.0f} min (media {DURACION_MEDIA_MIN} min)"
                )
        else:
            self.lbl_k16_estado.config(text="○ Sin transcripción activa", foreground=COLOR_TEXTO_SUAVE)

        if estado["temp_c"]:
            try:
                temp = float(estado["temp_c"])
                partes.append(f"Temperatura CPU: {temp:.0f}°C")
            except ValueError:
                partes.append(f"Temperatura CPU: {estado['temp_c']}")
        else:
            partes.append("Temperatura: sin datos")

        wd = estado["watchdog"]
        partes.append(f"Watchdog NAS: {wd or 'desconocido'}")

        self.lbl_k16.config(text="\n".join(partes), foreground=COLOR_TEXTO_SUAVE)

        # "Detener al finalizar": si el flag esta activo y el PID cambio
        # (episodio nuevo empezo, o el anterior murio y aparecio otro), actuar YA.
        if self.detener_al_finalizar and pid_actual and pid_actual != self.pid_k16_visto and self.pid_k16_visto is not None:
            self._ejecutar_detener_ahora(origen="detección de episodio nuevo")
            self.detener_al_finalizar = False
            FLAG_STOP_PENDIENTE.unlink(missing_ok=True)
            self._sincronizar_boton_proximo()

        self.pid_k16_visto = pid_actual
        self.lbl_control_estado.config(
            text="Conectado al K16.", foreground=COLOR_TEXTO_SUAVE,
        )

    # -- botones de control -------------------------------------------------

    def _hora(self):
        return datetime.now().strftime("%H:%M:%S")

    def detener_ahora(self):
        self._ejecutar_detener_ahora(origen="botón Detener AHORA")

    def _ejecutar_detener_ahora(self, origen):
        self.lbl_aviso.config(text=f"Enviando orden de parada ({origen})...", foreground=COLOR_TEXTO_SUAVE)
        self.root.update_idletasks()

        # Si habia una reanudacion programada pendiente, cancelarla tambien --
        # si no, se reactivaria sola mas tarde sin que este "Detener AHORA"
        # sirviera de nada a medio plazo.
        unidad_pendiente = self.reanudar_unidad
        if unidad_pendiente:
            self.reanudar_unidad = None
            self.btn_reanudar.config(
                text=f"Reanudar (en {RETRASO_REANUDAR_MIN} min)", style="Verde.TButton",
            )

        def trabajo():
            if unidad_pendiente:
                k16_cancelar_reanudar_programado(unidad_pendiente)
            detenido, restante = k16_detener_ahora()
            self.root.after(0, lambda: self._reportar_detener_ahora(detenido, restante))

        threading.Thread(target=trabajo, daemon=True).start()

    def _reportar_detener_ahora(self, detenido, restante):
        if detenido:
            self.lbl_aviso.config(
                text=f"✓ Detenido y verificado a las {self._hora()}. Watchdog NAS pausado en el K16.",
                foreground=COLOR_VERDE,
            )
        else:
            self.lbl_aviso.config(
                text=f"✗ Se envió la orden a las {self._hora()} pero no se pudo verificar la parada "
                     f"(K16 inaccesible o el proceso sigue activo): {restante}",
                foreground=COLOR_ROJO,
            )

    def toggle_detener_proximo(self):
        self.detener_al_finalizar = not self.detener_al_finalizar
        if self.detener_al_finalizar:
            FLAG_STOP_PENDIENTE.touch()
        else:
            FLAG_STOP_PENDIENTE.unlink(missing_ok=True)
        self._sincronizar_boton_proximo()
        if self.detener_al_finalizar:
            self.lbl_aviso.config(
                text=f"✓ Programado a las {self._hora()}: se detendrá en cuanto el K16 empiece "
                     f"un episodio nuevo (chequeo cada {INTERVALO_MS // 1000}s).",
                foreground=COLOR_NARANJA,
            )
        else:
            self.lbl_aviso.config(
                text=f"✓ Cancelado a las {self._hora()}: la parada programada ya no está activa.",
                foreground=COLOR_TEXTO_SUAVE,
            )

    def _sincronizar_boton_proximo(self):
        if self.detener_al_finalizar:
            self.btn_proximo.config(text="✕ Cancelar parada programada", style="Rojo.TButton")
        else:
            self.btn_proximo.config(text="Detener al finalizar", style="Naranja.TButton")

    def reanudar(self):
        if self.reanudar_unidad:
            # ya hay una reanudacion programada -> este clic la cancela (toggle)
            unidad = self.reanudar_unidad
            self.lbl_aviso.config(text="Cancelando reanudación programada...", foreground=COLOR_TEXTO_SUAVE)
            self.root.update_idletasks()

            def trabajo_cancelar():
                k16_cancelar_reanudar_programado(unidad)
                self.root.after(0, self._reportar_cancelar_reanudar)

            threading.Thread(target=trabajo_cancelar, daemon=True).start()
            return

        self.lbl_aviso.config(
            text=f"Programando reanudación en {RETRASO_REANUDAR_MIN} min...",
            foreground=COLOR_TEXTO_SUAVE,
        )
        self.root.update_idletasks()

        def trabajo():
            ok, unidad, salida = k16_reanudar()
            self.root.after(0, lambda: self._reportar_reanudar(ok, unidad, salida))

        threading.Thread(target=trabajo, daemon=True).start()

    def _reportar_reanudar(self, ok, unidad, salida):
        if ok:
            self.reanudar_unidad = unidad
            self.btn_reanudar.config(text="✕ Cancelar reanudación programada", style="Rojo.TButton")
            self.lbl_aviso.config(
                text=f"✓ Reanudación programada a las {self._hora()}: el watchdog NAS (y con él la "
                     f"transcripción) se reactivará en ~{RETRASO_REANUDAR_MIN} min, no de inmediato "
                     f"(unidad: {unidad}).",
                foreground=COLOR_VERDE,
            )
        else:
            self.lbl_aviso.config(
                text=f"✗ No se pudo programar la reanudación a las {self._hora()} "
                     f"(K16 inaccesible): {salida}",
                foreground=COLOR_ROJO,
            )

    def _reportar_cancelar_reanudar(self):
        self.reanudar_unidad = None
        self.btn_reanudar.config(
            text=f"Reanudar (en {RETRASO_REANUDAR_MIN} min)", style="Verde.TButton",
        )
        self.lbl_aviso.config(
            text=f"✓ Reanudación programada cancelada a las {self._hora()}. El watchdog sigue parado.",
            foreground=COLOR_TEXTO_SUAVE,
        )


if __name__ == "__main__":
    root = tk.Tk()
    PanelEstado(root)
    root.mainloop()
