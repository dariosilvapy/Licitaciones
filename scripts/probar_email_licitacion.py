"""
Prueba puntual: manda un correo de ejemplo con el formato real (tarjeta +
items) para UNA licitacion especifica, a UN destinatario especifico -- sin
depender de que haya novedades nuevas ni de las reglas de reglas_alertas.txt.

Se dispara a mano desde Actions ("Probar Email de una Licitacion"), con
inputs: tender_id (el id largo), id_llamado (el numero corto), destinatario.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core
from notificar_telegram import armar_email_html, enviar_email


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    tender_id = os.environ.get("TENDER_ID")
    id_llamado = os.environ.get("ID_LLAMADO", "")
    destinatario = os.environ.get("DESTINATARIO_PRUEBA")

    faltantes = [n for n, v in [
        ("DNCP_CONSUMER_KEY", consumer_key), ("DNCP_CONSUMER_SECRET", consumer_secret),
        ("GMAIL_USER", gmail_user), ("GMAIL_APP_PASSWORD", gmail_password),
        ("TENDER_ID", tender_id), ("DESTINATARIO_PRUEBA", destinatario),
    ] if not v]
    if faltantes:
        print(f"ERROR: faltan: {', '.join(faltantes)}")
        sys.exit(1)

    print("Autenticando...")
    token = core.obtener_token(consumer_key, consumer_secret)

    print(f"Consultando /tender/{tender_id} ...")
    import requests
    import urllib.parse
    tid = urllib.parse.quote(tender_id, safe="")
    resp = requests.get(f"{core.API_BASE}/tender/{tid}",
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code != 200:
        print(f"ERROR: HTTP {resp.status_code} -- {resp.text[:300]}")
        sys.exit(1)

    data = resp.json()
    tender = data.get("tender", {})

    proceso = {
        "id_llamado": id_llamado or tender.get("id", "?"),
        "tender_id_completo": tender.get("id", tender_id),
        "nombre_licitacion": tender.get("title", ""),
        "convocante": (tender.get("procuringEntity") or {}).get("name", ""),
        "categoria": tender.get("mainProcurementCategoryDetails", ""),
        "link": f"{core.SITE_BASE}/licitaciones/convocatoria/{tender.get('id', tender_id)}.html",
        "_items": tender.get("items", []),
    }

    print(f"Licitacion: {proceso['nombre_licitacion']}")
    print(f"Items encontrados: {len(proceso['_items'])}")

    cuerpo = armar_email_html([proceso])
    enviar_email(gmail_user, gmail_password, destinatario,
                 f"[PRUEBA] DNCP: {proceso['nombre_licitacion'][:60]}", cuerpo)

    print(f"Listo. Correo de prueba enviado a {destinatario}.")


if __name__ == "__main__":
    main()
