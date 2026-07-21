# Panel de control gráfico para la transcripción del K16. Se lanza solo al
# iniciar sesión (autostart) y queda vigilando en segundo plano: cuando detecta
# que empieza un episodio NUEVO, muestra su propia ventana automáticamente
# (el usuario no tiene que abrir nada). Muestra episodio actual, hora de
# inicio, tiempo transcurrido y estimado restante, y dos botones:
#   - "Detener AHORA": mata el proceso en curso y pausa el watchdog (no se
#     reanuda solo hasta pulsar "Reanudar"). Verifica el resultado real tras
#     actuar (no solo asume que funcionó).
#   - "Detener al finalizar este": deja terminar el episodio actual y para
#     ANTES de que empiece el siguiente. A diferencia del mecanismo viejo
#     (STOP_BATCH_AFTER_EPISODE.flag + check cada 15 min, que casi nunca
#     coincidía con la ventana de unos segundos entre episodios), este panel
#     vigila el proceso cada 5s desde dentro, así que actúa casi al instante.
#     Pulsarlo de nuevo CANCELA la parada programada (toggle).
#
# Cada acción muestra una confirmación explícita con hora exacta y si el
# resultado se verificó correctamente o no, en vez de asumir que el clic
# funcionó.
#
# Uso: se lanza automáticamente via ~/.config/autostart/panel_control.desktop
# (creado por install_panel.sh). También se puede lanzar a mano:
#   python3 scripts/linux/panel_control.py

import json
import os
import re
import subprocess
import sys
import time
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import psutil

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from dati_root import dati_root, logs_root  # noqa: E402
from panel_comun import (  # noqa: E402
    COLOR_AZUL, COLOR_FONDO, COLOR_NARANJA, COLOR_ROJO, COLOR_TARJETA,
    COLOR_TEXTO, COLOR_TEXTO_SUAVE, COLOR_VERDE, contar_estado_classificazione,
    contar_estado_classificazione_episodio, contar_progreso_total, formatear_fecha,
    leer_json_estado,
)
sys.path.insert(0, str(REPO / "scripts" / "linux"))
from kill_coordinado import matar_trascrizione  # noqa: E402

CSV_TERMICO = REPO / "logs" / "trascrizioni_log_termico.csv"
ESTADO_CLASIFICACION = logs_root(REPO) / "estado_clasificacion.json"
# Scritto da scripts/sync_snapshot_data.ps1 (HP14) sullo stesso share OMV di
# ESTADO_CLASIFICACION: nessuna connessione diretta HP14->K16, solo il file
# condiviso — stesso pattern gia' usato per la classificazione.
ESTADO_PUSH = logs_root(REPO) / "estado_push.json"
FRAMMENTI_DIR = dati_root(REPO) / "frammenti"
# Stesso mount point di default usato da avvia_trascrizione_sicura.sh/watchdog_nas.sh,
# ma rispettando ILVOLO_AUDIO_ROOT se impostata: prima era hardcoded qui soltanto,
# quindi un cambio di mount point rompeva in silenzio solo il progresso nel pannello.
AUDIO_ROOT = Path(os.environ.get("ILVOLO_AUDIO_ROOT", "/mnt/ilvolo-audio-backup"))
FLAG_STOP_PENDIENTE = REPO / "data" / "panel_stop_pendiente.flag"
CONSOLA_BATCH = REPO / "logs" / "consola_batch.log"
RE_PROGRESO = re.compile(r"^\[(\d+)/(\d+)\]")
DURACION_MEDIA_MIN = 55  # media observada: 44-51 min, con margen de seguridad
INTERVALO_CHEQUEO_MS = 5000
RETRASO_REANUDAR_MIN = 30  # ver nota en reanudar() -- misma cifra que panel_estado_hp14.py


def hora() -> str:
    return datetime.now().strftime("%H:%M:%S")


def buscar_whisperx():
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "whisperx" in cmdline and "-m whisperx" in cmdline:
            nombre = None
            for token in p.info["cmdline"]:
                if token.endswith(".mp3"):
                    nombre = Path(token).stem
            return p, nombre
    return None, None


