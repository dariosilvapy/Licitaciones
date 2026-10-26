"""
Actualiza los "Llamados en puerta" de la pagina CRM (tabla `llamados` en
Supabase) con los datos de la API de Datos Abiertos DNCP.

Reutiliza dncp_core.py (autenticacion y URL de la API) y, si estan
configurados, los avisos de Telegram de notificar_telegram.py.

Modos (variable CRM_MODO):
  - pendientes: solo los llamados que alguien pidio actualizar desde la
                pagina (boton "Actualizar") o que se acaban de cargar.
  - completo:   todos los llamados activos (no cerrados) con link.

Prueba sin tocar la base (variable CRM_PROBAR_LINK): consulta un llamado,
imprime la respuesta cruda de la API y lo que se guardaria. Sirve para
verificar el formato real de la API antes de usar el modo normal.

Que detecta y avisa (queda como "Novedad" en la ficha del llamado):
  cambio de estado, cambio de fechas/etapas, nuevas publicaciones o
  adendas, consultas respondidas y cambio del monto estimado.

Las licitaciones NO reguladas no estan en la API de la DNCP: se marcan con
un mensaje y sus fechas se cargan a mano en la ficha.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

MODO = os.environ.get("CRM_MODO", "pendientes").strip().lower()
PROBAR_LINK = os.environ.get("CRM_PROBAR_LINK", "").strip()
SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
PAGINA_CRM = os.environ.get("CRM_URL_PAGINA", "")

ESTADOS_CERRADOS = {"Adjudicado", "No adjudicado", "Desierto / Cancelado", "Descartado"}
PAUSA = 0.3
RENOVAR_TOKEN_CADA = 40


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Supabase (API REST, con la clave service_role guardada como secret)
# ---------------------------------------------------------------------------

def sb_headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def sb_listar_llamados() -> list:
    filas, desde = [], 0
    while True:
        resp = requests.get(f"{SB_URL}/rest/v1/llamados", headers={**sb_headers(), "Range": f"{desde}-{desde + 999}"},
                            params={"select": "id,data"}, timeout=30)
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"Supabase: no se pudo leer llamados (HTTP {resp.status_code}): {resp.text[:300]}")
        lote = resp.json()
        filas.extend(lote)
        if len(lote) < 1000:
            return filas
        desde += 1000


def sb_actualizar(doc_id: str, cambios: dict):
    resp = requests.post(f"{SB_URL}/rest/v1/rpc/merge_doc", headers=sb_headers(),
                         json={"p_table": "llamados", "p_id": doc_id, "p_patch": cambios}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Supabase: no se pudo guardar {doc_id} (HTTP {resp.status_code}): {resp.text[:300]}")


# ---------------------------------------------------------------------------
# DNCP
# ---------------------------------------------------------------------------

def slug_de_link(link: str):
    """Devuelve (tipo, slug). El slug de una convocatoria regulada es el
    mismo id que usa la API en /tender/{id}."""
    m = re.search(r"/convocatoria/([^/?#]+?)(?:\.html)?(?:[/?#]|$)", link or "")
    if m:
        return "regulada", m.group(1)
    m = re.search(r"/(?:licitaciones-no-reguladas|licitacion_no_regulada_ley_2051)/([^/?#]+?)(?:\.html)?(?:[/?#]|$)", link or "")
    if m:
        return "no-regulada", m.group(1)
    return None, None


def consultar_tender(token: str, slug: str):
    tid = urllib.parse.quote(slug, safe="")
    resp = requests.get(f"{core.API_BASE}/tender/{tid}", headers={"Authorization": f"Bearer {token}"}, timeout=40)
    if resp.status_code == 404:
        return None, "La DNCP no encontró este llamado (HTTP 404). Revise el link."
    if resp.status_code != 200:
        return None, f"La API de la DNCP respondió HTTP {resp.status_code}."
    try:
        return resp.json(), None
    except Exception:
        return None, "La API de la DNCP no devolvió datos válidos."


def fecha_hora(valor):
    s = str(valor or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return "", ""
    hora = s[11:16] if len(s) >= 16 and s[10] in "T " else ""
    return s[:10], ("" if hora == "00:00" and len(s) <= 19 else hora)


def como_lista(x):
    if isinstance(x, list):
        return [i for i in x if isinstance(i, dict)]
    return [x] if isinstance(x, dict) else []


def formatear_monto(valor, moneda):
    if valor in (None, ""):
        return ""
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    txt = f"{n:,.0f}".replace(",", ".")
    return f"USD {txt}" if (moneda or "").upper() == "USD" else f"Gs. {txt}"


def construir_dncp(datos: dict, slug: str) -> dict:
    """Convierte la respuesta de /tender/{id} al formato que muestra la pagina."""
    datos = datos if isinstance(datos, dict) else {}
    t = datos.get("tender") if isinstance(datos.get("tender"), dict) else {}

    etapas = []

    def agregar(nombre, valor):
        f, h = fecha_hora(valor)
        if f and not any(e["etapa"] == nombre and e["fecha"] == f for e in etapas):
            etapas.append({"etapa": nombre, "fecha": f, "hora": h})

    agregar("Tope de consultas", core.get_in(t, ["enquiryPeriod", "endDate"]))
    agregar("Entrega de ofertas", core.get_in(t, ["tenderPeriod", "endDate"]))
    agregar("Apertura de ofertas", core.get_in(t, ["bidOpening", "date"]))
    for m in como_lista(t.get("milestones")):
        agregar(m.get("title") or m.get("description") or m.get("type") or "Etapa", m.get("dueDate") or m.get("date"))
    etapas.sort(key=lambda e: (e["fecha"], e["hora"] or "99"))

    publicaciones = []
    for d in como_lista(t.get("documents")):
        publicaciones.append({
            "id": str(d.get("id") or d.get("url") or d.get("title") or ""),
            "tipo": d.get("documentTypeDetails") or d.get("documentType") or "Documento",
            "archivo": d.get("title") or "",
            "fecha": fecha_hora(d.get("datePublished"))[0],
        })
    for a in como_lista(t.get("amendments")):
        publicaciones.append({
            "id": "enmienda-" + str(a.get("id") or a.get("date") or ""),
            "tipo": "Adenda",
            "archivo": a.get("description") or a.get("rationale") or "",
            "fecha": fecha_hora(a.get("date"))[0],
        })

    consultas = como_lista(t.get("enquiries")) or como_lista(datos.get("enquiries"))
    valor = t.get("value") if isinstance(t.get("value"), dict) else {}

    ocid = str(datos.get("ocid") or "")
    id_llamado = core.get_in(datos, ["planning", "identifier"]) or next((p for p in ocid.split("-") if p.isdigit()), "")

    return {
        "titulo": t.get("title") or "",
        "idLlamado": str(id_llamado or ""),
        "modalidad": t.get("procurementMethodDetails") or t.get("procurementMethod") or "",
        "convocante": core.get_in(t, ["procuringEntity", "name"]) or core.get_in(datos, ["buyer", "name"]) or "",
        "estado": t.get("statusDetails") or t.get("status") or "",
        "montoEstimado": formatear_monto(valor.get("amount"), valor.get("currency")),
        "publicaciones": publicaciones,
        "consultasPublicadas": len(consultas) if consultas else None,
        "consultasRespondidas": len([c for c in consultas if c.get("answer")]) if consultas else None,
        "etapas": etapas,
        "slug": slug,
    }


def detectar_novedades(viejo: dict, nuevo: dict) -> list:
    """Compara lo guardado con lo nuevo. En la primera importacion no avisa nada,
    y tampoco cuando lo guardado viene de la version anterior (claude.ai), que
    nombraba distinto las etapas y publicaciones: ahi solo se toma como base."""
    if not viejo or "slug" not in viejo:
        return []
    nov = []

    def n(tipo, texto):
        nov.append({"id": uuid.uuid4().hex[:12], "tipo": tipo, "texto": texto, "fecha": ahora_iso(), "vista": False})

    if (viejo.get("estado") or "") != (nuevo.get("estado") or "") and nuevo.get("estado"):
        n("estado", f"Estado: {viejo.get('estado') or '—'} → {nuevo['estado']}")

    antes = {e["etapa"]: e for e in viejo.get("etapas") or []}
    for e in nuevo.get("etapas") or []:
        a = antes.get(e["etapa"])
        if a and (a.get("fecha"), a.get("hora")) != (e.get("fecha"), e.get("hora")):
            fa = "/".join(reversed(a["fecha"].split("-"))) + (f" {a['hora']}" if a.get("hora") else "")
            fn = "/".join(reversed(e["fecha"].split("-"))) + (f" {e['hora']}" if e.get("hora") else "")
            n("fecha", f"{e['etapa']}: {fa} → {fn}")
        elif not a and antes:
            n("fecha", f"Nueva etapa: {e['etapa']} el {'/'.join(reversed(e['fecha'].split('-')))}")

    ids_antes = {p.get("id") for p in viejo.get("publicaciones") or []}
    for p in nuevo.get("publicaciones") or []:
        if p.get("id") not in ids_antes:
            n("adenda", f"{p.get('tipo') or 'Publicación'}: {p.get('archivo') or 'sin título'}")

    ra, rn = viejo.get("consultasRespondidas") or 0, nuevo.get("consultasRespondidas") or 0
    if rn > ra:
        n("consultas", f"{rn - ra} consulta{'s' if rn - ra > 1 else ''} respondida{'s' if rn - ra > 1 else ''} nueva{'s' if rn - ra > 1 else ''}")

    if viejo.get("montoEstimado") and nuevo.get("montoEstimado") and viejo["montoEstimado"] != nuevo["montoEstimado"]:
        n("monto", f"Monto estimado: {viejo['montoEstimado']} → {nuevo['montoEstimado']}")
    return nov


def campos_principales(llamado: dict, dncp: dict) -> dict:
    """Fechas de la lista y del calendario + datos vacios que se completan solos."""
    c = {}
    por_nombre = {e["etapa"]: e for e in dncp["etapas"]}
    for etapa, campo in (("Tope de consultas", "TopeConsultas"), ("Entrega de ofertas", "Entrega"), ("Apertura de ofertas", "Apertura")):
        e = por_nombre.get(etapa)
        if e:
            c["fecha" + campo], c["hora" + campo] = e["fecha"], e.get("hora", "")
    if "fechaApertura" not in c and "fechaEntrega" in c:
        c["fechaApertura"], c["horaApertura"] = c["fechaEntrega"], c.get("horaEntrega", "")
    if dncp.get("idLlamado"):
        c["idDncp"] = dncp["idLlamado"]
    if not llamado.get("convocante") and dncp.get("convocante"):
        c["convocante"] = dncp["convocante"]
    if not llamado.get("objeto") and dncp.get("titulo"):
        c["objeto"] = dncp["titulo"]
    if dncp.get("montoEstimado"):
        c["montoEstimado"] = dncp["montoEstimado"]
    return c


# ---------------------------------------------------------------------------
# Avisos por Telegram (opcional)
# ---------------------------------------------------------------------------

def avisar_telegram(resumen: list):
    if not (TG_TOKEN and TG_CHAT and resumen):
        return
    try:
        from notificar_telegram import enviar_telegram, dividir_en_mensajes
    except Exception as e:
        print(f"Telegram: no se pudo importar notificar_telegram ({e}).")
        return
    bloques = []
    for titulo, link, novedades in resumen:
        b = f"📋 <b>Llamado en puerta: {titulo}</b>\n" + "".join(f"• {x['texto']}\n" for x in novedades)
        if link:
            b += f"DNCP: {link}\n"
        bloques.append(b)
    if PAGINA_CRM:
        bloques.append(f"Ver en el CRM: {PAGINA_CRM}")
    for m in dividir_en_mensajes(bloques):
        try:
            enviar_telegram(TG_TOKEN, TG_CHAT, m)
        except Exception as e:
            print(f"Telegram: error al enviar ({e}).")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

def probar(token: str):
    tipo, slug = slug_de_link(PROBAR_LINK)
    print(f"Link: {PROBAR_LINK}\nTipo: {tipo} | id: {slug}")
    if tipo != "regulada":
        print("Solo las convocatorias reguladas están en la API.")
        return
    datos, error = consultar_tender(token, slug)
    if error:
        print("ERROR:", error)
        return
    crudo = json.dumps(datos, ensure_ascii=False, indent=2)
    print("\n===== Respuesta cruda de la API (primeros 15000 caracteres) =====")
    print(crudo[:15000])
    print("\n===== Lo que se guardaría en la página =====")
    dncp = construir_dncp(datos, slug)
    print(json.dumps(dncp, ensure_ascii=False, indent=2))
    print(json.dumps(campos_principales({}, dncp), ensure_ascii=False, indent=2))


def main():
    ck, cs = os.environ.get("DNCP_CONSUMER_KEY"), os.environ.get("DNCP_CONSUMER_SECRET")
    if not ck or not cs:
        print("ERROR: faltan DNCP_CONSUMER_KEY / DNCP_CONSUMER_SECRET.")
        sys.exit(1)

    if PROBAR_LINK:
        probar(core.obtener_token(ck, cs))
        return

    if not SB_URL or not SB_KEY:
        print("ERROR: faltan los secrets SUPABASE_URL / SUPABASE_SERVICE_KEY.")
        sys.exit(1)

    filas = sb_listar_llamados()
    objetivo = []
    for f in filas:
        l = f.get("data") or {}
        if not l.get("link"):
            continue
        pendiente = (l.get("sync") or {}).get("estado") == "pendiente"
        if MODO == "completo":
            if pendiente or l.get("estado") not in ESTADOS_CERRADOS:
                objetivo.append((f["id"], l))
        elif pendiente:
            objetivo.append((f["id"], l))

    print(f"Modo: {MODO} | llamados en la base: {len(filas)} | a revisar: {len(objetivo)}")
    if not objetivo:
        print("Nada para revisar. Listo.")
        return

    token = core.obtener_token(ck, cs)
    llamadas, ok, errores, resumen_avisos = 0, 0, 0, []

    for doc_id, l in objetivo:
        if llamadas >= RENOVAR_TOKEN_CADA:
            token, llamadas = core.obtener_token(ck, cs), 0
        sync_previo = l.get("sync") or {}
        tipo, slug = slug_de_link(l.get("link"))
        try:
            if tipo == "no-regulada":
                sb_actualizar(doc_id, {"sync": {**sync_previo, "estado": "error", "revisado": ahora_iso(),
                               "mensaje": "las licitaciones no reguladas no están en la API de la DNCP; cargue las fechas a mano"}})
                print(f"  {doc_id}: no regulada (sin API).")
                continue
            if tipo != "regulada":
                sb_actualizar(doc_id, {"sync": {**sync_previo, "estado": "error", "revisado": ahora_iso(), "mensaje": "link no reconocido"}})
                errores += 1
                continue

            datos, error = consultar_tender(token, slug)
            llamadas += 1
            time.sleep(PAUSA)
            if error:
                sb_actualizar(doc_id, {"sync": {**sync_previo, "estado": "error", "revisado": ahora_iso(), "mensaje": error}})
                print(f"  {doc_id}: {error}")
                errores += 1
                continue

            dncp = construir_dncp(datos, slug)
            novedades = detectar_novedades(l.get("dncp") or {}, dncp)
            cambios = {"dncp": dncp, "sync": {"estado": "ok", "revisado": ahora_iso()}, **campos_principales(l, dncp)}
            if novedades:
                cambios["alertas"] = (l.get("alertas") or []) + novedades
                titulo = l.get("numero") or (f"ID {dncp['idLlamado']}" if dncp.get("idLlamado") else doc_id)
                resumen_avisos.append((f"{titulo} · {l.get('convocante') or dncp.get('convocante') or ''}", l.get("link"), novedades))
            sb_actualizar(doc_id, cambios)
            ok += 1
            print(f"  {doc_id}: ok ({len(novedades)} novedad(es)).")
        except Exception as e:
            errores += 1
            print(f"  {doc_id}: ERROR {e}")

    avisar_telegram(resumen_avisos)
    print(f"\nListo. Actualizados: {ok} | Con error: {errores} | Con novedades: {len(resumen_avisos)}")


if __name__ == "__main__":
    main()
