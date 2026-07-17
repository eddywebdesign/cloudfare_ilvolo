# Panel unico del HP14: estado (clasificacion + tareas Windows) y ahora
# tambien la transcripcion del K16 EN VIVO + control remoto, por SSH por
# clave ya configurado (eddy@192.168.8.130, sin contrasena). Sustituye a la
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
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

REPO = Path(__file__).resolve().parent.parent
ESTADO_PATH = REPO / "data" / "estado_clasificacion.json"
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
    if not ESTADO_PATH.exists():
        return None
    try:
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
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
    # pkill -9 -f 'whisperx' (sin corchete) SI debe matchear cualquier
    # variante real del proceso -- aqui no hay riesgo de auto-matcheo porque
    # pkill mata y sale, no imprime su propia linea de comandos como resultado.
    script = r"""
pkill -9 -f whisperx
pkill -9 -f trascrivi_locale_episodi
tmux kill-session -t trascrizione 2>/dev/null
systemctl --user stop ilvolo-watchdog-nas.timer
sleep 1
pgrep -f '[w]hisperx'
"""
    ssh_run_script(script)
    # Verificacion aparte con el patron anti-autodetectado, para no reportar
    # "sigue vivo" por culpa del propio pgrep de verificacion.
    ok2, restante = ssh_run_script("pgrep -f '[w]hisperx'")
    detenido = not ok2  # ssh_run marca ok=False cuando pgrep no encuentra nada (rc=1)
    return detenido, restante


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
        self.detener_al_finalizar = False
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

        t1 = self._tarjeta(cont, "Clasificación (Groq/Cerebras)")
        self.lbl_clas = ttk.Label(t1, text="Cargando...", style="Info.TLabel")
        self.lbl_clas.pack(anchor="w", pady=(6, 0))

        t2 = self._tarjeta(cont, "Tarea diaria: IlVoloClassificazioneNotturna")
        self.lbl_tarea1 = ttk.Label(t2, text="Cargando...", style="Info.TLabel")
        self.lbl_tarea1.pack(anchor="w", pady=(6, 0))

        t3 = self._tarjeta(cont, "Tarea diaria: IlVoloVerificaSettimanale")
        self.lbl_tarea2 = ttk.Label(t3, text="Cargando...", style="Info.TLabel")
        self.lbl_tarea2.pack(anchor="w", pady=(6, 0))

        t4 = self._tarjeta(cont, "Transcripción K16 (en vivo, vía SSH)")
        self.lbl_k16_estado = ttk.Label(t4, text="Consultando K16...", style="Estado.TLabel")
        self.lbl_k16_estado.pack(anchor="w", pady=(6, 0))
        self.lbl_k16 = ttk.Label(t4, text="", style="Info.TLabel")
        self.lbl_k16.pack(anchor="w", pady=(4, 0))

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
                "Tarjetas 1-3: solo lectura. Tarjetas 4-5: en vivo vía SSH al K16 "
                "(eddy@192.168.8.130). Sin conexión al K16, la tarjeta 4-5 lo indica."
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

    def _actualizar_tareas(self):
        for lbl, nombre in (
            (self.lbl_tarea1, "IlVoloClassificazioneNotturna"),
            (self.lbl_tarea2, "IlVoloVerificaSettimanale"),
        ):
            info = leer_tarea_programada(nombre)
            if info:
                ultima, resultado, proxima = info
                color = COLOR_VERDE if resultado.strip() == "0" else COLOR_ROJO
                lbl.config(
                    text=(
                        f"Última ejecución: {ultima}\n"
                        f"Código resultado: {resultado} {'(éxito)' if resultado.strip() == '0' else '(revisar)'}\n"
                        f"Próxima ejecución: {proxima}"
                    ),
                    foreground=color,
                )
            else:
                lbl.config(text="No se pudo leer la tarea.", foreground=COLOR_TEXTO_SUAVE)

    # -- tarjetas 4-5 (K16 en vivo, en un hilo aparte) ---------------------

    def actualizar(self):
        self._actualizar_clasificacion()
        self._actualizar_tareas()
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
