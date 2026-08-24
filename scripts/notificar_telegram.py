"""
Chequeo de novedades cada 2 horas + alerta por Telegram.

Corre via .github/workflows/alertas_telegram.yml (cron cada 2hs). Guarda
particionado por anio (ver dncp_core.py). Ademas de ser una clave "nueva",
se exige que fecha_publicacion sea reciente antes de avisar -- evita que
un backfill u otra corrida que agregue muchas claves de una sola vez
dispare una alerta por cada una.
"""

import os
import sys
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_CARACTERES_TELEGRAM = 4000
DIAS_RECIENCIA = int(os.environ.get("ALERTA_DIAS_RECIENCIA", "3"))
LIMITE_NOVEDADES_SIN_RESUMEN = int(os.environ.get("ALERTA_LIMITE_SIN_RESUMEN", "40"))


def enviar_telegram(token: str, chat_id: str, texto: str):
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(url, data={
        "chat_id": chat_id, "text": texto, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Error al enviar a Telegram (HTTP {resp.status_code}): {resp.text[:300]}")


def formatear_bloque(proceso: dict) -> str:
    nombre = proceso.get("nombre_licitacion") or "(sin nombre)"
    convocante = proceso.get("convocante") or "(sin convocante)"
    id_llamado = proceso.get("id_llamado") or "?"
    link = proceso.get("link") or ""
    bloque = (
        f"🆕 <b>Nueva publicación</b>\n"
        f"ID: {id_llamado}\n"
        f"Nombre: {nombre}\n"
        f"Convocante: {convocante}\n"
    )
    if link:
        bloque += f"Link: {link}\n"
    return bloque


def dividir_en_mensajes(bloques: list) -> list:
    mensajes = []
    actual = ""
    for bloque in bloques:
        if len(actual) + len(bloque) > LIMITE_CARACTERES_TELEGRAM:
            mensajes.append(actual)
            actual = ""
        actual += bloque + "\n"
    if actual:
        mensajes.append(actual)
    return mensajes


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    faltantes = [n for n, v in [
        ("DNCP_CONSUMER_KEY", consumer_key), ("DNCP_CONSUMER_SECRET", consumer_secret),
        ("TELEGRAM_BOT_TOKEN", telegram_token), ("TELEGRAM_CHAT_ID", telegram_chat_id),
    ] if not v]
    if faltantes:
        print(f"ERROR: faltan estas variables de entorno: {', '.join(faltantes)}")
        sys.exit(1)

    print("Autenticando contra la API de la DNCP...")
    token = core.obtener_token(consumer_key, consumer_secret)
    print("Token obtenido.")

    almacen = core.AlmacenParticionado()

    fecha_desde = str(date.today() - timedelta(days=1))
    fecha_hasta = str(date.today())
    print(f"Consultando novedades entre {fecha_desde} y {fecha_hasta}...")
    registros = core.buscar_todo(token, fecha_desde, fecha_hasta)

    limite_fecha = str(date.today() - timedelta(days=DIAS_RECIENCIA))
    procesos_nuevos_recientes = []

    for registro in registros:
        if not isinstance(registro, dict):
            continue
        plano = core.normalizar(registro)
        clave, existia = almacen.upsert(plano)
        if clave is None:
            continue
        if not existia and plano.get("fecha_publicacion", "") >= limite_fecha:
            procesos_nuevos_recientes.append(plano)

    almacen.guardar_cambios()

    print(f"Novedades reales a notificar: {len(procesos_nuevos_recientes)}")

    if not procesos_nuevos_recientes:
        print("Sin novedades -- no se manda nada a Telegram.")
        return

    bloques = [formatear_bloque(p) for p in procesos_nuevos_recientes]

    if len(bloques) > LIMITE_NOVEDADES_SIN_RESUMEN:
        print(f"ATENCION: {len(bloques)} novedades de una sola vez supera el limite razonable "
              f"({LIMITE_NOVEDADES_SIN_RESUMEN}). Se manda un resumen en vez del detalle.")
        resumen = (
            f"⚠️ <b>Aviso: muchas novedades de golpe</b>\n"
            f"Se detectaron {len(bloques)} procesos nuevos en esta corrida, más de lo esperable "
            f"para una ventana de 2 horas. No se mandó el detalle para no saturar el chat.\n"
            f"Revisá el dashboard o el log de GitHub Actions para más detalle."
        )
        enviar_telegram(telegram_token, telegram_chat_id, resumen)
        print("Listo (resumen enviado en vez del detalle).")
        return

    mensajes = dividir_en_mensajes(bloques)
    for i, mensaje in enumerate(mensajes, start=1):
        print(f"Enviando mensaje {i}/{len(mensajes)} a Telegram...")
        enviar_telegram(telegram_token, telegram_chat_id, mensaje)

    print(f"Listo. {len(procesos_nuevos_recientes)} novedad(es) enviada(s) por Telegram.")


if __name__ == "__main__":
    main()
