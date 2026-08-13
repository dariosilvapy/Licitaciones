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
        if registro.get("enriquecido") and registro.get("award_ids") and registro.get("monto_adjudicado") is None:
            registro["enriquecido"] = False
            registro.pop("proveedores_montos", None)
            registro.pop("monto_estimado", None)
            registro.pop("enriquecimiento_nota", None)
            revertidos += 1

    core.guardar_datos(procesos)
    print(f"Revertidos {revertidos} registro(s). Van a volver a procesarse en la proxima corrida de enriquecer_detalle.py.")


if __name__ == "__main__":
    main()
