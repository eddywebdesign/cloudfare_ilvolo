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
import os
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from dati_root import logs_root  # noqa: E402
from panel_comun import (  # noqa: E402
    COLOR_AZUL, COLOR_FONDO, COLOR_NARANJA, COLOR_ROJO, COLOR_TARJETA, COLOR_TEXTO,
    COLOR_TEXTO_SUAVE, COLOR_VERDE, contar_estado_classificazione,
    contar_estado_classificazione_episodio, contar_progreso_total,
    contar_rifatti_config_attuale, formatear_fecha, leer_json_estado,
)

ESTADO_PATH = logs_root(REPO) / "estado_clasificacion.json"
# Fallback esplicito al path di rete noto: se ILVOLO_DATA_DIR non e' visibile
# nel processo corrente (setx non si propaga a sessioni/processi gia' aperti
# finche' non c'e' un logout/login o un riavvio di explorer.exe - capitato
# davvero il 18/07/2026, pannello bloccato su "Sin datos" con dati freschi e
# validi sullo share), non arrenderti al path locale del repo (che non ha mai
# questo file) se il path di rete e' comunque raggiungibile.
ESTADO_PATH_FALLBACK = Path(r"\\192.168.8.80\Media\ilvolodellasera\logs\estado_clasificacion.json")
# Stesso file che K16 legge (ESTADO_PUSH in panel_control.py) -- HP14 lo
# raggiunge via UNC diretto, nessun SSH necessario (e' un file su share, non
# un dato del processo K16). ATTENZIONE: "ilvolo-audio-backup" NON e' piu'
# direttamente sotto Media\ (verificato il 2026-07-19 con Test-Path -- un
# commento vecchio in avvia_trascrizione_sicura.ps1 dava un path superato
# dalla migrazione dati del 17/07), ora vive sotto Media\ilvolodellasera\.
ESTADO_PUSH_PATH = Path(r"\\192.168.8.80\Media\ilvolodellasera\logs\estado_push.json")
FRAMMENTI_DIR_UNC = Path(r"\\192.168.8.80\Media\ilvolodellasera\data\frammenti")
TRASCRIZIONI_DIR_UNC = Path(r"\\192.168.8.80\Media\ilvolodellasera\data\trascrizioni")
AUDIO_ROOT_UNC = Path(r"\\192.168.8.80\Media\ilvolodellasera\ilvolo-audio-backup")
# Persistido en disco local del HP14 (no en el share): mismo motivo que en
# panel_control.py (K16) - si este panel se cierra/crashea con una parada
# programada pendiente, no debe perderse en silencio.
FLAG_STOP_PENDIENTE = REPO / "data" / "panel_hp14_stop_pendiente.flag"
# abrir_panel_estado.bat lancia con pythonw (nessuna console): senza questo,
# le eccezioni catturate in actualizar() sparirebbero nel nulla invece di
# finire da qualche parte leggibile per il debug.
LOG_ERRORES = REPO / "logs" / "panel_estado_hp14_errores.log"
INTERVALO_MS = 15000

K16_HOST = "eddy@192.168.8.130"  # 2026-07-22: K16 spostato fisicamente vicino alla TV, IP cambiato da .132 a .130 (stessa Ethernet, DHCP diverso da quella posizione) -- riverificare se torna alla postazione originale
SSH_TIMEOUT_S = 8
DURACION_MEDIA_MIN_CPU = 55  # misma media usada en scripts/linux/panel_control.py
DURACION_MEDIA_MIN_GPU = 1.7  # RTX 5070 via OCuLink, misurato 2026-07-22: ~1m35-40s/episodio reali
RETRASO_REANUDAR_MIN = 30  # ver nota en k16_reanudar()

# En Windows, subprocess abre una consola visible por cada llamada (ssh.exe,
# powershell.exe) aunque el panel se lance con pythonw -- con el refresco
# cada 15s eso se traduce en ventanas parpadeando sin parar. CREATE_NO_WINDOW
# las suprime.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def leer_estado_clasificacion():
    return leer_json_estado(ESTADO_PATH, ESTADO_PATH_FALLBACK)


def leer_estado_push():
    return leer_json_estado(ESTADO_PUSH_PATH)


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
    si el watchdog NAS esta activo, y el progreso "N de M" dentro de la
    carpeta-ano en curso. Formato de salida:
    'PID|EPISODIO|ETIME_S|TERM_LINE|WATCHDOG|IDX_BATCH|TOTAL_BATCH' (campos
    vacios si no aplica).

    Patron '[w]hisperx' (no 'whisperx' a secas): pgrep -f matchea contra la
    linea de comandos COMPLETA del proceso que lo invoca via ssh, que
    contiene literalmente el texto del patron -- sin el truco del corchete
    pgrep se detecta a si mismo como si fuera una transcripcion en curso
    (mismo gotcha ya documentado con pkill -f en la sesion RDP del K16).

    IDX_BATCH/TOTAL_BATCH: misma logica de leer_progreso_batch() en
    panel_control.py (ultima linea '[N/M] ...' de logs/consola_batch.log),
    reimplementada aqui en bash porque ese archivo es local a K16 -- a
    diferencia de progreso total/clasificacion (ver contar_progreso_total en
    panel_comun.py), no esta en el share de red y no se puede leer via UNC."""
    script = r"""
