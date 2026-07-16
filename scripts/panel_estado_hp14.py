# Panel de SOLO LECTURA del estado de la pipeline en el HP14. Sin ningun
# boton de accion (pedido explicito del usuario: no tocar/controlar el
# proceso de clasificacion desde aqui, solo verlo). Muestra:
#   - Ultima ejecucion de clasificacion (data/estado_clasificacion.json)
#   - Estado de las 2 tareas de Windows Task Scheduler (clasificacion +
#     verificacion semanal): ultima ejecucion, resultado, proxima ejecucion.
# Se refresca solo cada 30s.
#
# Uso: python scripts\panel_estado_hp14.py
# (o doble clic en abrir_panel_estado.bat)

import json
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

REPO = Path(__file__).resolve().parent.parent
ESTADO_PATH = REPO / "data" / "estado_clasificacion.json"
INTERVALO_MS = 30000

COLOR_FONDO = "#1e2530"
COLOR_TARJETA = "#2a3342"
COLOR_TEXTO = "#e6e9ef"
COLOR_TEXTO_SUAVE = "#9aa5b1"
COLOR_VERDE = "#3ba776"
COLOR_ROJO = "#c0392b"


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
        )
        salida = r.stdout.strip()
        if not salida or "|" not in salida:
            return None
        ultima, resultado, proxima = salida.split("|")
        return ultima, resultado, proxima
    except (subprocess.TimeoutExpired, OSError):
        return None


class PanelEstado:
    def __init__(self, root):
        self.root = root
        self.root.title("Il volo del mattino — estado (solo lectura)")
        self.root.geometry("460x320")
        self.root.configure(bg=COLOR_FONDO)

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
            font=("Segoe UI", 9),
        )
        style.configure(
            "Nota.TLabel", background=COLOR_FONDO, foreground=COLOR_TEXTO_SUAVE,
            font=("Segoe UI", 8), wraplength=420, justify="center",
        )

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

        ttk.Label(
            cont, text="Solo lectura — este panel no controla ni modifica nada.",
            style="Nota.TLabel",
        ).pack(pady=(4, 0))

    def actualizar(self):
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

        self.root.after(INTERVALO_MS, self.actualizar)


if __name__ == "__main__":
    root = tk.Tk()
    PanelEstado(root)
    root.mainloop()
