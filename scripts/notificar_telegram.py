"""
Chequeo de novedades cada 2 horas + alertas por Telegram y correo.

Corre via .github/workflows/alertas_telegram.yml (cron cada 2hs). Guarda
particionado por anio (ver dncp_core.py). Ademas de ser una clave "nueva",
se exige que fecha_publicacion sea reciente antes de avisar -- evita que
un backfill u otra corrida que agregue muchas claves de una sola vez
dispare una alerta por cada una.

Dos canales, cada uno opcional segun que secrets esten configurados:

  - Telegram: manda TODAS las novedades que pasan el filtro de
    data/palabras_clave.txt (lista simple, un solo destinatario: vos).

  - Email: rutea cada novedad segun data/reglas_alertas.txt (reglas de
    palabras -> lista de correos). Si una novedad coincide con varias
    reglas, le llega a todos los correos de esas reglas. Si no coincide
    con ninguna regla, no se manda por email a nadie (pero puede seguir
    yendo a Telegram si pasa el filtro de palabras_clave.txt).

Requiere GMAIL_USER y GMAIL_APP_PASSWORD como secrets para el envio de
email (contraseña de aplicacion de Gmail, no la contraseña real).
"""

import os
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_CARACTERES_TELEGRAM = 4000
DIAS_RECIENCIA = int(os.environ.get("ALERTA_DIAS_RECIENCIA", "3"))
LIMITE_NOVEDADES_SIN_RESUMEN = int(os.environ.get("ALERTA_LIMITE_SIN_RESUMEN", "40"))


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def enviar_telegram(token: str, chat_id: str, texto: str):
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(url, data={
        "chat_id": chat_id, "text": texto, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Error al enviar a Telegram (HTTP {resp.status_code}): {resp.text[:300]}")


def formatear_bloque_telegram(proceso: dict) -> str:
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


def enviar_novedades_telegram(telegram_token, telegram_chat_id, novedades):
    palabras = core.cargar_palabras_clave()
    filtradas = [p for p in novedades if core.coincide_palabra_clave(p, palabras)]

    print(f"Telegram: {len(filtradas)}/{len(novedades)} novedades pasan el filtro de palabras clave.")

    if not filtradas:
        print("Telegram: sin novedades que avisar.")
        return

    bloques = [formatear_bloque_telegram(p) for p in filtradas]

    if len(bloques) > LIMITE_NOVEDADES_SIN_RESUMEN:
        resumen = (
            f"⚠️ <b>Aviso: muchas novedades de golpe</b>\n"
            f"Se detectaron {len(bloques)} procesos nuevos en esta corrida, más de lo esperable "
            f"para una ventana de 2 horas. No se mandó el detalle para no saturar el chat.\n"
            f"Revisá el dashboard o el log de GitHub Actions para más detalle."
        )
        enviar_telegram(telegram_token, telegram_chat_id, resumen)
        print("Telegram: resumen enviado en vez del detalle (demasiadas novedades de golpe).")
        return

    mensajes = dividir_en_mensajes(bloques)
    for i, mensaje in enumerate(mensajes, start=1):
        print(f"Telegram: enviando mensaje {i}/{len(mensajes)}...")
        enviar_telegram(telegram_token, telegram_chat_id, mensaje)

    print(f"Telegram: {len(filtradas)} novedad(es) enviada(s).")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def formatear_fila_html(proceso: dict) -> str:
    nombre = proceso.get("nombre_licitacion") or "(sin nombre)"
    convocante = proceso.get("convocante") or "(sin convocante)"
    id_llamado = proceso.get("id_llamado") or "?"
    categoria = proceso.get("categoria") or ""
    link = proceso.get("link") or ""
    link_html = f'<a href="{link}">Ver publicación ↗</a>' if link else "—"
    return (
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;'>{id_llamado}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;'>{nombre}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;'>{convocante}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;'>{categoria}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;'>{link_html}</td>"
        "</tr>"
    )


def armar_email_html(procesos: list) -> str:
    filas = "".join(formatear_fila_html(p) for p in procesos)
    return f"""
    <html><body style="font-family:sans-serif;">
    <h2>Nuevas licitaciones publicadas</h2>
    <p>{len(procesos)} proceso(s) nuevo(s) que coinciden con tus palabras clave.</p>
    <table style="border-collapse:collapse;width:100%;">
      <thead><tr style="background:#f0f0f0;text-align:left;">
        <th style="padding:8px;">ID</th><th style="padding:8px;">Nombre</th>
        <th style="padding:8px;">Convocante</th><th style="padding:8px;">Categoría</th>
        <th style="padding:8px;">Link</th>
      </tr></thead>
      <tbody>{filas}</tbody>
    </table>
    </body></html>
    """


def enviar_email(remitente: str, password: str, destinatario: str, asunto: str, cuerpo_html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destinatario
    msg.attach(MIMEText(cuerpo_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())


def enviar_novedades_email(gmail_user, gmail_password, novedades):
    reglas = core.cargar_reglas_alertas()
    if not reglas:
        print("Email: no hay reglas en data/reglas_alertas.txt, no se manda nada por correo.")
        return

    correo_a_novedades = {}
    for proceso in novedades:
        for correo in core.destinatarios_para_registro(proceso, reglas):
            correo_a_novedades.setdefault(correo, []).append(proceso)

    if not correo_a_novedades:
        print("Email: ninguna novedad coincidio con alguna regla, no se manda nada.")
        return

    for destinatario, procesos in correo_a_novedades.items():
        asunto = f"DNCP: {len(procesos)} licitación(es) nueva(s)"
        cuerpo = armar_email_html(procesos)
        try:
            enviar_email(gmail_user, gmail_password, destinatario, asunto, cuerpo)
            print(f"Email: enviado a {destinatario} ({len(procesos)} proceso(s)).")
        except Exception as e:
            print(f"Email: ERROR enviando a {destinatario}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not consumer_key or not consumer_secret:
        print("ERROR: faltan DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        sys.exit(1)

    telegram_activo = bool(telegram_token and telegram_chat_id)
    email_activo = bool(gmail_user and gmail_password)

    if not telegram_activo and not email_activo:
        print("ERROR: no hay ni credenciales de Telegram ni de Gmail configuradas. Nada para hacer.")
        sys.exit(1)

    print(f"Canales activos -> Telegram: {telegram_activo} | Email: {email_activo}")

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

    print(f"Novedades reales detectadas: {len(procesos_nuevos_recientes)}")

    if not procesos_nuevos_recientes:
        print("Sin novedades -- no se manda nada.")
        return

    if telegram_activo:
        enviar_novedades_telegram(telegram_token, telegram_chat_id, procesos_nuevos_recientes)

    if email_activo:
        enviar_novedades_email(gmail_user, gmail_password, procesos_nuevos_recientes)


if __name__ == "__main__":
    main()