cd ~/ilvolodelmattino 2>/dev/null || cd ~/Documenti/ilvolodelmattino 2>/dev/null
PID=$(pgrep -f '[w]hisperx' | head -1)
EP=""
ETIME=""
GPU=""
if [ -n "$PID" ]; then
  ARGS=$(ps -p "$PID" -o args=)
  EP=$(echo "$ARGS" | grep -oE '[^ /]*\.mp3' | head -1)
  ETIME=$(ps -p "$PID" -o etimes= | tr -d ' ')
  echo "$ARGS" | grep -q 'device cuda' && GPU="1"
fi
TERM_LINE=$(tail -1 logs/trascrizioni_log_termico.csv 2>/dev/null)
WD=$(systemctl --user is-active ilvolo-watchdog-nas.timer 2>/dev/null)
PROGRESO=$(grep -oE '^\[[0-9]+/[0-9]+\]' logs/consola_batch.log 2>/dev/null | tail -1 | tr -d '[]')
IDX=$(echo "$PROGRESO" | cut -d/ -f1)
TOTAL=$(echo "$PROGRESO" | cut -d/ -f2)
echo "${PID}|${EP}|${ETIME}|${TERM_LINE}|${WD}|${IDX}|${TOTAL}|${GPU}"
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
    idx_batch = campos[5].strip() if len(campos) > 5 else ""
    total_batch = campos[6].strip() if len(campos) > 6 else ""
    gpu = campos[7].strip() if len(campos) > 7 else ""
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
        "idx_batch": int(idx_batch) if idx_batch.isdigit() else None,
        "total_batch": int(total_batch) if total_batch.isdigit() else None,
        "gpu": bool(gpu),
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
        self.episodio_actual = None  # actualizado por _aplicar_estado_k16, leido por _aplicar_progreso
        self.detener_al_finalizar = FLAG_STOP_PENDIENTE.exists()
        self._consultando_k16 = False
        self._consultando_progreso = False
        self.reanudar_unidad = None  # nombre de la unidad systemd-run pendiente, si hay una

        self._estilo()
        self._construir_ui()
        self._tick_reloj()
        self.actualizar()

    def _tick_reloj(self):
        self.lbl_reloj.config(text=f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self.root.after(1000, self._tick_reloj)

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
        style.configure(
            "Link.TLabel", background=COLOR_FONDO, foreground=COLOR_AZUL,
            font=("Segoe UI", 9, "underline"),
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
        # Canvas + scrollbar: con 3 tarjetas numeradas + progreso + control
        # remoto, el contenido ya no entra siempre en los 860px de alto fijo
        # de la ventana -- sin scroll, la parte de abajo (botones) quedaba
        # cortada segun la resolucion.
        canvas = tk.Canvas(self.root, bg=COLOR_FONDO, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        cont = ttk.Frame(canvas, style="Fondo.TFrame", padding=16)
        cont_id = canvas.create_window((0, 0), window=cont, anchor="nw")

        def _actualizar_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _ajustar_ancho(event):
            canvas.itemconfig(cont_id, width=event.width)

        cont.bind("<Configure>", _actualizar_scrollregion)
        canvas.bind("<Configure>", _ajustar_ancho)

        def _rueda_raton(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _rueda_raton)

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
        # Progreso total/clasificacion: calculados via UNC directo al share
        # OMV (no via SSH -- son datos de archivos compartidos, no del
        # proceso K16), misma formulacion exacta que panel_control.py.
        self.lbl_progreso_total = ttk.Label(t1, text="", style="Info.TLabel")
        self.lbl_progreso_total.pack(anchor="w", pady=(4, 0))
        # Reloj en vivo, independiente del ciclo de refresco de 15s: prueba
        # inequivocable de que el panel sigue vivo y no congelado (el bug real
        # del 2026-07-19 en panel_control.py se quedaba con la ultima tarjeta
        # vista para siempre, sin ningun indicio visual de que se habia parado).
        self.lbl_reloj = ttk.Label(t1, text="", style="Info.TLabel")
        self.lbl_reloj.pack(anchor="w", pady=(8, 0))

        t2 = self._tarjeta(cont, "2. Identificación (OMV, Groq/Cerebras/Gemini)")
        self.lbl_clas = ttk.Label(t2, text="Cargando...", style="Info.TLabel")
        self.lbl_clas.pack(anchor="w", pady=(6, 0))
        # Frammenti (2026-07-21): estadisticas de CLASIFICACION, no de
        # transcripcion -- viven aqui, no en la card 1. lbl_clas arriba es un
        # snapshot del ultimo giro del cron; estas dos se recalculan EN VIVO
        # cada refresco, por eso la etiqueta "en vivo ahora mismo".
        ttk.Label(t2, text="Estado en vivo ahora mismo:", style="Info.TLabel").pack(anchor="w", pady=(10, 0))
        self.lbl_frammenti_episodio = ttk.Label(t2, text="", style="Info.TLabel")
        self.lbl_frammenti_episodio.pack(anchor="w", pady=(4, 0))
        self.lbl_classificazione_stats = ttk.Label(t2, text="", style="Info.TLabel")
        self.lbl_classificazione_stats.pack(anchor="w", pady=(4, 0))

        t3 = self._tarjeta(cont, "3. Commit/Push (OMV → GitHub)")
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

        # Visible solo si el archivo existe (hay errores capturados en
        # actualizar() -- ver _log_error). Clic abre el .log con el programa
        # asociado por defecto en Windows, sin necesidad de ir a buscarlo a mano.
        self.lbl_log_errores = ttk.Label(
            cont, text="⚠ Ver log de errores", style="Link.TLabel", cursor="hand2",
        )
        self.lbl_log_errores.bind("<Button-1>", self._abrir_log_errores)
        # no se hace pack() aqui -- _actualizar_link_errores() lo muestra/oculta

    # -- tarjetas 1-3 (igual que antes) -----------------------------------

    def _actualizar_clasificacion(self):
        estado = leer_estado_clasificacion()
        if estado:
            color = COLOR_VERDE if estado.get("resultado") == "ok" else COLOR_ROJO
            self.lbl_clas.config(
                text=(
                    f"Última ejecución: {formatear_fecha(estado.get('ultima_ejecucion'))}\n"
                    f"Resultado: {estado.get('resultado', '?')}\n"
                    f"Archivos clasificados: {estado.get('archivos_clasificados', '?')}\n"
                    f"{estado.get('mensaje', '')}"
                ),
                foreground=color,
            )
        else:
            self.lbl_clas.config(text="Sin datos todavía.", foreground=COLOR_TEXTO_SUAVE)

    def _actualizar_push(self):
        # Desde 2026-07-20: escribe este estado autocommit_dati_omv.sh (OMV,
        # cron cada 20 min), no ya sync_snapshot_data.ps1 (HP14, tarea
        # deshabilitada) -- mismo archivo/mismo path UNC, misma formulacion
        # exacta que panel_control.py (K16). Ya no hay detalle suplementario
        # de tarea programada local: el HP14 no ejecuta nada de este proceso.
        estado = leer_estado_push()
        if estado:
            color = COLOR_VERDE if estado.get("resultado") == "ok" else COLOR_ROJO
            lineas = [
                f"Última ejecución: {formatear_fecha(estado.get('ultima_ejecucion'))}",
                f"Resultado: {estado.get('resultado', '?')}",
                f"{estado.get('mensaje', '')}",
            ]
        else:
            color = COLOR_TEXTO_SUAVE
            lineas = ["Sin datos todavía."]

        self.lbl_push.config(text="\n".join(lineas), foreground=color)

    # -- tarjetas 4-5 (K16 en vivo, en un hilo aparte) ---------------------

    def actualizar(self):
        # Isolato in try/except per ogni sotto-aggiornamento: un'eccezione (es.
        # JSON corrotto) non deve impedire il self.root.after() finale, o il
        # loop si ferma per sempre finche' non si riavvia a mano (stesso bug
        # reale gia' risolto in scripts/linux/panel_control.py il 2026-07-19 —
        # qui mancava lo stesso fix).
        try:
            self._actualizar_clasificacion()
        except Exception:
            self._log_error()
        try:
            self._actualizar_push()
        except Exception:
            self._log_error()
        try:
            if not self._consultando_k16:
                self._consultando_k16 = True
                threading.Thread(target=self._consultar_k16_en_hilo, daemon=True).start()
        except Exception:
            self._log_error()
        try:
            if not self._consultando_progreso:
                self._consultando_progreso = True
                threading.Thread(target=self._consultar_progreso_en_hilo, daemon=True).start()
        except Exception:
            self._log_error()
        self._actualizar_link_errores()
        self.root.after(INTERVALO_MS, self.actualizar)

    def _consultar_progreso_en_hilo(self):
        # En un hilo aparte porque son lecturas de red (UNC) que pueden
        # tardar -- no debe bloquear la UI como el resto del ciclo de 15s.
        try:
            transcritos, total_audio = contar_progreso_total(FRAMMENTI_DIR_UNC, AUDIO_ROOT_UNC)
        except Exception:
            transcritos, total_audio = None, None
        try:
            rifatti = contar_rifatti_config_attuale(TRASCRIZIONI_DIR_UNC)
        except Exception:
            rifatti = None
        try:
            stats = contar_estado_classificazione(FRAMMENTI_DIR_UNC)
        except Exception:
            stats = None
        try:
            stats_ep = contar_estado_classificazione_episodio(FRAMMENTI_DIR_UNC, self.episodio_actual or "")
        except Exception:
            stats_ep = None
        self.root.after(0, lambda: self._aplicar_progreso(transcritos, total_audio, rifatti, stats, stats_ep))

    def _aplicar_progreso(self, transcritos, total_audio, rifatti, stats, stats_ep):
        self._consultando_progreso = False
        rifatti_txt = f" (rifatti con config attuale: {rifatti})" if rifatti is not None else ""
        if transcritos is None:
            self.lbl_progreso_total.config(text="")
        elif total_audio:
            self.lbl_progreso_total.config(
                text=f"Progreso total: {transcritos} de {total_audio} episodios transcritos{rifatti_txt}",
                foreground=COLOR_TEXTO_SUAVE,
            )
        else:
            self.lbl_progreso_total.config(
                text=f"Progreso total: {transcritos} episodios transcritos (share no accesible, sin total){rifatti_txt}",
                foreground=COLOR_TEXTO_SUAVE,
            )

        if stats_ep:
            self.lbl_frammenti_episodio.config(
                text=(f"Puntata attuale ({stats_ep['data']}): {stats_ep['tot']} frammenti, "
                      f"{stats_ep['classificati']} classificati, {stats_ep['brevi']} scartati"),
                foreground=COLOR_TEXTO_SUAVE,
            )
        elif self.episodio_actual:
            self.lbl_frammenti_episodio.config(
                text="Puntata attuale: ancora senza frammenti generati",
                foreground=COLOR_TEXTO_SUAVE,
            )
        else:
            self.lbl_frammenti_episodio.config(text="")

        if stats:
            classificabili = stats["tot"] - stats["brevi"]
            pct = round(stats["classificati"] / classificabili * 100) if classificabili else 0
            self.lbl_classificazione_stats.config(
                text=(f"Totale accumulato (tutte le puntate): {stats['classificati']} classificati ({pct}%), "
                      f"{stats['da_fare']} in coda, {stats['brevi']} scartati (troppo corti)"),
                foreground=COLOR_TEXTO_SUAVE,
            )
        else:
            self.lbl_classificazione_stats.config(text="")

    def _actualizar_link_errores(self):
        if LOG_ERRORES.exists():
            self.lbl_log_errores.pack(pady=(6, 0))
        else:
            self.lbl_log_errores.pack_forget()

    def _abrir_log_errores(self, event=None):
        if LOG_ERRORES.exists():
            os.startfile(LOG_ERRORES)

    def _log_error(self):
        try:
            with open(LOG_ERRORES, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()}\n")
                traceback.print_exc(file=f)
        except OSError:
            pass

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
        self.episodio_actual = estado["episodio"]  # leido en _aplicar_progreso (hilo separado)

        partes = []
        if pid_actual:
            episodio = estado["episodio"] or "?"
            self.lbl_k16_estado.config(text="● Transcribiendo", foreground=COLOR_VERDE)
            idx, total = estado.get("idx_batch"), estado.get("total_batch")
            progreso = f" ({idx} de {total} en esta carpeta)" if idx and total else ""
            partes.append(f"Episodio: {episodio}{progreso}")
            etime_s = estado.get("etime_s")
            if etime_s is not None:
                duracion_media = DURACION_MEDIA_MIN_GPU if estado.get("gpu") else DURACION_MEDIA_MIN_CPU
                transcurrido_min = etime_s / 60
                restante_min = max(0, duracion_media - transcurrido_min)
                # Hora absoluta de inicio, no solo relativa -- para distinguir
                # de un vistazo "en marcha desde hace poco" de "esto lleva
                # parado/colgado desde ayer" (mismo formato que panel_control.py).
                inicio_dt = datetime.now() - timedelta(seconds=etime_s)
                partes.append(
                    f"Iniciado: {inicio_dt.strftime('%d/%m %H:%M')} ({transcurrido_min:.0f} min hace)"
                )
                gpu_label = " [GPU]" if estado.get("gpu") else ""
                partes.append(
                    f"Estimado restante: ~{restante_min:.1f} min (media {duracion_media:.1f} min{gpu_label})"
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
