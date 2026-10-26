-- =====================================================================
-- Esquema del CRM (Llamados en puerta + CRM canal privado)
-- Pegar completo en Supabase > SQL Editor > New query > Run.
-- Se puede volver a correr sin problema (no borra datos).
-- =====================================================================

-- 1) Miembros del equipo: SOLO las personas de esta lista pueden ver y
--    editar datos, aunque alguien consiga crear un usuario por su cuenta.
create table if not exists public.miembros (
  email      text primary key,
  nombre     text not null,
  rol        text not null default 'asesor' check (rol in ('asesor','gerencia','admin')),
  activo     boolean not null default true,
  creado     timestamptz not null default now()
);

create or replace function public.es_miembro() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.miembros
    where lower(email) = lower(auth.jwt() ->> 'email') and activo
  );
$$;

alter table public.miembros enable row level security;
drop policy if exists "miembros se ven entre si" on public.miembros;
create policy "miembros se ven entre si" on public.miembros
  for select to authenticated using (public.es_miembro());
-- Altas, bajas y cambios de rol se hacen desde el Table Editor de Supabase.

-- 2) Tablas de datos. Cada fila es un documento: id + data (JSON).
do $$
declare t text;
begin
  foreach t in array array['llamados','crm_cuentas','crm_oportunidades','crm_visitas'] loop
    execute format('create table if not exists public.%I (
      id text primary key,
      data jsonb not null default ''{}''::jsonb,
      actualizado timestamptz not null default now())', t);
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I replica identity full', t);

    execute format('drop policy if exists "miembros leen" on public.%I', t);
    execute format('drop policy if exists "miembros crean" on public.%I', t);
    execute format('drop policy if exists "miembros editan" on public.%I', t);
    execute format('drop policy if exists "miembros borran" on public.%I', t);
    execute format('create policy "miembros leen" on public.%I for select to authenticated using (public.es_miembro())', t);
    execute format('create policy "miembros crean" on public.%I for insert to authenticated with check (public.es_miembro())', t);
    execute format('create policy "miembros editan" on public.%I for update to authenticated using (public.es_miembro()) with check (public.es_miembro())', t);
    execute format('create policy "miembros borran" on public.%I for delete to authenticated using (public.es_miembro())', t);

    -- Cambios en vivo: lo que carga un asesor aparece al instante en las demás pantallas
    begin
      execute format('alter publication supabase_realtime add table public.%I', t);
    exception when duplicate_object then null;
    end;
  end loop;
end $$;

-- 3) Actualización parcial de un documento (equivale a "actualizar solo estos campos")
create or replace function public.merge_doc(p_table text, p_id text, p_patch jsonb)
returns jsonb language plpgsql security invoker set search_path = public as $$
declare r jsonb;
begin
  if p_table not in ('llamados','crm_cuentas','crm_oportunidades','crm_visitas') then
    raise exception 'Tabla no permitida: %', p_table;
  end if;
  execute format('update public.%I set data = data || $1, actualizado = now() where id = $2 returning data', p_table)
    into r using p_patch, p_id;
  if r is null then
    raise exception 'El documento % no existe o no tiene permiso', p_id using errcode = 'P0002';
  end if;
  return r;
end $$;
revoke execute on function public.merge_doc(text, text, jsonb) from public, anon;
grant execute on function public.merge_doc(text, text, jsonb) to authenticated, service_role;

-- 4) Cargar al equipo. CAMBIAR los correos por los reales antes de correr.
--    rol: 'asesor' carga oportunidades y visitas; 'gerencia' además responde
--    los pedidos a gerencia; 'admin' igual que gerencia.
insert into public.miembros (email, nombre, rol) values
  ('CORREO-DE-DARIO@ejemplo.com',    'Darío Silva',      'admin'),
  ('CORREO-DE-RIGO@ejemplo.com',     'Rigo Laterra',     'asesor'),
  ('CORREO-DE-JULIA@ejemplo.com',    'Julia Irigoitia',  'asesor'),
  ('CORREO-DE-JULIO@ejemplo.com',    'Julio Pintos',     'asesor'),
  ('CORREO-DE-GUADALUPE@ejemplo.com','Guadalupe Ayala',  'asesor')
on conflict (email) do update set nombre = excluded.nombre, rol = excluded.rol, activo = true;
