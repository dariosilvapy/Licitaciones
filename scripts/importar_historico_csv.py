"""
Importa datos historicos desde archivos CSV tipo "reporte_AAAA.csv".
Guarda particionado por anio (ver dncp_core.py) -- cada registro se
bucketea automaticamente segun su fecha_publicacion real, sin importar de
que archivo CSV vino.

Regla de fusion: si un proceso YA existe y vino de la API (mas rico), NO se
pisa. Si ya existia pero vino de un CSV historico anterior (por ejemplo con
un link roto de una version vieja de este script), SI se corrige.
"""

import csv
import glob
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

HISTORICO_DIR = os.path.join(core.DATA_DIR, "historico_raw")
OCID_PREFIJO = core.OCID_PREFIJO


def parsear_fecha(valor: str) -> str:
    if not valor:
        return ""
    for formato in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor.strip(), formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def procesar_csv(ruta: str) -> dict:
    registros = {}
    with open(ruta, encoding="utf-8-sig") as f:
        lector = csv.DictReader(f, delimiter=";")
        for fila in lector:
            nro = (fila.get("nro_licitacion") or "").strip()
            if not nro:
                continue
            clave = f"{OCID_PREFIJO}-{nro}"
            registros[clave] = {
                "ocid": clave,
                "id_llamado": nro,
                "tender_id_completo": fila.get("convocatoria_slug") or fila.get("planificacion_slug") or "",
                "nombre_licitacion": (fila.get("nombre_licitacion") or "").strip(),
                "convocante": (fila.get("convocante") or "").strip(),
                "estado": (fila.get("etapa_licitacion") or "").strip(),
                "categoria": (fila.get("categoria") or "").strip(),
                "modalidad": (fila.get("tipo_procedimiento") or "").strip(),
                "fecha_publicacion": parsear_fecha(fila.get("fecha_publicacion_convocatoria", "")),
                "fecha_apertura_ofertas": parsear_fecha(fila.get("fecha_entrega_oferta", "")),
                "fecha_estimada": parsear_fecha(fila.get("fecha_estimada", "")),
                "link": f"https://www.contrataciones.gov.py/licitaciones/convocatoria/{fila.get('convocatoria_slug', '').strip()}.html" if fila.get("convocatoria_slug") else "",
                "proveedores_adjudicados": "",
                "cantidad_adjudicaciones": 1 if (fila.get("etapa_licitacion") or "").strip().lower() == "adjudicada" else 0,
                "cantidad_contratos": 0,
                "fuente": "csv_historico",
            }
    return registros


def main():
    if not os.path.isdir(HISTORICO_DIR):
        print(f"No existe la carpeta {HISTORICO_DIR}. Creala y pone ahi los CSV historicos.")
        sys.exit(1)

    archivos = sorted(glob.glob(os.path.join(HISTORICO_DIR, "*.csv")))
    if not archivos:
        print(f"No se encontraron archivos .csv en {HISTORICO_DIR}.")
        sys.exit(1)

    almacen = core.AlmacenParticionado()
    agregados, actualizados, protegidos = 0, 0, 0

    for archivo in archivos:
        print(f"Procesando {archivo} ...")
        registros = procesar_csv(archivo)
        print(f"  {len(registros)} registros leidos.")

        for clave, registro in registros.items():
            anio = core.anio_de_registro(registro)
            existente = almacen.obtener_registro(anio, clave)

            if existente is None:
                almacen.upsert(registro)
                agregados += 1
            elif existente.get("fuente") == "csv_historico":
                almacen.upsert(registro)
                actualizados += 1
            else:
                protegidos += 1  # vino de la API, no se pisa

    almacen.guardar_cambios()

    print(f"\nListo. Agregados: {agregados} | Actualizados (corregidos): {actualizados} | "
          f"Protegidos (ya venian de la API, no tocados): {protegidos}")


if __name__ == "__main__":
    main()
