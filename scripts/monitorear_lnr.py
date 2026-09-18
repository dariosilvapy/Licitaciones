"""
Monitoreo de Licitaciones No Reguladas (LNR) del MSPBS.

Esta seccion de la DNCP NO esta cubierta por la API (/search/processes),
asi que se resuelve descargando directo el CSV que ofrece el buscador
publico -- confirmado que es un link plano (sin login, sin JavaScript
por detras), asi que se automatiza 100% con un simple GET, sin navegador.

Fuente: https://www.contrataciones.gov.py/buscador/licitaciones-no-reguladas.csv
Filtros aplicados: convocantes[0]=306 (MSPBS), etapas_licitacion[0]=CONV
(solo las que todavia estan en convocatoria abierta -- las que importan
para decidir si presentarse).

Guarda los slugs ya vistos en data/lnr_conocidos.json para detectar
"nuevo" en cada corrida. La primera corrida solo guarda la base sin
avisar de nada (para no mandar de golpe todo lo que ya estaba publicado
antes de empezar a monitorear).

Reutiliza exactamente la misma logica de Telegram/email/reglas que el
resto del sistema (dncp_core.py + notificar_telegram.py) -- Telegram
manda TODAS las LNR nuevas (sin filtro, como el resto de las alertas);
el email rutea segun data/reglas_alertas.txt (palabras clave + exclusiones).
"""

import csv
import io
import os
import sys
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core
from notificar_telegram import (
    enviar_telegram, dividir_en_mensajes, enviar_email, LIMITE_NOVEDADES_SIN_RESUMEN,
)

CSV_URL = "https://www.contrataciones.gov.py/buscador/licitaciones-no-reguladas.csv"
CONVOCANTE_MSPBS = "306"
ETAPA_ABIERTA = "CONV"
MAX_PAGINAS = 15  # tope de seguridad, igual que en la busqueda de la API

CONOCIDOS_FILE = os.path.join(core.DATA_DIR, "lnr_conocidos.json")


def descargar_pagina_csv(pagina: int) -> list:
    params = {
        "convocantes[0]": CONVOCANTE_MSPBS,
        "etapas_licitacion[0]": ETAPA_ABIERTA,
        "page": pagina,
    }
    resp = requests.get(CSV_URL, params=params, timeout=45)
    if resp.status_code != 200:
        raise RuntimeError(f"Error HTTP {resp.status_code} descargando la pagina {pagina}")

    texto = resp.content.decode("utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto), delimiter=";")
    return list(lector)


def descargar_todo() -> list:
    todas = []
    for pagina in range(1, MAX_PAGINAS + 1):
        filas = descargar_pagina_csv(pagina)
        print(f"  Pagina {pagina}: {len(filas)} fila(s).")
        if not filas:
            break
        todas.extend(filas)
        if len(filas) < 20:  # por debajo de un tamano de pagina tipico, asumimos que es la ultima
            break
    return todas


def normalizar_lnr(fila: dict) -> dict:
    slug = fila.get("slug", "").strip()
    return {
        "slug": slug,
        "id_llamado": fila.get("nro_licitacion", "").strip(),
        "nombre_licitacion": (fila.get("nombre_licitacion") or "").strip(),
        "convocante": (fila.get("convocante") or "").strip(),
        "categoria": "",  # no viene poblada en este export
        "fecha_entrega_oferta": (fila.get("fecha_entrega_oferta") or "").strip(),
        "etapa": (fila.get("etapa_licitacion") or "").strip(),
        "link": f"https://www.contrataciones.gov.py/licitaciones-no-reguladas/{slug}.html" if slug else "",
    }


def cargar_conocidos() -> set:
    if os.path.exists(CONOCIDOS_FILE):
        import json
        try:
            with open(CONOCIDOS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f).get("conocidos", []))
        except (Exception,):
            return set()
    return set()


def guardar_conocidos(conocidos: set):
    import json
    os.makedirs(core.DATA_DIR, exist_ok=True)
    with open(CONOCIDOS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": date.today().isoformat(),
            "total": len(conocidos),
            "conocidos": sorted(conocidos),
        }, f, ensure_ascii=False, indent=2)


