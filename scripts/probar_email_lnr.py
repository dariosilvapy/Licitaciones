"""
Prueba puntual: busca una LNR especifica por numero y manda un correo de
prueba (marcado [PRUEBA]) a una lista de destinatarios, sin depender de
que este en la etapa "abierta" ni de las reglas de reglas_alertas.txt.

Se dispara desde Actions ("Probar Email LNR"), con inputs: lnr_id (numero,
ej. 1006) y destinatarios (lista separada por coma).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core
from monitorear_lnr import CSV_URL, CONVOCANTE_MSPBS, normalizar_lnr
from notificar_telegram import enviar_email

import csv
import io
import requests


def buscar_lnr_por_numero(numero: str) -> dict:
    """Busca por nro_nombre_licitacion, SIN restringir etapa (para
    encontrarla aunque ya no este abierta)."""
    params = {
        "nro_nombre_licitacion": numero,
        "convocantes[0]": CONVOCANTE_MSPBS,
    }
    resp = requests.get(CSV_URL, params=params, timeout=45)
    if resp.status_code != 200:
        raise RuntimeError(f"Error HTTP {resp.status_code} buscando la LNR {numero}")

    texto = resp.content.decode("utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto), delimiter=";")
    filas = list(lector)

    for fila in filas:
        if fila.get("nro_licitacion", "").strip() == str(numero):
            return normalizar_lnr(fila)

    return None


def armar_email_prueba(lnr: dict) -> str:
    return f"""
    <html><body style="font-family:sans-serif;">
    <h2>⚠️ Correo de PRUEBA — Alertas LNR</h2>
    <p>Este es un correo de prueba, no una alerta real.</p>
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:14px;margin-top:14px;">
      <div style="font-size:11px;color:#888;text-transform:uppercase;">LNR N° {lnr['id_llamado']}</div>
      <div style="font-size:15px;font-weight:600;margin:2px 0 6px;">{lnr['nombre_licitacion']}</div>
      <div style="font-size:13px;color:#444;">Convocante: {lnr['convocante']}</div>
      <div style="font-size:13px;color:#444;">Etapa: {lnr['etapa']}</div>
      <div style="font-size:13px;color:#444;">Fecha de entrega de oferta: {lnr['fecha_entrega_oferta'] or 'no especificada'}</div>
      <div style="margin-top:8px;"><a href="{lnr['link']}">Ver publicación completa ↗</a></div>
    </div>
    </body></html>
    """


def main():
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    lnr_id = os.environ.get("LNR_ID")
    destinatarios_raw = os.environ.get("DESTINATARIOS_PRUEBA", "")

    destinatarios = [d.strip() for d in destinatarios_raw.split(",") if d.strip()]

    faltantes = [n for n, v in [
        ("GMAIL_USER", gmail_user), ("GMAIL_APP_PASSWORD", gmail_password),
        ("LNR_ID", lnr_id),
    ] if not v]
    if faltantes or not destinatarios:
        print(f"ERROR: faltan datos. Variables faltantes: {faltantes}. Destinatarios: {destinatarios}")
        sys.exit(1)

    print(f"Buscando LNR N° {lnr_id} (MSPBS)...")
    lnr = buscar_lnr_por_numero(lnr_id)

    if not lnr:
        print(f"ERROR: no se encontro ninguna LNR con numero {lnr_id} para MSPBS.")
        sys.exit(1)

    print(f"Encontrada: {lnr['nombre_licitacion']}")

    cuerpo = armar_email_prueba(lnr)
    asunto = f"[PRUEBA] LNR {lnr_id}: {lnr['nombre_licitacion'][:60]}"

    exitosos, con_error = 0, 0
    for destinatario in destinatarios:
        try:
            enviar_email(gmail_user, gmail_password, destinatario, asunto, cuerpo)
            print(f"OK: enviado a {destinatario}")
            exitosos += 1
        except Exception as e:
            print(f"ERROR enviando a {destinatario}: {e}")
            con_error += 1

    print(f"\nListo. Exitosos: {exitosos} | Con error: {con_error}")
    if con_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
