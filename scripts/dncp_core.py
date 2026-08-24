"""
Modulo compartido: autenticacion, busqueda, normalizacion, Y ALMACENAMIENTO
PARTICIONADO POR ANIO contra la API de Datos Abiertos DNCP.

Por que particionado por anio (en vez de un solo data/procesos.json):
con decenas de miles de registros, ese archivo unico supero el limite de
100MB que impone GitHub para un commit normal, y los pushes empezaron a
fallar (perdiendo el trabajo de esa corrida). La solucion es un archivo por
anio en data/procesos/{anio}.json (bucketeado por el anio de
fecha_publicacion), mas un data/indice.json chico con el resumen de que
anios existen y cuantos registros tiene cada uno -- asi ningun archivo
individual crece sin limite, y ademas cada corrida solo lee/escribe los
anios que realmente toco (no todo el historico completo cada vez).
"""

import base64
import json
import os
from datetime import date

import requests

SITE_BASE = "https://www.contrataciones.gov.py"
AUTH_BASE = f"{SITE_BASE}/datos"
API_BASE = f"{SITE_BASE}/datos/api/v3/doc"

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PROCESOS_DIR = os.path.join(DATA_DIR, "procesos")
INDICE_FILE = os.path.join(DATA_DIR, "indice.json")

# Compatibilidad: el archivo unico viejo, solo se usa para la migracion.
DATA_FILE_LEGACY = os.path.join(DATA_DIR, "procesos.json")

OCID_PREFIJO = "ocds-03ad3f"
ITEMS_POR_PAGINA = 500
MAX_PAGINAS = 20  # 500 x 20 = 10.000, limite documentado por la DNCP

ANIO_SIN_FECHA = "otros"  # bucket para registros sin fecha_publicacion valida


# ---------------------------------------------------------------------------
# Autenticacion y busqueda (sin cambios respecto a versiones anteriores)
# ---------------------------------------------------------------------------

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
    award_ids = []
    for a in awards:
        if isinstance(a, dict):
            if a.get("id"):
                award_ids.append(a["id"])
            for s in a.get("suppliers", []):
                if isinstance(s, dict) and s.get("name"):
                    proveedores.append(s["name"])

    tender_id_completo = tender.get("id") or ""
    link = f"{SITE_BASE}/licitaciones/convocatoria/{tender_id_completo}.html" if tender_id_completo else ""

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
        "award_ids": award_ids,
    }


# ---------------------------------------------------------------------------
# Enriquecimiento: campos que se preservan en un upsert
# ---------------------------------------------------------------------------

CAMPOS_ENRIQUECIMIENTO = [
    "enriquecido", "monto_adjudicado", "monto_adjudicado_gs", "monto_adjudicado_usd",
    "monto_estimado", "proveedores_montos", "fecha_apertura_real", "enriquecimiento_nota",
]


def combinar_con_enriquecimiento(existente, nuevo: dict) -> dict:
    resultado = dict(nuevo)
    if existente:
        for campo in CAMPOS_ENRIQUECIMIENTO:
            if campo in existente:
                resultado[campo] = existente[campo]
    return resultado


# ---------------------------------------------------------------------------
# Almacenamiento particionado por anio
# ---------------------------------------------------------------------------

def anio_de_registro(registro: dict) -> str:
    fecha = (registro or {}).get("fecha_publicacion") or ""
    if len(fecha) >= 4 and fecha[:4].isdigit():
        return fecha[:4]
    return ANIO_SIN_FECHA


def ruta_anio(anio: str) -> str:
    return os.path.join(PROCESOS_DIR, f"{anio}.json")


def listar_anios_existentes() -> list:
    if not os.path.isdir(PROCESOS_DIR):
        return []
    return sorted(
        f[:-5] for f in os.listdir(PROCESOS_DIR)
        if f.endswith(".json")
    )


def cargar_datos_anio(anio: str) -> dict:
    path = ruta_anio(anio)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("procesos", {})
        except (json.JSONDecodeError, OSError):
            print(f"Aviso: no se pudo leer {path}, se arranca vacio para ese anio.")
    return {}


def guardar_datos_anio(anio: str, procesos_anio: dict):
    os.makedirs(PROCESOS_DIR, exist_ok=True)
    with open(ruta_anio(anio), "w", encoding="utf-8") as f:
        json.dump({
            "anio": anio,
            "last_updated": date.today().isoformat(),
            "total": len(procesos_anio),
            "procesos": procesos_anio,
        }, f, ensure_ascii=False, indent=2)
    _actualizar_indice_entrada(anio, len(procesos_anio))


def _actualizar_indice_entrada(anio: str, total: int):
    indice = {}
    if os.path.exists(INDICE_FILE):
        try:
            with open(INDICE_FILE, "r", encoding="utf-8") as f:
                indice = json.load(f)
        except (json.JSONDecodeError, OSError):
            indice = {}

    anios = indice.get("anios", {})
    anios[anio] = {"total": total, "last_updated": date.today().isoformat()}
    indice["anios"] = anios
    indice["total_general"] = sum(a.get("total", 0) for a in anios.values())
    indice["actualizado"] = date.today().isoformat()

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INDICE_FILE, "w", encoding="utf-8") as f:
        json.dump(indice, f, ensure_ascii=False, indent=2)


class AlmacenParticionado:
    """Encapsula la lectura/escritura por anio, cacheando lo que ya se leyo
    en esta corrida y guardando SOLO los anios que se modificaron -- nunca
    reescribe un anio que no toco esta corrida."""

    def __init__(self):
        self._cache = {}
        self._sucios = set()

    def _obtener_bucket(self, anio: str) -> dict:
        if anio not in self._cache:
            self._cache[anio] = cargar_datos_anio(anio)
        return self._cache[anio]

    def upsert(self, registro_plano: dict):
        """Inserta o actualiza un registro. Devuelve (clave, existia_antes)."""
        anio = anio_de_registro(registro_plano)
        bucket = self._obtener_bucket(anio)
        clave = registro_plano.get("ocid") or registro_plano.get("id_llamado")
        if not clave:
            return None, False
        existia = clave in bucket
        bucket[clave] = combinar_con_enriquecimiento(bucket.get(clave), registro_plano)
        self._sucios.add(anio)
        return clave, existia

    def obtener_registro(self, anio: str, clave: str):
        return self._obtener_bucket(anio).get(clave)

    def actualizar_registro(self, anio: str, clave: str, registro_actualizado: dict):
        self._obtener_bucket(anio)[clave] = registro_actualizado
        self._sucios.add(anio)

    def marcar_sucio(self, anio: str):
        self._sucios.add(anio)

    def guardar_cambios(self):
        for anio in self._sucios:
            guardar_datos_anio(anio, self._cache[anio])
        self._sucios.clear()

    def iterar_todos(self):
        """Genera (anio, clave, registro) de TODO lo que hay guardado,
        cargando un anio a la vez. Util para recorridos globales
        (enriquecimiento, reset) sin asumir que todo cabe comodo en un
        solo archivo."""
        for anio in listar_anios_existentes():
            bucket = self._obtener_bucket(anio)
            for clave, registro in list(bucket.items()):
                yield anio, clave, registro

    def total_cargado(self) -> int:
        return sum(len(b) for b in self._cache.values())


# ---------------------------------------------------------------------------
# Busqueda paginada (sin cambios)
# ---------------------------------------------------------------------------

def buscar_todo(token: str, fecha_desde: str, fecha_hasta: str, guardar_muestra_en: str = None) -> list:
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
