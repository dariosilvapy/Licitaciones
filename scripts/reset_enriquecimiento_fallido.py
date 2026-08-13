"""
Uso unico: revierte el flag "enriquecido" en los registros que quedaron mal
marcados por el bug de extraccion de montos (la respuesta de /awards/{id}
venia como {"awards": [...]} y el script buscaba en el lugar equivocado,
asi que todo quedo con monto_adjudicado = None pero enriquecido = True).

Despues de correr esto, el proximo "Enriquecer detalle" los va a volver a
tomar con la logica ya corregida.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core


def main():
    procesos = core.cargar_datos_existentes()

    revertidos = 0
    for clave, registro in procesos.items():
        if not registro.get("enriquecido"):
            continue

        tiene_award_ids = bool(registro.get("award_ids"))
        monto_nulo = registro.get("monto_adjudicado") is None
        proveedores_montos = registro.get("proveedores_montos") or []
        falta_moneda = any("moneda" not in pm for pm in proveedores_montos) if proveedores_montos else False

        # Casos a revertir:
        #  a) tiene award_ids pero monto null (bug original de extraccion), o
        #  b) fue marcado "sin award_ids" y ahora SI tiene award_ids porque
        #     se re-corrio el backfill de su rango de fechas, o
        #  c) tiene montos guardados de una version vieja que no separaba
        #     guaranies de dolares (mezclados en una sola suma sin sentido).
        if (tiene_award_ids and monto_nulo) or falta_moneda:
            registro["enriquecido"] = False
            registro.pop("proveedores_montos", None)
            registro.pop("monto_estimado", None)
            registro.pop("monto_adjudicado_gs", None)
            registro.pop("monto_adjudicado_usd", None)
            registro.pop("enriquecimiento_nota", None)
            revertidos += 1

    core.guardar_datos(procesos)
    print(f"Revertidos {revertidos} registro(s). Van a volver a procesarse en la proxima corrida de enriquecer_detalle.py.")


if __name__ == "__main__":
    main()
