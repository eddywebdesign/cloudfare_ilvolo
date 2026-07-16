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

import subprocess
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import psutil

REPO = Path(__file__).resolve().parent.parent.parent
DURACION_MEDIA_MIN = 55  # media observada: 44-51 min, con margen de seguridad
INTERVALO_CHEQUEO_MS = 5000

# Paleta sobria (fondo oscuro, acentos planos) en vez de los colores saturados
# de antes.
COLOR_FONDO = "#1e2530"
COLOR_TARJETA = "#2a3342"
COLOR_TEXTO = "#e6e9ef"
COLOR_TEXTO_SUAVE = "#9aa5b1"
COLOR_VERDE = "#3ba776"
COLOR_ROJO = "#c0392b"
COLOR_NARANJA = "#c9822a"
COLOR_AZUL = "#3d7fd9"


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


def matar_transcripcion():
    for pattern in ("whisperx", "trascrivi_locale_episodi", "avvia_trascrizione_sicura"):
        subprocess.run(["pkill", "-9", "-f", pattern], check=False)
    subprocess.run(["tmux", "kill-session", "-t", "trascrizione"], check=False)


def systemctl_user(accion, unidad) -> bool:
    r = subprocess.run(["systemctl", "--user", accion, unidad], check=False)
    return r.returncode == 0


def watchdog_activo() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "ilvolo-watchdog-nas.timer"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() == "active"


class Panel:
    def __init__(self, root):
        self.root = root
        self.root.title("Il volo del mattino — control")
        self.root.geometry("440x340")
        self.root.configure(bg=COLOR_FONDO)
        self.root.attributes("-topmost", True)

        self.pid_actual = None
        self.episodio_actual = None
        self.inicio_actual = None
        self.detener_al_finalizar = False

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
            font=("Ubuntu", 15, "bold"),
        )
        style.configure(
            "Info.TLabel", background=COLOR_TARJETA, foreground=COLOR_TEXTO_SUAVE,
            font=("Ubuntu", 10),
        )
        style.configure(
            "Aviso.TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO_SUAVE,
            font=("Ubuntu", 9), wraplength=400, justify="center",
        )
        style.configure(
            "Banner.TLabel", background=COLOR_NARANJA, foreground="white",
            font=("Ubuntu", 10, "bold"), padding=8,
        )
        for nombre, color in (
            ("Rojo.TButton", COLOR_ROJO), ("Naranja.TButton", COLOR_NARANJA),
            ("Verde.TButton", COLOR_VERDE), ("Azul.TButton", COLOR_AZUL),
        ):
            style.configure(
                nombre, background=color, foreground="white",
                font=("Ubuntu", 10, "bold"), padding=10, borderwidth=0,
            )
            style.map(nombre, background=[("active", color)])

    def _construir_ui(self):
        cont = ttk.Frame(self.root, style="Fondo.TFrame", padding=16)
        cont.pack(fill="both", expand=True)

        tarjeta = ttk.Frame(cont, style="Tarjeta.TFrame", padding=18)
        tarjeta.pack(fill="x")

        self.lbl_estado = ttk.Label(tarjeta, text="Comprobando...", style="Titulo.TLabel")
        self.lbl_estado.pack(anchor="w")

        self.lbl_episodio = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_episodio.pack(anchor="w", pady=(8, 0))

        self.lbl_tiempo = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_tiempo.pack(anchor="w")

        self.lbl_restante = ttk.Label(tarjeta, text="", style="Info.TLabel")
        self.lbl_restante.pack(anchor="w")

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
            cont, text="Reanudar", style="Verde.TButton", command=self.reanudar,
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
        matar_transcripcion()
        systemctl_user("stop", "ilvolo-watchdog-nas.timer")
        self.detener_al_finalizar = False
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
        systemctl_user("start", "ilvolo-watchdog-nas.timer")
        self.detener_al_finalizar = False
        self._sincronizar_boton_proximo()

        time.sleep(1)
        if watchdog_activo():
            self.lbl_aviso.config(
                text=f"✓ Reanudado y verificado a las {hora()}.", foreground=COLOR_VERDE,
            )
        else:
            self.lbl_aviso.config(
                text=f"✗ Se envió la orden a las {hora()} pero el watchdog no quedó "
                     f"activo — revisa manualmente.",
                foreground=COLOR_ROJO,
            )

    def actualizar(self):
        p, nombre = buscar_whisperx()

        if p is None:
            self.lbl_estado.config(text="○ Sin transcripción activa")
            self.lbl_episodio.config(text="")
            self.lbl_tiempo.config(text="")
            self.lbl_restante.config(text="")
            self.pid_actual = None
        else:
            es_nuevo = p.pid != self.pid_actual
            if es_nuevo:
                if self.detener_al_finalizar:
                    # el episodio anterior termino' y habiamos pedido parar: actuar YA
                    matar_transcripcion()
                    systemctl_user("stop", "ilvolo-watchdog-nas.timer")
                    self.detener_al_finalizar = False
                    self._sincronizar_boton_proximo()
                    self.lbl_aviso.config(
                        text=f"✓ Detenido automáticamente a las {hora()}, tras "
                             f"finalizar el episodio anterior.",
                        foreground=COLOR_VERDE,
                    )
                    self.root.after(INTERVALO_CHEQUEO_MS, self.actualizar)
                    return

                self.pid_actual = p.pid
                self.episodio_actual = nombre
                self.inicio_actual = p.create_time()
                # Nuevo episodio: sacar la ventana al frente para que se vea sin abrirla a mano
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)

            transcurrido_min = (time.time() - self.inicio_actual) / 60
            restante_min = max(0, DURACION_MEDIA_MIN - transcurrido_min)

            self.lbl_estado.config(text="● Transcribiendo")
            self.lbl_episodio.config(text=f"Episodio: {self.episodio_actual}")
            self.lbl_tiempo.config(text=f"Empezado hace: {transcurrido_min:.0f} min")
            self.lbl_restante.config(
                text=f"Estimado restante: ~{restante_min:.0f} min (media {DURACION_MEDIA_MIN} min)"
            )

        self.root.after(INTERVALO_CHEQUEO_MS, self.actualizar)


if __name__ == "__main__":
    root = tk.Tk()
    Panel(root)
    root.mainloop()
