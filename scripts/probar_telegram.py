"""
Prueba de conexion con Telegram. Manda un mensaje fijo para verificar que
TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID estan bien configurados, sin depender
de que existan licitaciones nuevas. Se dispara a mano desde la pestana
Actions ("Probar Telegram").
"""

import os
import sys
from datetime import datetime

import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en los secrets.")
        sys.exit(1)

    texto = (
        "✅ <b>Prueba de conexión</b>\n"
        f"El bot de alertas DNCP está funcionando correctamente.\n"
        f"Hora de la prueba (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}\n\n"
        "A partir de ahora vas a recibir un mensaje como este cada vez que se "
        "publique una licitación nueva (chequeo cada 2 horas)."
    )

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"},
        timeout=20,
    )

    if resp.status_code != 200:
        print(f"ERROR al enviar (HTTP {resp.status_code}): {resp.text[:300]}")
        print("\nCausas comunes:")
        print("- Token incorrecto o regenerado despues de guardarlo en secrets.")
        print("- Chat ID incorrecto.")
        print("- Nunca le mandaste un mensaje al bot (Telegram no deja que un bot inicie conversacion).")
        sys.exit(1)

    print("Mensaje de prueba enviado correctamente. Revisa tu Telegram.")


if __name__ == "__main__":
    main()
