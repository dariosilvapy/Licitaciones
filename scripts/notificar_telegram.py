"""
Chequeo de novedades cada 2 horas + alerta por Telegram.

Corre via .github/workflows/alertas_telegram.yml (cron cada 2hs). Hace lo
mismo que fetch_dncp.py pero con una ventana corta (hoy + ayer, como
colchon por si algo se publico cerca de medianoche), y ademas:

  1. Anota que OCID ya conociamos ANTES de esta corrida.
  2. Actualiza data/procesos.json igual que la corrida diaria (efecto
     secundario util: el dashboard tambien queda mas fresco).
  3. Cualquier OCID que aparezca ahora y no estuviera antes = "nuevo".
  4. Manda un mensaje a Telegram con la lista de nuevos (ID, nombre,
     convocante, link). Si no hay nuevos, no manda nada (para no generar
     ruido).

Nota sobre la granularidad de fecha: la API solo confirma parametros
fecha_desde/fecha_hasta a nivel de dia (no de hora), asi que este script no
filtra por "ultimas 2 horas" en la consulta -- vuelve a traer el dia
completo cada vez, y la deteccion de "nuevo" se hace comparando contra lo
que ya teniamos guardado, no contra la ventana de la consulta. Esto es mas
robusto (no depende de que la API soporte precision horaria) aunque hace
llamadas un poco redundantes; el costo es bajo.
"""

import os
import sys
from datetime import date, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
LIMITE_CARACTERES_TELEGRAM = 4000  # el limite real es 4096, dejamos margen

# Ademas de ser una clave "nueva" (no vista antes en procesos.json), se
# exige que la fecha de publicacion sea realmente reciente antes de avisar.
# Esto evita que un backfill historico, o cualquier otra corrida que agregue
# muchas claves "nuevas" al archivo de una sola vez, dispare una alerta por
# cada una -- si la fecha de publicacion es vieja, no es una novedad real.
DIAS_RECIENCIA = int(os.environ.get("ALERTA_DIAS_RECIENCIA", "3"))
LIMITE_NOVEDADES_SIN_RESUMEN = int(os.environ.get("ALERTA_LIMITE_SIN_RESUMEN", "40"))


def enviar_telegram(token: str, chat_id: str, texto: str):
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
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
    """Junta bloques en mensajes de hasta LIMITE_CARACTERES_TELEGRAM,
    para no pasarse del limite de Telegram si hay muchas novedades."""
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

    faltantes = [nombre for nombre, valor in [
        ("DNCP_CONSUMER_KEY", consumer_key),
        ("DNCP_CONSUMER_SECRET", consumer_secret),
        ("TELEGRAM_BOT_TOKEN", telegram_token),
        ("TELEGRAM_CHAT_ID", telegram_chat_id),
    ] if not valor]
    if faltantes:
        print(f"ERROR: faltan estas variables de entorno: {', '.join(faltantes)}")
        sys.exit(1)

    print("Autenticando contra la API de la DNCP...")
    token = core.obtener_token(consumer_key, consumer_secret)
    print("Token obtenido.")

    procesos = core.cargar_datos_existentes()
    claves_antes = set(procesos.keys())
    print(f"Procesos ya conocidos antes de esta corrida: {len(claves_antes)}")

    fecha_desde = str(date.today() - timedelta(days=1))  # colchon de 1 dia
    fecha_hasta = str(date.today())

    print(f"Consultando novedades entre {fecha_desde} y {fecha_hasta}...")
    registros = core.buscar_todo(token, fecha_desde, fecha_hasta)

    for registro in registros:
        if not isinstance(registro, dict):
            continue
        plano = core.normalizar(registro)
        clave = plano["ocid"] or plano["id_llamado"]
        if not clave:
            continue
        procesos[clave] = core.combinar_con_enriquecimiento(procesos.get(clave), plano)  # upsert, igual que la corrida diaria

    core.guardar_datos(procesos)

    claves_nuevas = set(procesos.keys()) - claves_antes
    print(f"Procesos nuevos detectados (por clave): {len(claves_nuevas)}")

    # Filtro de reciencia: solo avisar de los que ademas tengan fecha de
    # publicacion dentro de la ventana de dias reciente.
    limite_fecha = str(date.today() - timedelta(days=DIAS_RECIENCIA))
    claves_nuevas_recientes = {
        clave for clave in claves_nuevas
        if (procesos[clave].get("fecha_publicacion") or "") >= limite_fecha
    }
    descartadas_por_fecha = len(claves_nuevas) - len(claves_nuevas_recientes)
    if descartadas_por_fecha:
        print(f"Descartadas por no ser recientes (fecha_publicacion < {limite_fecha}): {descartadas_por_fecha}")
    claves_nuevas = claves_nuevas_recientes

    print(f"Novedades reales a notificar: {len(claves_nuevas)}")

    if not claves_nuevas:
        print("Sin novedades -- no se manda nada a Telegram.")
        return

    bloques = [formatear_bloque(procesos[clave]) for clave in claves_nuevas]

    if len(bloques) > LIMITE_NOVEDADES_SIN_RESUMEN:
        print(f"ATENCION: {len(bloques)} novedades de una sola vez supera el limite razonable "
              f"({LIMITE_NOVEDADES_SIN_RESUMEN}). Se manda un resumen en vez de un mensaje por cada una, "
              f"para no inundar Telegram -- probablemente valga la pena revisar si esto es una novedad real "
              f"o un efecto secundario de otro workflow (backfill, etc.).")
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

    print(f"Listo. {len(claves_nuevas)} novedad(es) enviada(s) por Telegram.")


if __name__ == "__main__":
    main()
