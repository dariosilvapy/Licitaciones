# Dashboard DNCP — Contrataciones Públicas (Paraguay)

Dashboard interactivo que se actualiza solo, todos los días, con datos de
licitaciones, convocantes, montos y estados desde la API de Datos Abiertos
de la DNCP.

**Cómo funciona:**
- Un script en Python (`scripts/fetch_dncp.py`) corre automáticamente en los
  servidores de GitHub (no en tu PC) una vez al día, autentica contra la API
  de la DNCP y actualiza `data/procesos.json`.
- `index.html` es el dashboard: lee ese archivo y muestra KPIs, gráficos
  (Chart.js) y una tabla filtrable/ordenable/exportable.
- GitHub Pages publica `index.html` en una URL gratuita, que ves actualizada
  automáticamente cada vez que el script corre.

Todo gratis, sin instalar nada en tu computadora.

---

## Instalación (una sola vez)

### 1. Crear el repositorio

1. Andá a [github.com](https://github.com) e iniciá sesión (o creá una
   cuenta gratis).
2. Botón verde **"New"** (repositorio nuevo).
3. Nombre sugerido: `dncp-dashboard`. Puede ser público o privado — ambos
   son gratis y ambos permiten GitHub Pages.
4. Creá el repositorio (no hace falta marcar ninguna opción extra).

### 2. Subir los archivos

En la página del repositorio recién creado:

1. Click en **"uploading an existing file"** (o `Add file` > `Upload files`).
2. Arrastrá **todos** los archivos y carpetas que te compartí, manteniendo
   la misma estructura:
   ```
   .github/workflows/actualizar_datos.yml
   scripts/fetch_dncp.py
   data/procesos.json
   index.html
   README.md
   ```
   Si al arrastrar la carpeta `.github` no conserva la estructura, creála a
   mano: `Add file` > `Create new file`, escribí como nombre
   `.github/workflows/actualizar_datos.yml` (GitHub crea las carpetas solo al
   escribir las barras `/`), y pegá el contenido del archivo.
3. Confirmá el commit ("Commit changes").

### 3. Configurar tus credenciales (sin exponerlas nunca en el código)

1. En el repositorio: `Settings` > `Secrets and variables` > `Actions`.
2. `New repository secret`:
   - Nombre: `DNCP_CONSUMER_KEY` → valor: tu Consumer Key.
   - Nombre: `DNCP_CONSUMER_SECRET` → valor: tu Consumer Secret.
3. Estos valores quedan encriptados — ni vos ni nadie los vuelve a ver en
   texto plano, y nunca aparecen en los logs de ejecución.

### 4. Activar GitHub Pages

1. `Settings` > `Pages`.
2. En "Source" elegí `Deploy from a branch`.
3. Branch: `main`, carpeta `/ (root)`.
4. Guardar. En 1-2 minutos te va a dar una URL tipo
   `https://tu-usuario.github.io/dncp-dashboard/` — esa es tu dashboard.

### 5. Correr la primera actualización manualmente

No hace falta esperar al cron de todos los días para la primera carga:

1. Pestaña `Actions` del repositorio.
2. Click en el workflow **"Actualizar datos DNCP"**.
3. Botón `Run workflow` > `Run workflow`.
4. Esperá 1-2 minutos, refrescá, y deberías ver el check verde ✅.
5. Entrá a tu URL de GitHub Pages — ya debería mostrar datos.

A partir de ahí, corre solo todos los días a las 09:00 UTC (podés cambiar
la hora editando el `cron` en `.github/workflows/actualizar_datos.yml`).

---

## Personalización

- **Frecuencia de actualización**: editá la línea `cron` en el workflow.
  Formato: minuto hora día mes día-semana, siempre en UTC.
- **Rango de días que se re-consultan**: variable `DIAS_SOLAPAMIENTO` en
  `scripts/fetch_dncp.py` (por defecto 10 días, para capturar cambios de
  estado en procesos recientes).
- **Historial inicial**: variable `DIAS_BACKFILL_INICIAL` (por defecto 60
  días) — solo aplica la primera vez que corre, cuando no hay datos previos.

## Si algo falla

- **El workflow falla en GitHub Actions**: entrá a la pestaña `Actions`,
  abrí la corrida fallida, y mirá el log — casi siempre es un secret mal
  configurado o un cambio en la API.
- **El dashboard muestra "Todavía no hay datos"**: significa que
  `data/procesos.json` sigue vacío — corré el workflow manualmente (paso 5).
- **Querés parar la automatización**: `Settings` > `Actions` > `General` >
  deshabilitar Actions, o simplemente borrá el archivo del workflow.