def formatear_bloque_telegram(lnr: dict) -> str:
    return (
        f"🆕 <b>Nueva LNR (MSPBS)</b>\n"
        f"ID: {lnr['id_llamado']}\n"
        f"Nombre: {lnr['nombre_licitacion']}\n"
        f"Fecha de entrega de oferta: {lnr['fecha_entrega_oferta'] or 'no especificada'}\n"
        f"Link: {lnr['link']}\n"
    )


def formatear_tarjeta_email(lnr: dict) -> str:
    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:14px;margin-bottom:14px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;">LNR N° {lnr['id_llamado']}</div>
      <div style="font-size:15px;font-weight:600;margin:2px 0 6px;">{lnr['nombre_licitacion']}</div>
      <div style="font-size:13px;color:#444;">Fecha de entrega de oferta: {lnr['fecha_entrega_oferta'] or 'no especificada'}</div>
      <div style="margin-top:8px;"><a href="{lnr['link']}">Ver publicación completa ↗</a></div>
    </div>
    """


def armar_email_html(lnrs: list) -> str:
    tarjetas = "".join(formatear_tarjeta_email(l) for l in lnrs)
    return f"""
    <html><body style="font-family:sans-serif;">
    <h2>Nuevas Licitaciones No Reguladas (MSPBS)</h2>
    <p>{len(lnrs)} LNR nueva(s) que coinciden con tus palabras clave.</p>
    {tarjetas}
    </body></html>
    """


def main():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    telegram_activo = bool(telegram_token and telegram_chat_id)
    email_activo = bool(gmail_user and gmail_password)

    if not telegram_activo and not email_activo:
        print("ERROR: no hay credenciales de Telegram ni de Gmail configuradas.")
        sys.exit(1)

    print("Descargando CSV de Licitaciones No Reguladas (MSPBS, en convocatoria abierta)...")
    filas = descargar_todo()
    print(f"Total de filas descargadas: {len(filas)}")

    lnrs = [normalizar_lnr(f) for f in filas if f.get("slug")]

    conocidos = cargar_conocidos()
    es_primera_corrida = len(conocidos) == 0

    nuevas = [l for l in lnrs if l["slug"] not in conocidos]
    conocidos.update(l["slug"] for l in lnrs)
    guardar_conocidos(conocidos)

    if es_primera_corrida:
        print(f"Primera corrida: se guardan {len(nuevas)} LNR como linea de base, sin avisar de ninguna.")
        return

    print(f"LNR nuevas detectadas: {len(nuevas)}")
    if not nuevas:
        print("Sin novedades -- no se manda nada.")
        return

    if telegram_activo:
        if len(nuevas) > LIMITE_NOVEDADES_SIN_RESUMEN:
            enviar_telegram(telegram_token, telegram_chat_id,
                             f"⚠️ Se detectaron {len(nuevas)} LNR nuevas de golpe. Revisá el dashboard/log.")
        else:
            bloques = [formatear_bloque_telegram(l) for l in nuevas]
            for i, mensaje in enumerate(dividir_en_mensajes(bloques), start=1):
                enviar_telegram(telegram_token, telegram_chat_id, mensaje)
            print(f"Telegram: {len(nuevas)} LNR enviada(s).")

    if email_activo:
        config_reglas = core.cargar_reglas_alertas()
        if not config_reglas["reglas"]:
            print("Email: no hay reglas configuradas, no se manda nada por correo.")
        else:
            correo_a_lnrs = {}
            for lnr in nuevas:
                for correo in core.destinatarios_para_registro(lnr, config_reglas):
                    correo_a_lnrs.setdefault(correo, []).append(lnr)

            for destinatario, lista in correo_a_lnrs.items():
                try:
                    enviar_email(gmail_user, gmail_password, destinatario,
                                 f"DNCP LNR: {len(lista)} licitación(es) nueva(s) - MSPBS",
                                 armar_email_html(lista))
                    print(f"Email: enviado a {destinatario} ({len(lista)} LNR).")
                except Exception as e:
                    print(f"Email: ERROR enviando a {destinatario}: {e}")


if __name__ == "__main__":
    main()
