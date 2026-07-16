# Panel de control gráfico para la transcripción del K16. Se lanza solo al
# iniciar sesión (autostart) y queda vigilando en segundo plano: cuando detecta
# que empieza un episodio NUEVO, muestra su propia ventana automáticamente
# (el usuario no tiene que abrir nada). Muestra episodio actual, hora de
# inicio, tiempo transcurrido y estimado restante, y dos botones:
#   - "Detener AHORA": mata el proceso en curso y pausa el watchdog (no se
#     reanuda solo hasta pulsar "Reanudar").
#   - "Detener al finalizar este": deja terminar el episodio actual y para
#     ANTES de que empiece el siguiente. A diferencia del mecanismo viejo
#     (STOP_BATCH_AFTER_EPISODE.flag + check cada 15 min, que casi nunca
#     coincidía con la ventana de unos segundos entre episodios), este panel
#     vigila el proceso cada 5s desde dentro, así que actúa casi al instante.
#
# Uso: se lanza automáticamente via ~/.config/autostart/panel_control.desktop
# (creado por install_panel.sh). También se puede lanzar a mano:
#   python3 scripts/linux/panel_control.py

import subprocess
import time
import tkinter as tk
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parent.parent.parent
DURACION_MEDIA_MIN = 55  # media observada: 44-51 min, con margen de seguridad
INTERVALO_CHEQUEO_MS = 5000


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


def systemctl_user(accion, unidad):
    subprocess.run(["systemctl", "--user", accion, unidad], check=False)


class Panel:
    def __init__(self, root):
        self.root = root
        self.root.title("Il volo del mattino - control transcripción")
        self.root.geometry("420x260")
        self.root.attributes("-topmost", True)

        self.pid_actual = None
        self.episodio_actual = None
        self.inicio_actual = None
        self.detener_al_finalizar = False

        self.lbl_estado = tk.Label(root, text="Comprobando...", font=("Sans", 14, "bold"))
        self.lbl_estado.pack(pady=(15, 5))

        self.lbl_episodio = tk.Label(root, text="", font=("Sans", 11))
        self.lbl_episodio.pack()

        self.lbl_tiempo = tk.Label(root, text="", font=("Sans", 11))
        self.lbl_tiempo.pack()

        self.lbl_restante = tk.Label(root, text="", font=("Sans", 11))
        self.lbl_restante.pack(pady=(0, 15))

        frame_botones = tk.Frame(root)
        frame_botones.pack(pady=5)

        self.btn_ahora = tk.Button(
            frame_botones, text="Detener AHORA", bg="#c0392b", fg="white",
            font=("Sans", 11, "bold"), command=self.detener_ahora, width=16, height=2,
        )
        self.btn_ahora.grid(row=0, column=0, padx=5)

        self.btn_proximo = tk.Button(
            frame_botones, text="Detener al\nfinalizar este", bg="#e67e22", fg="white",
            font=("Sans", 11, "bold"), command=self.marcar_detener_proximo, width=16, height=2,
        )
        self.btn_proximo.grid(row=0, column=1, padx=5)

        self.btn_reanudar = tk.Button(
            root, text="Reanudar", bg="#27ae60", fg="white",
            font=("Sans", 10, "bold"), command=self.reanudar, width=34,
        )
        self.btn_reanudar.pack(pady=(10, 0))

        self.lbl_aviso = tk.Label(root, text="", font=("Sans", 9), fg="#e67e22")
        self.lbl_aviso.pack(pady=(5, 0))

        self.actualizar()

    def detener_ahora(self):
        matar_transcripcion()
        systemctl_user("stop", "ilvolo-watchdog-nas.timer")
        self.detener_al_finalizar = False
        self.lbl_aviso.config(text="Detenido ahora. El watchdog está pausado.")

    def marcar_detener_proximo(self):
        self.detener_al_finalizar = True
        self.lbl_aviso.config(text="Se detendrá en cuanto termine el episodio actual.")

    def reanudar(self):
        systemctl_user("start", "ilvolo-watchdog-nas.timer")
        self.detener_al_finalizar = False
        self.lbl_aviso.config(text="Reanudado.")

    def actualizar(self):
        p, nombre = buscar_whisperx()

        if p is None:
            self.lbl_estado.config(text="Sin transcripción activa", fg="#7f8c8d")
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
                    self.lbl_aviso.config(text="Detenido tras finalizar el episodio anterior.")
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

            self.lbl_estado.config(text="Transcribiendo", fg="#27ae60")
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
