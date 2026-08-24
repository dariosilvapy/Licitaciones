"""
Migracion de una sola vez: convierte el data/procesos.json monolitico viejo
en el nuevo esquema particionado por anio (data/procesos/{anio}.json +
data/indice.json). Necesario porque el archivo unico supero el limite de
100MB de GitHub y los pushes empezaron a fallar.

Despues de correr esto, data/procesos.json ya no se usa mas -- se deja
vacio (placeholder chico) para no confundir, o se puede borrar del repo.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core

LEGACY_FILE = core.DATA_FILE_LEGACY


def main():
    if not os.path.exists(LEGACY_FILE):
        print(f"No existe {LEGACY_FILE}. Nada que migrar (ya estas en el esquema nuevo, o es la primera vez).")
        return

    print(f"Leyendo {LEGACY_FILE} ...")
    with open(LEGACY_FILE, "r", encoding="utf-8") as f:
        contenido = json.load(f)

    procesos = contenido.get("procesos", {})
    print(f"{len(procesos)} registros encontrados en el archivo viejo.")

    if not procesos:
        print("El archivo viejo esta vacio. Nada que migrar.")
        return

    almacen = core.AlmacenParticionado()
    por_anio_conteo = {}

    for clave, registro in procesos.items():
        anio = core.anio_de_registro(registro)
        por_anio_conteo[anio] = por_anio_conteo.get(anio, 0) + 1
        almacen.actualizar_registro(anio, clave, registro)

    print("\nDistribucion por anio:")
    for anio in sorted(por_anio_conteo.keys()):
        print(f"  {anio}: {por_anio_conteo[anio]} registro(s)")

    almacen.guardar_cambios()

    # Se vacia el archivo viejo (no se borra el path para no romper nada
    # que todavia lo referencie por accidente, pero queda inofensivo).
    with open(LEGACY_FILE, "w", encoding="utf-8") as f:
        json.dump({"migrado": True, "nota": "Los datos ahora viven en data/procesos/{anio}.json"}, f, ensure_ascii=False, indent=2)

    print(f"\nMigracion completa. {len(procesos)} registros repartidos en {len(por_anio_conteo)} archivo(s) por anio.")
    print(f"{LEGACY_FILE} quedo vacio (placeholder).")


if __name__ == "__main__":
    main()
