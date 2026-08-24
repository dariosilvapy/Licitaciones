"""
Enriquecimiento de detalle: monto adjudicado real (por proveedor, separado
por moneda) y fecha de apertura de ofertas precisa. Guarda particionado por
anio (ver dncp_core.py) -- recorre todos los anios buscando pendientes,
pero solo reescribe los anios que efectivamente toco en esta corrida.
"""

import os
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

BATCH_SIZE = int(os.environ.get("ENRIQUECER_BATCH_SIZE", "150"))
PAUSA_ENTRE_LLAMADAS = 0.15
RENOVAR_TOKEN_CADA = 50

PROVEEDORES_PRIORITARIOS = [
    p.strip().lower() for p in os.environ.get("ENRIQUECER_PROVEEDORES", "").split(",") if p.strip()
]
SOLO_PRIORITARIOS = os.environ.get("ENRIQUECER_SOLO_PRIORITARIOS", "").strip().lower() in ("1", "true", "si", "yes")


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


def coincide_proveedor_prioritario(registro: dict) -> bool:
    if not PROVEEDORES_PRIORITARIOS:
        return False
    nombres = (registro.get("proveedores_adjudicados") or "").lower()
    return any(t in nombres for t in PROVEEDORES_PRIORITARIOS)


def ordenar_pendientes(pendientes: list) -> list:
    """pendientes: lista de (anio, clave, registro). Prioritarios primero
    (si se configuraron), y dentro de cada grupo, mas recientes primero."""
    def fecha_de(item):
        return item[2].get("fecha_publicacion") or ""

    if PROVEEDORES_PRIORITARIOS:
        prioritarios = [it for it in pendientes if coincide_proveedor_prioritario(it[2])]
        prioritarios.sort(key=fecha_de, reverse=True)
        if SOLO_PRIORITARIOS:
            return prioritarios
        resto = [it for it in pendientes if not coincide_proveedor_prioritario(it[2])]
        resto.sort(key=fecha_de, reverse=True)
        return prioritarios + resto

    return sorted(pendientes, key=fecha_de, reverse=True)