def leer_temperatura():
    """Devuelve (temperatura_c, segundos_desde_ultima_lectura) o (None, None)
    si el CSV no existe o esta vacio."""
    if not CSV_TERMICO.exists():
        return None, None
    try:
        ultima = CSV_TERMICO.read_text(encoding="utf-8").strip().splitlines()[-1]
        campos = ultima.split(",")
        if len(campos) < 2:
            return None, None
        ts = datetime.fromisoformat(campos[0])
        temp = float(campos[1])
        antiguedad = (datetime.now() - ts).total_seconds()
        return temp, antiguedad
    except (ValueError, IndexError):
        return None, None


def leer_estado_clasificacion():
    return leer_json_estado(ESTADO_CLASIFICACION)


def leer_estado_push():
    return leer_json_estado(ESTADO_PUSH)


def leer_progreso_batch():
    """Devuelve (indice, total) del ultimo episodio en curso segun el log
    de consola (linea '[N/M] [fecha] archivo.mp3'), o (None, None) si no
    hay ninguna linea de progreso reciente."""
    if not CONSOLA_BATCH.exists():
        return None, None
    try:
        lineas = CONSOLA_BATCH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None, None
    for linea in reversed(lineas):
        m = RE_PROGRESO.match(linea)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def matar_transcripcion(motivo="pannello K16"):
    detenuto, _ = matar_trascrizione(origine="pannello K16", motivo=motivo)
    return detenuto


def systemctl_user(accion, unidad) -> bool:
    r = subprocess.run(["systemctl", "--user", accion, unidad], check=False)
    return r.returncode == 0


def programar_reanudacion(retraso_min=RETRASO_REANUDAR_MIN) -> tuple[bool, str]:
    """NO reactiva el timer al instante. `ilvolo-watchdog-nas.timer` tiene
    Persistent=true: al reactivarlo con `systemctl start` tras haber estado
    parado, systemd lo considera un intervalo "perdido" y dispara una
    ejecucion de catch-up INMEDIATA -- eso relanzo' el batch sin aviso en un
    incidente real. Se programa la reactivacion con `systemd-run --on-active`
    (unidad transient con nombre unico) en vez de systemctl_user("start", ...)
    directo, dando margen real antes de que vuelva a arrancar la
    transcripcion. Stesso meccanismo gia' usato da panel_estado_hp14.py
    (via SSH) -- qui e' locale, K16 lancia systemd-run su se stesso."""
    unidad = f"ilvolo-watchdog-nas-resume-{int(time.time())}"
    r = subprocess.run(
        ["systemd-run", "--user", f"--unit={unidad}", f"--on-active={retraso_min}min",
         "bash", "-c", "systemctl --user start ilvolo-watchdog-nas.timer"],
        check=False,
    )
    return r.returncode == 0, unidad


def cancelar_reanudacion_programada(unidad) -> None:
    if not unidad:
        return
    subprocess.run(["systemctl", "--user", "stop", f"{unidad}.timer"], check=False)
    subprocess.run(["systemctl", "--user", "reset-failed", f"{unidad}.timer", f"{unidad}.service"], check=False)


