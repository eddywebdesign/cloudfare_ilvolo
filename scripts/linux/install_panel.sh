#!/usr/bin/env bash
# Instala el panel de control grafico para que se abra solo al iniciar sesion
# (autostart de GNOME) y lo lanza ahora mismo tambien, sin esperar al proximo
# login.
#
# Uso: bash scripts/linux/install_panel.sh

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/panel_control.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Panel control transcripcion
Exec=python3 $REPO/scripts/linux/panel_control.py
X-GNOME-Autostart-enabled=true
EOF

echo "Autostart instalado en $AUTOSTART_DIR/panel_control.desktop"

# Lanzarlo ya, en esta sesion grafica, sin esperar a un reinicio/relogin
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY="wayland-0"
export DISPLAY=":1"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

nohup python3 "$REPO/scripts/linux/panel_control.py" > "$REPO/logs/panel_control.log" 2>&1 < /dev/null &
disown
echo "Panel lanzado ahora, PID $!"
