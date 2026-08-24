"""
Actualizacion diaria de datos DNCP (corrida amplia, 90 dias / 15 dias de
solapamiento). Guarda particionado por anio (ver dncp_core.py) -- solo
reescribe los anios efectivamente tocados en esta corrida.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

DIAS_SOLAPAMIENTO = 15
DIAS_BACKFILL_INICIAL = 90


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")

    if not consumer_key or not consumer_secret:
        print("ERROR: faltan las variables de entorno DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        sys.exit(1)

    print("Autenticando contra la API de la DNCP...")
    token = core.obtener_token(consumer_key, consumer_secret)
    print("Token obtenido (valido 15 minutos).")

    almacen = core.AlmacenParticionado()
    es_primera_corrida = len(core.listar_anios_existentes()) == 0
    dias = DIAS_BACKFILL_INICIAL if es_primera_corrida else DIAS_SOLAPAMIENTO

    fecha_desde = str(date.today() - timedelta(days=dias))
    fecha_hasta = str(date.today())

    print(f"Buscando procesos con fecha_release entre {fecha_desde} y {fecha_hasta}"
          f" ({'backfill inicial' if es_primera_corrida else 'actualizacion incremental'})...")

    muestra_path = os.path.join(core.DATA_DIR, "muestra_respuesta_busqueda.json")
    registros = core.buscar_todo(token, fecha_desde, fecha_hasta, guardar_muestra_en=muestra_path)

    nuevos, actualizados = 0, 0
    for registro in registros:
        if not isinstance(registro, dict):
            continue
        plano = core.normalizar(registro)
        clave, existia = almacen.upsert(plano)
        if clave is None:
            continue
        if existia:
            actualizados += 1
        else:
            nuevos += 1

    almacen.guardar_cambios()

    print(f"\nListo. Recibidos: {len(registros)} | Nuevos: {nuevos} | "
          f"Actualizados: {actualizados}")


if __name__ == "__main__":
    main()