def enriquecer_registro(token: str, registro: dict, llamadas_hechas: list) -> tuple:
    """Devuelve (registro_actualizado, hubo_error)."""
    tender_id = registro.get("tender_id_completo") or ""
    if not tender_id:
        registro["enriquecido"] = True
        registro["enriquecimiento_nota"] = "sin tender_id_completo"
        return registro, False

    if "award_ids" not in registro and registro.get("cantidad_adjudicaciones", 0) > 0:
        registro["enriquecido"] = True
        registro["enriquecimiento_nota"] = "sin award_ids: re-correr backfill de su rango de fechas y luego el reset"
        return registro, False

    tender_id_encoded = urllib.parse.quote(tender_id, safe="")
    datos_tender, error = solicitar(token, f"tender/{tender_id_encoded}")
    llamadas_hechas[0] += 1
    time.sleep(PAUSA_ENTRE_LLAMADAS)

    if error:
        registro["enriquecido"] = True
        registro["enriquecimiento_nota"] = f"error tender: {error}"
        return registro, True

    tender = datos_tender.get("tender", {}) if isinstance(datos_tender, dict) else {}

    bid_opening = tender.get("bidOpening", {}) if isinstance(tender.get("bidOpening"), dict) else {}
    if bid_opening.get("date"):
        registro["fecha_apertura_real"] = core.solo_fecha(bid_opening["date"])

    valor_tender = tender.get("value", {}) if isinstance(tender.get("value"), dict) else {}
    if valor_tender.get("amount") is not None:
        registro["monto_estimado"] = valor_tender["amount"]

    proveedores_montos = []
    monto_gs_total = 0
    monto_usd_total = 0
    tuvo_error_award = False

    for award_id in registro.get("award_ids", []):
        award_id_encoded = urllib.parse.quote(str(award_id), safe="")
        datos_award, error_award = solicitar(token, f"awards/{award_id_encoded}")
        llamadas_hechas[0] += 1
        time.sleep(PAUSA_ENTRE_LLAMADAS)

        if error_award or not isinstance(datos_award, dict):
            tuvo_error_award = True
            continue

        lista_awards = datos_award.get("awards", [])
        award = lista_awards[0] if lista_awards and isinstance(lista_awards[0], dict) else {}
        valor_award = award.get("value", {}) if isinstance(award.get("value"), dict) else {}
        monto = valor_award.get("amount")

        suppliers = award.get("suppliers", [])
        if not isinstance(suppliers, list):
            suppliers = [suppliers] if suppliers else []

        for s in suppliers:
            if isinstance(s, dict) and s.get("name"):
                moneda = valor_award.get("currency") or "PYG"
                proveedores_montos.append({
                    "nombre": s["name"],
                    "monto": monto if monto is not None else 0,
                    "moneda": moneda,
                })
                if monto:
                    if moneda == "USD":
                        monto_usd_total += monto
                    else:
                        monto_gs_total += monto

    registro["proveedores_montos"] = proveedores_montos
    registro["monto_adjudicado_gs"] = monto_gs_total if proveedores_montos else None
    registro["monto_adjudicado_usd"] = monto_usd_total if proveedores_montos else None
    registro["monto_adjudicado"] = monto_gs_total if proveedores_montos else None
    registro["enriquecido"] = True
    if tuvo_error_award:
        registro["enriquecimiento_nota"] = "algunas adjudicaciones no se pudieron consultar"

    return registro, False


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")

    if not consumer_key or not consumer_secret:
        print("ERROR: faltan DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        sys.exit(1)

    almacen = core.AlmacenParticionado()

    print("Escaneando todos los anios en busca de pendientes...")
    pendientes = [(anio, clave, registro) for anio, clave, registro in almacen.iterar_todos()
                  if not registro.get("enriquecido")]

    total_cargado = almacen.total_cargado()
    print(f"Total de procesos revisados: {total_cargado} | Pendientes de enriquecer: {len(pendientes)}")

    if not pendientes:
        print("No hay nada pendiente. Listo.")
        return

    pendientes = ordenar_pendientes(pendientes)
    if PROVEEDORES_PRIORITARIOS:
        cantidad_prioritarios = len([p for p in pendientes if coincide_proveedor_prioritario(p[2])])
        print(f"Proveedores priorizados: {PROVEEDORES_PRIORITARIOS} "
              f"({cantidad_prioritarios} coinciden"
              f"{', SOLO se procesan estos' if SOLO_PRIORITARIOS else ', el resto se procesa despues'})")

    lote = pendientes[:BATCH_SIZE]
    print(f"Procesando este lote: {len(lote)} registro(s).\n")

    print("Autenticando...")
    token = core.obtener_token(consumer_key, consumer_secret)
    llamadas_hechas = [0]

    exitosos, con_error = 0, 0

    for i, (anio, clave, registro) in enumerate(lote, start=1):
        if llamadas_hechas[0] >= RENOVAR_TOKEN_CADA:
            token = core.obtener_token(consumer_key, consumer_secret)
            llamadas_hechas[0] = 0

        registro_actualizado, hubo_error = enriquecer_registro(token, registro, llamadas_hechas)
        almacen.actualizar_registro(anio, clave, registro_actualizado)

        if hubo_error:
            con_error += 1
        else:
            exitosos += 1

        if i % 10 == 0 or i == len(lote):
            print(f"  {i}/{len(lote)} procesados (guardando progreso)...")
            almacen.guardar_cambios()

    almacen.guardar_cambios()

    restantes = len(pendientes) - len(lote)
    print(f"\nListo este lote. Exitosos: {exitosos} | Con error: {con_error} | "
          f"Quedan pendientes (al menos): {restantes}")


if __name__ == "__main__":
    main()
