"""
Uso unico (por casos puntuales): revierte el flag "enriquecido" en
registros que quedaron mal marcados por bugs ya corregidos:
  a) tiene award_ids pero monto null (bug de extraccion de /awards/{id}), o
  b) fue marcado "sin award_ids" y ahora SI tiene (se re-corrio el backfill), o
  c) tiene montos guardados sin separar moneda (version vieja).

Guarda particionado por anio (ver dncp_core.py).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core


def main():
    almacen = core.AlmacenParticionado()
    revertidos = 0

    for anio, clave, registro in almacen.iterar_todos():
        if not registro.get("enriquecido"):
            continue

        tiene_award_ids = bool(registro.get("award_ids"))
        monto_nulo = registro.get("monto_adjudicado") is None
        proveedores_montos = registro.get("proveedores_montos") or []
        falta_moneda = any("moneda" not in pm for pm in proveedores_montos) if proveedores_montos else False

        if (tiene_award_ids and monto_nulo) or falta_moneda:
            registro["enriquecido"] = False
            registro.pop("proveedores_montos", None)
            registro.pop("monto_estimado", None)
            registro.pop("monto_adjudicado_gs", None)
            registro.pop("monto_adjudicado_usd", None)
            registro.pop("monto_adjudicado", None)
            registro.pop("enriquecimiento_nota", None)
            almacen.actualizar_registro(anio, clave, registro)
            revertidos += 1

    almacen.guardar_cambios()
    print(f"Revertidos {revertidos} registro(s). Van a volver a procesarse en la proxima corrida de enriquecer_detalle.py.")


if __name__ == "__main__":
    main()
