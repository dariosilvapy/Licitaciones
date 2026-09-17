"""
Prueba de conexion y ruteo de email. Manda un mensaje de prueba a CADA
correo que aparece en data/reglas_alertas.txt, indicando con que palabras
clave quedo asociado -- asi confirmas de una sola vez que:
  1. La conexion SMTP con Gmail funciona (GMAIL_USER / GMAIL_APP_PASSWORD).
  2. Cada direccion de reglas_alertas.txt recibe su correo.
  3. El ruteo (que palabras van a que correo) es el que vos configuraste.

Se dispara a mano desde la pestana Actions ("Probar Email").
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import dncp_core as core
from notificar_telegram import enviar_email


def main():
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        print("ERROR: falta GMAIL_USER o GMAIL_APP_PASSWORD en los secrets.")
        sys.exit(1)

    config_reglas = core.cargar_reglas_alertas()
    reglas = config_reglas["reglas"]
    exclusiones = config_reglas["exclusiones"]
    if not reglas:
        print("ERROR: data/reglas_alertas.txt no tiene ninguna regla cargada (o no existe).")
        sys.exit(1)

    print(f"{len(reglas)} regla(s) encontrada(s) en reglas_alertas.txt.")
    if exclusiones:
        print(f"Exclusiones globales: {', '.join(exclusiones)}")
    print()

    # Agrupar por correo: un correo puede aparecer en mas de una regla.
    correo_a_palabras = {}
    for regla in reglas:
        for correo in regla["correos"]:
            correo_a_palabras.setdefault(correo, [])
            correo_a_palabras[correo].extend(regla["palabras"])

    exitosos, con_error = 0, 0

    for correo, palabras in correo_a_palabras.items():
        palabras_unicas = sorted(set(palabras))
        cuerpo = f"""
        <html><body style="font-family:sans-serif;">
        <h2>✅ Prueba de conexión — Alertas DNCP</h2>
        <p>Este es un correo de prueba para confirmar que la ruta de alertas hacia
        <b>{correo}</b> está funcionando correctamente.</p>
        <p>Este correo recibe avisos cuando una licitación coincide con alguna de estas
        palabras clave:</p>
        <ul>{"".join(f"<li>{p}</li>" for p in palabras_unicas)}</ul>
        <p>A partir de ahora vas a recibir un correo real cada vez que se publique una
        licitación nueva que coincida con estos términos (chequeo cada 2 horas).</p>
        </body></html>
        """
        try:
            enviar_email(gmail_user, gmail_password, correo,
                         "Prueba de conexión — Alertas DNCP", cuerpo)
            print(f"OK: enviado a {correo} (palabras: {', '.join(palabras_unicas)})")
            exitosos += 1
        except Exception as e:
            print(f"ERROR enviando a {correo}: {e}")
            con_error += 1

    print(f"\nListo. Exitosos: {exitosos} | Con error: {con_error}")
    if con_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
