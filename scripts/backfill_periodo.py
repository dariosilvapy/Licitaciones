"""
Backfill extendido por rango de fechas, vía API (no CSV).

A diferencia de fetch_dncp.py (ventana corta, corrida diaria) o
importar_historico_csv.py (desde archivos CSV), este script trae un rango
de fechas AMPLIO directo de la API de la DNCP -- partido en bloques
mensuales, porque /search/processes tiene un limite duro de 10.000
registros por consulta (documentado por la propia DNCP), sin importar cuan
grande sea el rango de fechas pedido. Si pidieramos un anio entero de una
sola vez y hubiera mas de 10.000 procesos, la API cortaria en silencio y
nos faltarian datos sin darnos cuenta.

Partiendo mes a mes, cada consulta individual es mucho mas chica y esta muy
lejos de los 10.000, asi que es seguro.

Uso: se dispara desde el workflow "Backfill por rango de fechas"
(workflow_dispatch, con inputs de fecha_desde y fecha_hasta).

Los datos que trae son API-source (mas ricos que el CSV historico: incluyen
proveedores, cantidad de adjudicaciones/contratos, etc.), y se fusionan con
upsert normal -- se actualizan si ya existian, igual que fetch_dncp.py.
"""

import calendar
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core


def generar_bloques_mensuales(fecha_desde: date, fecha_hasta: date):
    """Genera tuplas (inicio, fin) de un mes cada una, cubriendo el rango
    pedido (el primer y ultimo bloque se recortan a las fechas exactas)."""
    bloques = []
    actual = fecha_desde.replace(day=1)

    while actual <= fecha_hasta:
        ultimo_dia_mes = calendar.monthrange(actual.year, actual.month)[1]
        fin_mes = actual.replace(day=ultimo_dia_mes)

        inicio_bloque = max(actual, fecha_desde)
        fin_bloque = min(fin_mes, fecha_hasta)
        bloques.append((inicio_bloque, fin_bloque))

        if actual.month == 12:
            actual = actual.replace(year=actual.year + 1, month=1)
        else:
            actual = actual.replace(month=actual.month + 1)

    return bloques


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")
    fecha_desde_str = os.environ.get("FECHA_DESDE")
    fecha_hasta_str = os.environ.get("FECHA_HASTA")

    faltantes = [n for n, v in [
        ("DNCP_CONSUMER_KEY", consumer_key), ("DNCP_CONSUMER_SECRET", consumer_secret),
        ("FECHA_DESDE", fecha_desde_str), ("FECHA_HASTA", fecha_hasta_str),
    ] if not v]
    if faltantes:
        print(f"ERROR: faltan: {', '.join(faltantes)}")
        sys.exit(1)

    fecha_desde = date.fromisoformat(fecha_desde_str)
    fecha_hasta = date.fromisoformat(fecha_hasta_str)

    if fecha_desde > fecha_hasta:
        print("ERROR: FECHA_DESDE no puede ser posterior a FECHA_HASTA.")
        sys.exit(1)

    print("Autenticando contra la API de la DNCP...")
    token = core.obtener_token(consumer_key, consumer_secret)
    print("Token obtenido.")

    procesos = core.cargar_datos_existentes()
    total_previo = len(procesos)

    bloques = generar_bloques_mensuales(fecha_desde, fecha_hasta)
    print(f"\nRango total: {fecha_desde} a {fecha_hasta} -- partido en {len(bloques)} bloque(s) mensual(es).\n")

    nuevos_total, actualizados_total = 0, 0

    for i, (inicio, fin) in enumerate(bloques, start=1):
        print(f"--- Bloque {i}/{len(bloques)}: {inicio} a {fin} ---")

        # El token dura 15 minutos -- si el backfill es largo, se renueva
        # preventivamente cada varios bloques.
        if i > 1 and i % 10 == 0:
            print("Renovando token (por las dudas, backfill largo)...")
            token = core.obtener_token(consumer_key, consumer_secret)

        registros = core.buscar_todo(token, str(inicio), str(fin))

        if len(registros) >= 9800:
            print(f"  ATENCION: este bloque devolvio {len(registros)} registros, "
                  f"muy cerca del limite de 10.000. Puede haber datos truncados "
                  f"en este mes puntual -- si pasa, conviene partir ese mes en "
                  f"quincenas y correr el backfill de nuevo solo para ese rango.")

        nuevos_bloque, actualizados_bloque = 0, 0
        for registro in registros:
            if not isinstance(registro, dict):
                continue
            plano = core.normalizar(registro)
            clave = plano["ocid"] or plano["id_llamado"]
            if not clave:
                continue
            if clave in procesos:
                actualizados_bloque += 1
            else:
                nuevos_bloque += 1
            procesos[clave] = plano

        print(f"  {len(registros)} recibidos | {nuevos_bloque} nuevos | {actualizados_bloque} actualizados")
        nuevos_total += nuevos_bloque
        actualizados_total += actualizados_bloque

        # Se guarda progresivamente despues de cada bloque, para no perder
        # todo el trabajo si un bloque futuro falla.
        core.guardar_datos(procesos)

    print(f"\n=== Listo ===")
    print(f"Antes: {total_previo} | Nuevos: {nuevos_total} | "
          f"Actualizados: {actualizados_total} | Total ahora: {len(procesos)}")


if __name__ == "__main__":
    main()