class Panel:
    def __init__(self, root):
        self.root = root
        self.root.title("Il volo del mattino — control")
        # Offset esplicito (non solo dimensione): senza, il window manager sotto
        # RDP condiviso posiziona la finestra a sinistra, dietro la barra dock
        # verticale di GNOME — va spostata a mano ogni volta. +160 la porta
        # oltre la dock in tutte le risoluzioni viste finora.
        self.root.geometry("620x800+160+60")
        self.root.minsize(560, 700)
        self.root.configure(bg=COLOR_FONDO)
        self.root.attributes("-topmost", True)

        # Registrar el proceso YA en marcha (si existe) como conocido antes de
        # arrancar el bucle de actualizar(): sin esto, un reinicio del panel
        # mientras whisperx esta corriendo hace que actualizar() lo confunda
        # con un episodio "recien empezado" (pid_actual partia de None) y, si
        # habia una parada programada pendiente, lo mataba al instante en vez
        # de esperar a que terminara. Paso de verdad el 18/07: elimino' 4h de
        # trabajo de diarizacion ya casi terminada.
        p_inicial, nombre_inicial = buscar_whisperx()
        self.pid_actual = p_inicial.pid if p_inicial else None
        self.episodio_actual = nombre_inicial
        self.inicio_actual = p_inicial.create_time() if p_inicial else None
        # Persistido en disco: si el panel se cae o se reinicia (crash, reboot,
        # actualizacion) mientras hay una parada programada pendiente, no se
        # pierde en silencio - se restaura aqui. Motivo: paso de verdad el
        # 17/07, el panel se reinicio' solo entre el clic del usuario y el
        # fin del episodio y la orden de parar desaparecio' sin avisar.
        self.detener_al_finalizar = FLAG_STOP_PENDIENTE.exists()
        self.reanudar_unidad = None  # nombre de la unidad systemd-run pendiente, si hay una

        self._estilo()
        self._construir_ui()
        self._sincronizar_boton_proximo()
        if self.detener_al_finalizar:
            self.lbl_aviso.config(
                text=f"↺ Parada programada restaurada al reiniciar el panel "
                     f"(pendiente desde antes de las {hora()}).",
                foreground=COLOR_NARANJA,
            )
        self.actualizar()

    def _estilo(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Fondo.TFrame", background=COLOR_FONDO)
        style.configure("Tarjeta.TFrame", background=COLOR_TARJETA)
        style.configure(
            "Titulo.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO,
            font=("Ubuntu", 19, "bold"),
        )
        style.configure(
            "Info.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO_SUAVE,
            font=("Ubuntu", 14), wraplength=530, justify="left",
        )
        style.configure(
            "Aviso.TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO_SUAVE,
            font=("Ubuntu", 12), wraplength=560, justify="center",
        )
        style.configure(
            "Banner.TLabel", background=COLOR_NARANJA, foreground="white",
            font=("Ubuntu", 13, "bold"), padding=10,
        )
        for nombre, color in (
            ("Rojo.TButton", COLOR_ROJO), ("Naranja.TButton", COLOR_NARANJA),
            ("Verde.TButton", COLOR_VERDE), ("Azul.TButton", COLOR_AZUL),
        ):
            style.configure(
                nombre, background=color, foreground="white",
                font=("Ubuntu", 13, "bold"), padding=14, borderwidth=0,
            )
            style.map(nombre, background=[("active", color)])

    def _construir_ui(self):
        cont = ttk.Frame(self.root, style="Fondo.TFrame", padding=16)
        cont.pack(fill="both", expand=True)

        tarjeta = ttk.Frame(cont, style="Tarjeta.TFrame", padding=18)
        tarjeta.pack(fill="x")

        ttk.Label(tarjeta, text="1. Transcripción", style="Info.TLabel").pack(anchor="w")
        self.lbl_estado = ttk.Label(tarjeta, text="Comprobando...", style="Titulo.TLabel")
        self.lbl_estado.pack(anchor="w")

        self.lbl_episodio = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_episodio.pack(anchor="w", pady=(8, 0))

        self.lbl_tiempo = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_tiempo.pack(anchor="w")

        self.lbl_restante = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_restante.pack(anchor="w")

        self.lbl_temp = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_temp.pack(anchor="w", pady=(4, 0))

        self.lbl_progreso_total = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_progreso_total.pack(anchor="w", pady=(4, 0))

        # Frammenti DE ESTA puntata en concreto -- separado del total
        # acumulado (linea siguiente), que antes se mostraban mezclados en
        # una sola linea sin distinguir "de este episodio" vs "de siempre".
        self.lbl_frammenti_episodio = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_frammenti_episodio.pack(anchor="w", pady=(4, 0))

        self.lbl_classificazione_stats = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_classificazione_stats.pack(anchor="w", pady=(4, 0))

        # Tarjeta separada, solo lectura: estado de la clasificacion. Desde
        # 2026-07-18 corre en OMV (cron diario), ya no en HP14 — se lee del
        # mismo share (logs/estado_clasificacion.json via ILVOLO_LOGS_DIR),
        # no via git ni conexion directa entre maquinas.
        tarjeta_clas = ttk.Frame(cont, style="Tarjeta.TFrame", padding=18)
        tarjeta_clas.pack(fill="x", pady=(10, 0))
        ttk.Label(
            tarjeta_clas, text="2. Identificación (OMV, Groq/Cerebras/Gemini)", style="Titulo.TLabel"
        ).pack(anchor="w")
        self.lbl_clasificacion = ttk.Label(tarjeta_clas, text="", style="Info.TLabel")
        self.lbl_clasificacion.pack(anchor="w", pady=(6, 0))

        # Tarjeta separada, solo lectura: estado dell'ultimo giro di
        # sync_snapshot_data.ps1 su HP14. Legge estado_push.json dallo stesso
        # share OMV (vedi ESTADO_PUSH sopra), nessuna connessione diretta a HP14.
        tarjeta_push = ttk.Frame(cont, style="Tarjeta.TFrame", padding=18)
        tarjeta_push.pack(fill="x", pady=(10, 0))
        ttk.Label(
            tarjeta_push, text="3. Commit/Push (OMV → GitHub)", style="Titulo.TLabel"
        ).pack(anchor="w")
        self.lbl_push = ttk.Label(tarjeta_push, text="", style="Info.TLabel")
        self.lbl_push.pack(anchor="w", pady=(6, 0))

        self.banner_programada = ttk.Label(
            cont, text="⏸ Parada programada: se detendrá al terminar este episodio",
            style="Banner.TLabel", anchor="center",
        )
        # se muestra/oculta con pack/pack_forget, no siempre visible

        frame_botones = ttk.Frame(cont, style="Fondo.TFrame")
        frame_botones.pack(fill="x", pady=(16, 8))
        frame_botones.columnconfigure((0, 1), weight=1)

        self.btn_ahora = ttk.Button(
            frame_botones, text="Detener AHORA", style="Rojo.TButton",
            command=self.detener_ahora,
        )
        self.btn_ahora.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_proximo = ttk.Button(
            frame_botones, text="Detener al finalizar", style="Naranja.TButton",
            command=self.toggle_detener_proximo,
        )
        self.btn_proximo.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.btn_reanudar = ttk.Button(
            cont, text=f"Reanudar (en {RETRASO_REANUDAR_MIN} min)", style="Verde.TButton",
            command=self.reanudar,
        )
        self.btn_reanudar.pack(fill="x", pady=(0, 8))

        self.lbl_aviso = ttk.Label(cont, text="", style="Aviso.TLabel")
        self.lbl_aviso.pack(fill="x")

    def _mostrar_banner_programada(self, visible: bool):
        if visible:
            self.banner_programada.pack(fill="x", pady=(0, 4))
        else:
            self.banner_programada.pack_forget()

    def detener_ahora(self):
        matar_transcripcion(motivo="bottone 'Detener AHORA'")
        systemctl_user("stop", "ilvolo-watchdog-nas.timer")
        self.detener_al_finalizar = False
        FLAG_STOP_PENDIENTE.unlink(missing_ok=True)
        self._sincronizar_boton_proximo()

        # Verificar de verdad, no solo asumir que el clic funciono'
        time.sleep(1)
        p, _ = buscar_whisperx()
        if p is None:
            self.lbl_aviso.config(
                text=f"✓ Detenido y verificado a las {hora()}. Watchdog pausado.",
                foreground=COLOR_VERDE,
            )
        else:
            self.lbl_aviso.config(
                text=f"✗ Se envió la orden a las {hora()} pero el proceso PID {p.pid} "
                     f"sigue activo — reintenta o avisa.",
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
                text=f"✓ Programado a las {hora()}: se detendrá en cuanto termine "
                     f"el episodio actual.",
                foreground=COLOR_NARANJA,
            )
        else:
            self.lbl_aviso.config(
                text=f"✓ Cancelado a las {hora()}: la parada programada ya no está activa.",
                foreground=COLOR_TEXTO_SUAVE,
            )

    def _sincronizar_boton_proximo(self):
        """El botón cambia de texto/color según el estado, para que un segundo
        clic sea inequívoco (antes era un toggle silencioso: un doble clic sin
        querer cancelaba la parada programada sin que se notara)."""
        self._mostrar_banner_programada(self.detener_al_finalizar)
        if self.detener_al_finalizar:
            self.btn_proximo.config(text="✕ Cancelar parada programada", style="Rojo.TButton")
        else:
            self.btn_proximo.config(text="Detener al finalizar", style="Naranja.TButton")

    def reanudar(self):
        if self.reanudar_unidad:
            # ya hay una reanudacion programada -> este clic la cancela (toggle)
            cancelar_reanudacion_programada(self.reanudar_unidad)
            self.reanudar_unidad = None
            self.btn_reanudar.config(text=f"Reanudar (en {RETRASO_REANUDAR_MIN} min)", style="Verde.TButton")
            self.lbl_aviso.config(
                text=f"✓ Reanudación programada cancelada a las {hora()}. El watchdog sigue parado.",
                foreground=COLOR_TEXTO_SUAVE,
            )
            return

        self.detener_al_finalizar = False
        FLAG_STOP_PENDIENTE.unlink(missing_ok=True)
        self._sincronizar_boton_proximo()

        ok, unidad = programar_reanudacion()
        if ok:
            self.reanudar_unidad = unidad
            self.btn_reanudar.config(text="✕ Cancelar reanudación programada", style="Rojo.TButton")
            self.lbl_aviso.config(
                text=f"✓ Reanudación programada a las {hora()}: el watchdog NAS (y con él la "
                     f"transcripción) se reactivará en ~{RETRASO_REANUDAR_MIN} min, no de inmediato "
                     f"(unidad: {unidad}).",
                foreground=COLOR_VERDE,
            )
        else:
            self.lbl_aviso.config(
                text=f"✗ No se pudo programar la reanudación a las {hora()}.",
                foreground=COLOR_ROJO,
            )

    def _actualizar_temperatura(self):
        temp, antiguedad = leer_temperatura()
        if temp is None:
            self.lbl_temp.config(text="Temperatura: sin datos", foreground=COLOR_TEXTO_SUAVE)
            return
        color = COLOR_VERDE if temp < 90 else COLOR_ROJO
        if antiguedad is not None and antiguedad > 180:
            # el logger deberia escribir cada 60s; si lleva mas de 3 min sin
            # actualizar, avisar en vez de mostrar un dato quiza' desfasado
            self.lbl_temp.config(
                text=f"Temperatura: {temp:.0f}°C (⚠ dato de hace {antiguedad/60:.0f} min, "
                     f"el logger puede haberse detenido)",
                foreground=COLOR_NARANJA,
            )
        else:
            self.lbl_temp.config(text=f"Temperatura CPU: {temp:.0f}°C", foreground=color)

    def _actualizar_clasificacion(self):
        estado = leer_estado_clasificacion()
        if not estado:
            self.lbl_clasificacion.config(
                text="Sin datos todavía.", foreground=COLOR_TEXTO_SUAVE,
            )
            return
        color = COLOR_VERDE if estado.get("resultado") == "ok" else COLOR_ROJO
        try:
            total_frammenti = sum(1 for _ in FRAMMENTI_DIR.glob("*.json"))
        except OSError:
            total_frammenti = None
        clasificados = estado.get("archivos_clasificados", "?")
        total_txt = f" de {total_frammenti}" if total_frammenti else ""
        self.lbl_clasificacion.config(
            text=(
                f"Última ejecución: {formatear_fecha(estado.get('ultima_ejecucion'))}\n"
                f"Resultado: {estado.get('resultado', '?')}\n"
                f"Archivos clasificados: {clasificados}{total_txt}"
            ),
            foreground=color,
        )

    def _actualizar_push(self):
        estado = leer_estado_push()
        if not estado:
            self.lbl_push.config(text="Sin datos todavía.", foreground=COLOR_TEXTO_SUAVE)
            return
        color = COLOR_VERDE if estado.get("resultado") == "ok" else COLOR_ROJO
        self.lbl_push.config(
            text=(
                f"Última ejecución: {formatear_fecha(estado.get('ultima_ejecucion'))}\n"
                f"Resultado: {estado.get('resultado', '?')}\n"
                f"{estado.get('mensaje', '')}"
            ),
            foreground=color,
        )

    def _actualizar_progreso_total(self):
        transcritos, total_audio = contar_progreso_total(FRAMMENTI_DIR, AUDIO_ROOT)
        if transcritos is None:
            self.lbl_progreso_total.config(text="")
            return
        if total_audio:
            self.lbl_progreso_total.config(
                text=f"Progreso total: {transcritos} de {total_audio} episodios transcritos"
            )
        else:
            self.lbl_progreso_total.config(
                text=f"Progreso total: {transcritos} episodios transcritos (NAS no montado, sin total)"
            )

        stats_ep = contar_estado_classificazione_episodio(FRAMMENTI_DIR, self.episodio_actual or "")
        if stats_ep:
            self.lbl_frammenti_episodio.config(
                text=(f"Puntata attuale ({stats_ep['data']}): {stats_ep['tot']} frammenti, "
                      f"{stats_ep['classificati']} classificati, {stats_ep['brevi']} scartati")
            )
        elif self.episodio_actual:
            self.lbl_frammenti_episodio.config(
                text="Puntata attuale: ancora senza frammenti generati"
            )
        else:
            self.lbl_frammenti_episodio.config(text="")

        stats = contar_estado_classificazione(FRAMMENTI_DIR)
        if stats:
            classificabili = stats["tot"] - stats["brevi"]
            pct = round(stats["classificati"] / classificabili * 100) if classificabili else 0
            self.lbl_classificazione_stats.config(
                text=(f"Totale accumulato (tutte le puntate): {stats['classificati']} classificati ({pct}%), "
                      f"{stats['da_fare']} in coda, {stats['brevi']} scartati (troppo corti)")
            )
        else:
            self.lbl_classificazione_stats.config(text="")

    def actualizar(self):
        # Ogni sotto-aggiornamento e' isolato nel proprio try/except: un'eccezione
        # (es. JSON corrotto in un frammento) non deve congelare l'intero pannello.
        # In Tkinter un'eccezione in un callback non fa crashare il processo ma
        # interrompe silenziosamente quella invocazione — se non richiamiamo noi
        # stessi self.root.after() nel finally, il loop di refresh si ferma per
        # sempre (bug reale, 2026-07-18: un solo JSON corrotto ha bloccato tutte
        # le schede, transcripcion inclusa, per oltre 24h finche' non e' stato
        # riavviato a mano).
        try:
            self._actualizar_temperatura()
        except Exception:
            traceback.print_exc()
        try:
            self._actualizar_clasificacion()
        except Exception:
            traceback.print_exc()
        try:
            self._actualizar_progreso_total()
        except Exception:
            traceback.print_exc()
        try:
            self._actualizar_push()
        except Exception:
            traceback.print_exc()

        try:
            p, nombre = buscar_whisperx()

            if p is None:
                self.lbl_estado.config(text="○ Sin transcripción activa")
                self.lbl_episodio.config(text="")
                self.lbl_tiempo.config(text="")
                self.lbl_restante.config(text="")
                self.pid_actual = None
            else:
                es_nuevo = p.pid != self.pid_actual
                if es_nuevo and self.detener_al_finalizar:
                    # el episodio anterior termino' y habiamos pedido parar: actuar YA
                    matar_transcripcion(motivo="parada programada (episodio nuevo detectado)")
                    systemctl_user("stop", "ilvolo-watchdog-nas.timer")
                    self.detener_al_finalizar = False
                    FLAG_STOP_PENDIENTE.unlink(missing_ok=True)
                    self._sincronizar_boton_proximo()
                    self.lbl_aviso.config(
                        text=f"✓ Detenido automáticamente a las {hora()}, tras "
                             f"finalizar el episodio anterior.",
                        foreground=COLOR_VERDE,
                    )
                elif es_nuevo:
                    self.pid_actual = p.pid
                    self.episodio_actual = nombre
                    self.inicio_actual = p.create_time()
                    # Nuevo episodio: sacar la ventana al frente para que se vea sin abrirla a mano
                    self.root.deiconify()
                    self.root.geometry("+160+60")  # misma correccion de posicion que en __init__
                    self.root.lift()
                    self.root.attributes("-topmost", True)

                if not (es_nuevo and self.detener_al_finalizar):
                    transcurrido_min = (time.time() - self.inicio_actual) / 60
                    restante_min = max(0, DURACION_MEDIA_MIN - transcurrido_min)

                    idx, total = leer_progreso_batch()
                    progreso = f" ({idx} de {total} en esta carpeta)" if idx and total else ""
                    self.lbl_estado.config(text="● Transcribiendo")
                    self.lbl_episodio.config(text=f"Episodio: {self.episodio_actual}{progreso}")
                    inicio_hhmm = datetime.fromtimestamp(self.inicio_actual).strftime("%d/%m %H:%M")
                    self.lbl_tiempo.config(text=f"Iniziato: {inicio_hhmm} ({transcurrido_min:.0f} min fa)")
                    self.lbl_restante.config(
                        text=f"Estimado restante: ~{restante_min:.0f} min (media {DURACION_MEDIA_MIN} min)"
                    )
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(INTERVALO_CHEQUEO_MS, self.actualizar)


if __name__ == "__main__":
    root = tk.Tk()
    Panel(root)
    root.mainloop()
