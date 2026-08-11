"""
Actualizacion automatica de datos DNCP -- version confirmada con la API real.

Este script corre en GitHub Actions todos los dias (ver
.github/workflows/actualizar_datos.yml). Al correr en los servidores de
GitHub, no depende de CORS ni de la red corporativa de nadie.

Historia de este script (por transparencia):
  v1: asumia un endpoint /search/processes con un prefijo de ruta
      incorrecto (api/v3/doc/search/processes con parametros inventados).
      No funcionaba.
  v2 (diagnostico): se cambio a descargar el ZIP masivo anual, ante la duda
      de si /search/processes existia.
  v3 (esta version): confirmado con el Swagger real de la DNCP
      (https://www.contrataciones.gov.py/datos/api/v3/doc/) que el
      endpoint /search/processes SI EXISTE, con esta lista de parametros
      real (confirmada por captura de pantalla del propio usuario):

        page, items_per_page, fecha_desde, fecha_hasta, tipo_fecha,
        ocid, tender.title, tender.procuringEntity.name,
        tender.statusDetails, tender.mainProcurementCategory,
        awards.suppliers.name, awards.suppliers.id, contracts.id,
        contracts.statusDetails, order, etc.

      tipo_fecha acepta: entrega_ofertas, adjudicacion, publicacion_llamado,
      firma_contrato, fecha_release.

Que hace este script:
  1. Se autentica contra la API (OAuth, token de 15 min).
  2. Llama a /search/processes filtrando por fecha_release en un rango de
     dias reciente (para capturar tanto procesos nuevos como cambios de
     estado en procesos existentes), paginando para no perder resultados.
  3. El resultado es un "record package" minimo de OCDS. Normaliza cada
     registro a un formato plano que el dashboard (index.html) puede leer
     directamente.
  4. Combina con lo que ya habia en data/procesos.json (no borra historial).
  5. Ademas guarda data/muestra_respuesta_busqueda.json con la respuesta
     cruda de la primera pagina, para poder ajustar el mapeo de campos si
     hace falta sin tener que volver a correr todo el workflow.
"""

import base64
import json
import os
import sys
from datetime import date, timedelta

import requests

SITE_BASE = "https://www.contrataciones.gov.py"
AUTH_BASE = f"{SITE_BASE}/datos"                  # /datos/oauth/token
API_BASE = f"{SITE_BASE}/datos/api/v3/doc"        # base confirmada por el Swagger v3

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "procesos.json")
MUESTRA_FILE = os.path.join(DATA_DIR, "muestra_respuesta_busqueda.json")

# Cuantos dias hacia atras se vuelve a consultar en cada corrida (por
# fecha_release), para capturar procesos que cambiaron de estado.
DIAS_SOLAPAMIENTO = 15

# En la primera corrida (sin datos previos) cuanto historial se trae.
DIAS_BACKFILL_INICIAL = 90

ITEMS_POR_PAGINA = 500
MAX_PAGINAS = 20  # tope de seguridad: 500 x 20 = 10.000, el limite documentado por la DNCP


