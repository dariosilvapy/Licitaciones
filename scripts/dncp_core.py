"""
Modulo compartido: funciones de autenticacion, busqueda y normalizacion
contra la API de Datos Abiertos DNCP. Lo usan tanto la actualizacion diaria
(fetch_dncp.py) como el chequeo de alertas cada 2 horas
(notificar_telegram.py), para no duplicar la misma logica dos veces.
"""

import base64
import json
import os
from datetime import datetime

import requests

SITE_BASE = "https://www.contrataciones.gov.py"
AUTH_BASE = f"{SITE_BASE}/datos"
API_BASE = f"{SITE_BASE}/datos/api/v3/doc"

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "procesos.json")

OCID_PREFIJO = "ocds-03ad3f"
ITEMS_POR_PAGINA = 500
MAX_PAGINAS = 20  # 500 x 20 = 10.000, limite documentado por la DNCP


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
        # Esta API devuelve 404 cuando la busqueda no encuentra resultados
        # para el rango pedido, en vez de un 200 con lista vacia.
        return {"records": []}

    if resp.status_code != 200:
        raise RuntimeError(f"Error HTTP {resp.status_code} en {url}: {resp.text[:500]}")

    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Respuesta no es JSON valido:\n{resp.text[:500]}")


def extraer_registros(pagina: dict):
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


def solo_fecha(valor: str) -> str:
    """Recorta un datetime ISO ('2026-06-18T16:13:25-04:00') a solo la
    fecha ('2026-06-18'). Si ya viene sin hora, lo deja igual."""
    if not valor:
        return ""
    return str(valor)[:10]


def normalizar(registro: dict) -> dict:
    """Aplana un record/release OCDS al esquema plano que usa el dashboard."""

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

    numero_licitacion = planning.get("identifier") or ""
    if not numero_licitacion and ocid:
        partes = ocid.split("-")
        numeros = [p for p in partes if p.isdigit()]
        numero_licitacion = numeros[0] if numeros else ""

    proveedores = []
    for a in awards:
        if isinstance(a, dict):
            for s in a.get("suppliers", []):
                if isinstance(s, dict) and s.get("name"):
                    proveedores.append(s["name"])

    tender_id_completo = tender.get("id") or ""
    # Link directo a la convocatoria en el portal (solo es un link valido si
    # tender_id_completo es el slug clasico y no un UUID interno).
    link = f"{SITE_BASE}/datos/id/convocatorias/{tender_id_completo}" if tender_id_completo else ""

    tender_period = tender.get("tenderPeriod", {}) if isinstance(tender.get("tenderPeriod"), dict) else {}

    return {
        "ocid": ocid,
        "id_llamado": numero_licitacion or tender_id_completo or ocid,
        "tender_id_completo": tender_id_completo,
        "nombre_licitacion": tender.get("title") or "",
        "convocante": get_in(tender, ["procuringEntity", "name"]) or buyer.get("name") or "",
        "estado": tender.get("statusDetails") or "",
        "categoria": tender.get("mainProcurementCategoryDetails") or "",
        "fecha_publicacion": solo_fecha(tender_period.get("startDate") or release.get("date")),
        "fecha_apertura_ofertas": solo_fecha(tender_period.get("endDate")),
        "modalidad": tender.get("procurementMethodDetails") or "",
        "cantidad_adjudicaciones": len(awards),
        "cantidad_contratos": len(contracts),
        "proveedores_adjudicados": ", ".join(sorted(set(proveedores))) if proveedores else "",
        "link": link,
    }


def cargar_datos_existentes() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                contenido = json.load(f)
            return contenido.get("procesos", {})
        except (json.JSONDecodeError, OSError):
            print("Aviso: no se pudo leer procesos.json existente, se arranca de cero.")
    return {}


def guardar_datos(procesos: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    salida = {
        "last_updated": datetime.now().date().isoformat(),
        "total_procesos": len(procesos),
        "procesos": procesos,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)


def buscar_todo(token: str, fecha_desde: str, fecha_hasta: str, guardar_muestra_en: str = None) -> list:
    """Pagina /search/processes para el rango de fechas dado y devuelve la
    lista completa de registros crudos (sin normalizar)."""
    todos = []
    pagina_num = 1
    primera_guardada = False

    while pagina_num <= MAX_PAGINAS:
        params = {
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "tipo_fecha": "fecha_release",
            "page": pagina_num,
            "items_per_page": ITEMS_POR_PAGINA,
            "order": "date desc",
        }
        respuesta = buscar_pagina(token, params)

        if guardar_muestra_en and not primera_guardada:
            os.makedirs(os.path.dirname(guardar_muestra_en), exist_ok=True)
            with open(guardar_muestra_en, "w", encoding="utf-8") as f:
                json.dump(respuesta, f, ensure_ascii=False, indent=2)
            primera_guardada = True

        registros = extraer_registros(respuesta)
        print(f"  Pagina {pagina_num}: {len(registros)} registro(s).")

        if not registros:
            break

        todos.extend(registros)

        if len(registros) < ITEMS_POR_PAGINA:
            break

        pagina_num += 1

    return todos
