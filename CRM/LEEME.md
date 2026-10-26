# CRM · Llamados en puerta + canal privado

Página: https://dariosilvapy.github.io/Licitaciones/CRM/

- La página (`CRM/index.html`) se publica con el mismo GitHub Pages del repo.
- Los datos del CRM viven en **Supabase**, no en el repositorio: el repo es público y
  cuentas, contactos y montos no deben quedar a la vista. Solo entran las personas
  de la tabla `miembros`, con su email y contraseña.
- `scripts/actualizar_llamados_crm.py` actualiza los llamados desde la API de la DNCP
  (usa `dncp_core.py`). Corre solo con `.github/workflows/actualizar_llamados_crm.yml`:
  cada 30 minutos procesa lo pedido desde la página, y de lunes a viernes a las 11:50
  revisa todos los llamados activos. Si hay novedades, avisa por Telegram.

## Instalación (una sola vez)

### 1. Base de datos en Supabase
1. En supabase.com: **New project**. Región sugerida: **South America (São Paulo)**.
   Guarde la contraseña de la base en un lugar seguro.
2. **SQL Editor → New query**: pegue el contenido de `supabase/esquema_crm.sql`.
   **Antes de correrlo**, cambie los correos de ejemplo del final por los reales de
   cada persona. Botón **Run**.
3. **Authentication → Sign In / Providers**: desactive **Allow new users to sign up**
   (los usuarios los crea usted, nadie puede registrarse solo).
4. **Authentication → URL Configuration**:
   - Site URL: `https://dariosilvapy.github.io/Licitaciones/CRM/`
   - Redirect URLs: agregue la misma dirección.
   (Sirve para el enlace de "¿Olvidó su contraseña?").

### 2. Usuarios
Por cada persona: **Authentication → Users → Add user → Create new user**,
con su email (el mismo de la tabla `miembros`), una contraseña inicial y
**Auto Confirm User** marcado. Al entrar, cada uno puede cambiarla desde
"Cambiar contraseña".

Para sumar a alguien más adelante: créele el usuario y agregue una fila en
**Table Editor → miembros** (email, nombre, rol). Para dar de baja: `activo = false`.

Roles: `asesor` (carga cuentas, oportunidades y visitas), `gerencia` (además
responde los pedidos a gerencia), `admin` (igual que gerencia).

### 3. Conectar la página
En `CRM/config.js` pegue, desde **Project Settings → API**:
- `supabaseUrl`: el **Project URL**
- `supabaseAnonKey`: la clave **anon public**

### 4. Subir al repositorio
`Add file → Upload files`, respetando las carpetas:
```
CRM/index.html
CRM/config.js
CRM/LEEME.md
scripts/actualizar_llamados_crm.py
.github/workflows/actualizar_llamados_crm.yml
supabase/esquema_crm.sql
```

### 5. Secrets de GitHub
`Settings → Secrets and variables → Actions → New repository secret`:
- `SUPABASE_URL`: el Project URL
- `SUPABASE_SERVICE_KEY`: la clave **service_role** (Project Settings → API).
  Es secreta: solo va acá, nunca en `config.js` ni en ningún archivo.

Los secrets de la DNCP y de Telegram ya existen y se reutilizan.

### 6. Probar la conexión con la DNCP
`Actions → Llamados en puerta (CRM) → Run workflow`, y en **probar_link** pegue el
link de un llamado. No guarda nada: muestra en el log lo que devuelve la API y lo
que se cargaría en la página.

## Notas
- Las licitaciones **no reguladas** no están en la API de la DNCP: se pueden cargar,
  pero sus fechas se completan a mano en la ficha.
- Supabase gratis pausa los proyectos sin uso por 7 días; el workflow cada 30 minutos
  lo mantiene activo.