def obtener_token(consumer_key: str, consumer_secret: str) -> str:
    request_token = base64.b64encode(
        f"{consumer_key}:{consumer_secret}".encode("utf-8")
    ).decode("utf-8")

    resp = requests.post(
        f"{AUTH_BASE}/oauth/token",
        headers={"Authorization": f"Basic {request_token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"No se pudo autenticar (HTTP {resp.status_code}): {resp.text[:300]}")

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("La respuesta de autenticacion no incluyo access_token.")
    return token


def buscar_pagina(token: str, params: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{API_BASE}/search/processes"
    resp = requests.get(url, headers=headers, params=params, timeout=45)

    if resp.status_code == 404:
        # Esta API parece devolver 404 cuando la busqueda no encuentra
        # resultados para el rango pedido, en vez de un 200 con lista vacia.
        # Se trata como "sin resultados" en vez de como un error fatal.
        print(f"  (sin resultados para este rango -- la API devolvio 404, se interpreta como lista vacia)")
        return {"records": []}

    if resp.status_code != 200:
        raise RuntimeError(f"Error HTTP {resp.status_code} en {url}: {resp.text[:500]}")

    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Respuesta no es JSON valido:\n{resp.text[:500]}")


def extraer_registros(pagina: dict):
    """La respuesta es un record package OCDS minimo. Probamos las formas
    mas comunes en que puede venir estructurado, sin asumir una unica."""
    if pagina is None:
        return []
    if isinstance(pagina, list):
        return pagina
    if isinstance(pagina, dict):
        for clave in ("records", "releases", "results", "data", "@graph"):
            if clave in pagina and isinstance(pagina[clave], list):
                return pagina[clave]
    return []


def get_in(d, *paths):
    """Intenta varias rutas posibles (cada una una lista de claves) sobre un
    dict anidado, y devuelve el primer valor no vacio que encuentre."""
    for path in paths:
        actual = d
        ok = True
        for clave in path:
            if isinstance(actual, list):
                actual = actual[0] if actual else None
            if isinstance(actual, dict) and clave in actual:
                actual = actual[clave]
            else:
                ok = False
                break
        if ok and actual not in (None, "", []):
            return actual
    return None


def normalizar(registro: dict) -> dict:
    """Aplana un record/release OCDS al esquema plano que usa el dashboard
    (index.html). Mapeo confirmado con una respuesta real de
    /search/processes (agosto 2026):

      - El numero corto de licitacion (6 digitos) esta en
        compiledRelease.planning.identifier -- NO en tender.id, que a veces
        viene como UUID interno en vez del formato clasico "NNNNNN-titulo".
      - No hay montos (value/amount) en esta respuesta -- ver aviso en
        MUESTRA_FILE. Se deja vacio por ahora.
    """

    release = registro.get("compiledRelease") if isinstance(registro.get("compiledRelease"), dict) else registro

    ocid = registro.get("ocid") or release.get("ocid") or ""
    tender = release.get("tender", {}) if isinstance(release.get("tender"), dict) else {}
    planning = release.get("planning", {}) if isinstance(release.get("planning"), dict) else {}
    buyer = release.get("buyer", {}) if isinstance(release.get("buyer"), dict) else {}
    awards = release.get("awards", [])
    if not isinstance(awards, list):
        awards = [awards] if awards else []
    contracts = release.get("contracts", [])
    if not isinstance(contracts, list):
        contracts = [contracts] if contracts else []

    # El numero de licitacion "lindo" (6 digitos): primero planning.identifier
    # (siempre numerico), si no esta se intenta sacar del ocid, y como ultimo
    # recurso se usa tender.id (que a veces es un UUID, mejor que nada).
    numero_licitacion = planning.get("identifier") or ""
    if not numero_licitacion and ocid:
        # ocid tiene forma "ocds-03ad3f-405062" o "ocds-03ad3f-405062-1"
        partes = ocid.split("-")
        numeros = [p for p in partes if p.isdigit()]
        numero_licitacion = numeros[0] if numeros else ""

    proveedores = []
    for a in awards:
        if isinstance(a, dict):
            for s in a.get("suppliers", []):
                if isinstance(s, dict) and s.get("name"):
                    proveedores.append(s["name"])

    plano = {
        "ocid": ocid,
        "id_llamado": numero_licitacion or tender.get("id") or ocid,
        "tender_id_completo": tender.get("id") or "",
        "nombre_licitacion": tender.get("title") or "",
        "convocante": get_in(tender, ["procuringEntity", "name"]) or buyer.get("name") or "",
        "estado": tender.get("statusDetails") or "",
        "categoria": tender.get("mainProcurementCategoryDetails") or "",
        "fecha_publicacion": get_in(tender, ["tenderPeriod", "startDate"]) or release.get("date") or "",
        "modalidad": tender.get("procurementMethodDetails") or "",
        "cantidad_adjudicaciones": len(awards),
        "cantidad_contratos": len(contracts),
        "proveedores_adjudicados": ", ".join(sorted(set(proveedores))) if proveedores else "",
        # Todavia no confirmado si /search/processes trae montos en algun
        # caso -- por ahora queda vacio. Ver charla sobre como resolverlo
        # (llamada adicional por contrato/adjudicacion, o via CSV masivo).
        "monto_adjudicado": None,
    }
    return plano


def cargar_datos_existentes() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                contenido = json.load(f)
            return contenido.get("procesos", {})
        except (json.JSONDecodeError, OSError):
            print("Aviso: no se pudo leer el archivo existente, se arranca de cero.")
    return {}


def main():
    consumer_key = os.environ.get("DNCP_CONSUMER_KEY")
    consumer_secret = os.environ.get("DNCP_CONSUMER_SECRET")

    if not consumer_key or not consumer_secret:
        print("ERROR: faltan las variables de entorno DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        print("Configuralas como 'secrets' del repositorio en GitHub "
              "(Settings > Secrets and variables > Actions).")
        sys.exit(1)

    print("Autenticando contra la API de la DNCP...")
    token = obtener_token(consumer_key, consumer_secret)
    print("Token obtenido (valido 15 minutos).")

    procesos = cargar_datos_existentes()
    es_primera_corrida = len(procesos) == 0
    dias = DIAS_BACKFILL_INICIAL if es_primera_corrida else DIAS_SOLAPAMIENTO

    fecha_desde = str(date.today() - timedelta(days=dias))
    fecha_hasta = str(date.today())

    print(f"Buscando procesos con fecha_release entre {fecha_desde} y {fecha_hasta}"
          f" ({'backfill inicial' if es_primera_corrida else 'actualizacion incremental'})...")

    nuevos, actualizados, total_recibidos = 0, 0, 0
    pagina_num = 1
    primera_respuesta_guardada = False

    while pagina_num <= MAX_PAGINAS:
        params = {
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "tipo_fecha": "fecha_release",
            "page": pagina_num,
            "items_per_page": ITEMS_POR_PAGINA,
            "order": "date desc",
        }

        try:
            respuesta = buscar_pagina(token, params)
        except RuntimeError as e:
            print(f"ERROR en pagina {pagina_num}: {e}")
            if pagina_num == 1:
                # Si falla ya en la primera pagina, no tiene sentido seguir.
                sys.exit(1)
            break

        if not primera_respuesta_guardada:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(MUESTRA_FILE, "w", encoding="utf-8") as f:
                json.dump(respuesta, f, ensure_ascii=False, indent=2)
            primera_respuesta_guardada = True

        registros = extraer_registros(respuesta)
        print(f"Pagina {pagina_num}: {len(registros)} registro(s).")

        if not registros:
            break

        total_recibidos += len(registros)

        for registro in registros:
            if not isinstance(registro, dict):
                continue
            plano = normalizar(registro)
            clave = plano["ocid"] or plano["id_llamado"]
            if not clave:
                continue
            if clave in procesos:
                actualizados += 1
            else:
                nuevos += 1
            procesos[clave] = plano

        if len(registros) < ITEMS_POR_PAGINA:
            break  # ultima pagina

        pagina_num += 1

    salida = {
        "last_updated": date.today().isoformat(),
        "total_procesos": len(procesos),
        "procesos": procesos,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"\nListo. Recibidos: {total_recibidos} | Nuevos: {nuevos} | "
          f"Actualizados: {actualizados} | Total acumulado: {len(procesos)}")
    print(f"Muestra de la respuesta cruda guardada en {MUESTRA_FILE} "
          "(revisala si algun campo del dashboard aparece vacio).")


if __name__ == "__main__":
    main()
