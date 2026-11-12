-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 005: `public` guarda SOLO lo global (ADR-002, addendum C-7 / SEG #12).
--
-- Las migraciones de tenant 005 (admin_audit), 006 (catálogos) y 007 (PINAR) corrieron
-- alguna vez con un `search_path` cuyo primer schema no existía: el CREATE TABLE cae en el
-- primero que SÍ existe, `public`. Quedaron 14 copias huérfanas de tablas de TENANT en
-- `public` (más la función de trigger de PINAR). Ninguna ruta las lee a propósito: con el
-- `search_path` de solo-el-tenant (C-7), una lectura sin tabla propia es
-- `UndefinedTable`, no un fallback silencioso a estas copias.
--
-- Lista estática: son nombres que solo existen en `migrations/tenant/`. Sin
-- compatibilidad con datos viejos: son copias, el dato vivo está en cada `tenant_*`.

DROP TABLE IF EXISTS
    public.pinar_seguimiento,
    public.pinar_priorizacion,
    public.pinar_proyecto_aspecto,
    public.pinar_proyecto,
    public.pinar_instrumento_ref,
    public.pinar_aspecto_critico,
    public.pinar_objetivo,
    public.pinar_eje,
    public.pinar_plan,
    public.mensajes_rapidos,
    public.soportes,
    public.formas_envio,
    public.causales,
    public.admin_audit
    CASCADE;

DROP FUNCTION IF EXISTS public.pinar_seguimiento_append_only();
