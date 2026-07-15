# Envio de alertas por email (SMTP Gmail) para el K16 headless, sin nadie mirando
# pantalla/logs en persona. Credenciales NUNCA en git: se leen de
# ~/.config/ilvolo_alert_smtp.conf (formato "CLAVE=valor" por linea), que el
# usuario crea el mismo con permisos 600.
#
# Uso como modulo: from enviar_alerta import enviar_alerta
# Uso standalone (test manual): python3 scripts/linux/enviar_alerta.py

import smtplib
from email.message import EmailMessage
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "ilvolo_alert_smtp.conf"
DESTINATARIO = "eddydl@libero.it"


def _leer_config():
    if not CONFIG_PATH.exists():
        return None
    valores = {}
    for linea in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in linea:
            clave, valor = linea.split("=", 1)
            valores[clave.strip()] = valor.strip()
    if "EMAIL" not in valores or "APP_PASSWORD" not in valores:
        return None
    return valores


def enviar_alerta(asunto: str, cuerpo: str) -> bool:
    """Devuelve True si el email se envio' correctamente, False si no (config
    ausente o fallo SMTP) - nunca lanza excepcion, para no romper el caller
    (check_batch_health.py / sensori_temp.py deben seguir funcionando aunque
    el email falle)."""
    config = _leer_config()
    if not config:
        print(f"enviar_alerta: config ausente en {CONFIG_PATH}, email NO enviado.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[K16 il volo del mattino] {asunto}"
    msg["From"] = config["EMAIL"]
    msg["To"] = DESTINATARIO
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(config["EMAIL"], config["APP_PASSWORD"])
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"enviar_alerta: ERROR al enviar email: {e}")
        return False


if __name__ == "__main__":
    ok = enviar_alerta(
        "Test de alerta",
        "Correo de prueba del sistema de alarmas del K16 (transcripcion il volo del mattino). "
        "Si lo recibes, el envio funciona correctamente.",
    )
    print("Enviado:" if ok else "NO enviado:", ok)
