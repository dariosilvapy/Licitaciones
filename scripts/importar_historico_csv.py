"""
Importa datos historicos desde archivos CSV tipo "reporte_AAAA.csv"
(exportados desde el portal de la DNCP, formato con columnas
nro_licitacion, nombre_licitacion, convocante, etapa_licitacion, etc.,
separadas por ";").

Este NO es el mismo formato que usa la API (/search/processes) -- es un
export mas simple, sin datos de proveedores ni montos. Se usa solo para
rellenar el historial que la API no cubre (la corrida diaria solo trae los
ultimos 90 dias).

Como usar:
  1. Descargar/generar el CSV de cada anio desde el portal de la DNCP.
  2. Ponerlos en la carpeta data/historico_raw/ del repositorio (el nombre
     de archivo no importa, se procesan todos los .csv que haya ahi).
  3. Correr este script (localmente, o via el workflow
     'Importar historico' en GitHub Actions).

Regla de fusion con los datos ya existentes (data/procesos.json):
  - Si un proceso YA existe (por ejemplo porque la API ya lo trajo, con
    proveedores adjudicados y demas), el CSV historico NO lo pisa -- estos
    datos son mas pobres que los de la API, asi que solo se usan para
    RELLENAR huecos, nunca para reemplazar algo mejor que ya haya.
"""

import csv
import glob
import json
import os
import sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "procesos.json")
HISTORICO_DIR = os.path.join(DATA_DIR, "historico_raw")

# Prefijo real que usa la DNCP para sus OCID (confirmado en datos reales).
# Se arma un ocid sintetico con el numero de licitacion para que, si el
# mismo proceso aparece despues via la API, ambas fuentes compartan la
# misma clave y no se dupliquen.
OCID_PREFIJO = "ocds-03ad3f"


def parsear_fecha(valor: str) -> str:
    """Convierte 'dd/m/yyyy HH:MM' a 'yyyy-mm-dd' (formato que ya usa el
    dashboard para agrupar por mes). Devuelve '' si no se puede parsear."""
    if not valor:
        return ""
    for formato in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor.strip(), formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def procesar_csv(ruta: str) -> dict:
    """Lee un CSV historico y devuelve un dict {clave: registro_plano}."""
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
                "proveedores_adjudicados": "",  # no viene en este formato de CSV
                "cantidad_adjudicaciones": 1 if (fila.get("etapa_licitacion") or "").strip().lower() == "adjudicada" else 0,
                "cantidad_contratos": 0,
                "fuente": "csv_historico",
            }
    return registros


def cargar_datos_existentes() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                contenido = json.load(f)
            return contenido.get("procesos", {})
        except (json.JSONDecodeError, OSError):
            print("Aviso: no se pudo leer procesos.json existente, se arranca de cero.")
    return {}


def main():
    if not os.path.isdir(HISTORICO_DIR):
        print(f"No existe la carpeta {HISTORICO_DIR}. Creala y poné ahí los CSV historicos.")
        sys.exit(1)

    archivos = sorted(glob.glob(os.path.join(HISTORICO_DIR, "*.csv")))
    if not archivos:
        print(f"No se encontraron archivos .csv en {HISTORICO_DIR}.")
        sys.exit(1)

    procesos = cargar_datos_existentes()
    total_previo = len(procesos)

    agregados, actualizados, protegidos = 0, 0, 0

    for archivo in archivos:
        print(f"Procesando {archivo} ...")
        registros = procesar_csv(archivo)
        print(f"  {len(registros)} registros leidos.")

        for clave, registro in registros.items():
            existente = procesos.get(clave)
            if existente is None:
                procesos[clave] = registro
                agregados += 1
            elif existente.get("fuente") == "csv_historico":
                # Ya lo habiamos cargado nosotros mismos desde un CSV antes
                # (por ejemplo con un link roto de una version vieja de este
                # script) -- es seguro corregirlo con los datos actuales.
                procesos[clave] = registro
                actualizados += 1
            else:
                # Vino de la API (mas rico: proveedores, awards, etc.) -- no
                # se pisa nunca con datos de un CSV historico mas pobre.
                protegidos += 1

    salida = {
        "last_updated": datetime.now().date().isoformat(),
        "total_procesos": len(procesos),
        "procesos": procesos,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"\nListo. Antes: {total_previo} | Agregados: {agregados} | "
          f"Actualizados (corregidos): {actualizados} | "
          f"Protegidos (ya venian de la API, no tocados): {protegidos} | "
          f"Total ahora: {len(procesos)}")


if __name__ == "__main__":
    main()
