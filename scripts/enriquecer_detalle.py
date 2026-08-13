"""
Enriquecimiento de detalle: monto adjudicado real (por proveedor) y fecha
de apertura de ofertas precisa (tender.bidOpening.date, que refleja
postergas -- a diferencia de tenderPeriod.endDate, que puede quedar con la
fecha original del pliego).

Por que existe este script aparte: /search/processes (lo que usa la
actualizacion diaria) es un "record package minimo" -- no trae montos ni la
fecha de apertura real. Para conseguirlos hace falta una llamada aparte por
licitacion (GET /tender/{id}) y una llamada aparte por cada adjudicacion
(GET /awards/{id}). Con ~13.500 procesos ya cargados, hacer esto para todos
de una sola vez no es viable (riesgo de rate-limit, y el token dura 15
minutos) -- este script procesa un LOTE por corrida (BATCH_SIZE, por
defecto 150) y se deja programado para correr cada tanto hasta ponerse al
dia con todo el historico. Los procesos nuevos que trae la corrida diaria
tambien van entrando a la cola automaticamente (quedan con
"enriquecido": False hasta que este script los alcance).

Guarda progreso despues de cada registro, asi una corrida interrumpida no
pierde el trabajo ya hecho.
"""

import os
import sys
import time
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

BATCH_SIZE = int(os.environ.get("ENRIQUECER_BATCH_SIZE", "150"))
PAUSA_ENTRE_LLAMADAS = 0.15  # segundos, para no golpear la API muy seguido
RENOVAR_TOKEN_CADA = 50  # llamadas


def solicitar(token: str, path: str):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{core.API_BASE}/{path.lstrip('/')}"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    try:
        return resp.json(), None
    except Exception:
        return None, "respuesta no es JSON"


def enriquecer_registro(token: str, clave: str, registro: dict, llamadas_hechas: list) -> tuple:
    """Devuelve (registro_actualizado, token_actual, hubo_error)."""
    tender_id = registro.get("tender_id_completo") or ""
    if not tender_id:
        registro["enriquecido"] = True
        registro["enriquecimiento_nota"] = "sin tender_id_completo"
        return registro, token, False

    import urllib.parse
    tender_id_encoded = urllib.parse.quote(tender_id, safe="")

    datos_tender, error = solicitar(token, f"tender/{tender_id_encoded}")
    llamadas_hechas[0] += 1
    time.sleep(PAUSA_ENTRE_LLAMADAS)

    if error:
        registro["enriquecido"] = True
        registro["enriquecimiento_nota"] = f"error tender: {error}"
        return registro, token, True

    tender = datos_tender.get("tender", {}) if isinstance(datos_tender, dict) else {}

    bid_opening = tender.get("bidOpening", {}) if isinstance(tender.get("bidOpening"), dict) else {}
    if bid_opening.get("date"):
        registro["fecha_apertura_real"] = core.solo_fecha(bid_opening["date"])

    valor_tender = tender.get("value", {}) if isinstance(tender.get("value"), dict) else {}
    if valor_tender.get("amount") is not None:
        registro["monto_estimado"] = valor_tender["amount"]

    # Montos por adjudicacion (proveedor + monto real adjudicado)
    proveedores_montos = []
    monto_adjudicado_total = 0
    tuvo_error_award = False

    for award_id in registro.get("award_ids", []):
        award_id_encoded = urllib.parse.quote(str(award_id), safe="")
        datos_award, error_award = solicitar(token, f"awards/{award_id_encoded}")
        llamadas_hechas[0] += 1
        time.sleep(PAUSA_ENTRE_LLAMADAS)

        if error_award or not isinstance(datos_award, dict):
            tuvo_error_award = True
            continue

        award = datos_award.get("award", datos_award) if isinstance(datos_award.get("award", datos_award), dict) else {}
        valor_award = award.get("value", {}) if isinstance(award.get("value"), dict) else {}
        monto = valor_award.get("amount")

        suppliers = award.get("suppliers", [])
        if not isinstance(suppliers, list):
            suppliers = [suppliers] if suppliers else []

        for s in suppliers:
            if isinstance(s, dict) and s.get("name"):
                proveedores_montos.append({
                    "nombre": s["name"],
                    "monto": monto if monto is not None else 0,
                })
                if monto:
                    monto_adjudicado_total += monto

    registro["proveedores_montos"] = proveedores_montos
    registro["monto_adjudicado"] = monto_adjudicado_total if proveedores_montos else None
    registro["enriquecido"] = True
    if tuvo_error_award:
        registro["enriquecimiento_nota"] = "algunas adjudicaciones no se pudieron consultar"

    return registro, token, False


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")

    if not consumer_key or not consumer_secret:
        print("ERROR: faltan DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        sys.exit(1)

    procesos = core.cargar_datos_existentes()
    pendientes = [clave for clave, r in procesos.items() if not r.get("enriquecido")]

    print(f"Total de procesos: {len(procesos)} | Pendientes de enriquecer: {len(pendientes)}")

    if not pendientes:
        print("No hay nada pendiente. Listo.")
        return

    lote = pendientes[:BATCH_SIZE]
    print(f"Procesando este lote: {len(lote)} registro(s).\n")

    print("Autenticando...")
    token = core.obtener_token(consumer_key, consumer_secret)
    llamadas_hechas = [0]

    exitosos, con_error = 0, 0

    for i, clave in enumerate(lote, start=1):
        if llamadas_hechas[0] >= RENOVAR_TOKEN_CADA:
            token = core.obtener_token(consumer_key, consumer_secret)
            llamadas_hechas[0] = 0

        registro = procesos[clave]
        registro_actualizado, token, hubo_error = enriquecer_registro(token, clave, registro, llamadas_hechas)
        procesos[clave] = registro_actualizado

        if hubo_error:
            con_error += 1
        else:
            exitosos += 1

        if i % 10 == 0 or i == len(lote):
            print(f"  {i}/{len(lote)} procesados (guardando progreso)...")
            core.guardar_datos(procesos)

    core.guardar_datos(procesos)

    restantes = len([c for c, r in procesos.items() if not r.get("enriquecido")])
    print(f"\nListo este lote. Exitosos: {exitosos} | Con error: {con_error} | "
          f"Quedan pendientes: {restantes}")


if __name__ == "__main__":
    main()
