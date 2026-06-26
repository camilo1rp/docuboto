-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 003: audit_log hardening (D-03)
--
-- Endurece la inmutabilidad de public.audit_log en tres capas:
--   1. CHECK enum cerrado sobre el campo `canal` → la BD rechaza valores no registrados.
--   2. Rol de aplicación orpycamcp_app con permisos mínimos (INSERT + SELECT).
--      REVOKE de operaciones destructivas desde PUBLIC.
--   3. Trigger BEFORE TRUNCATE en tabla padre y partición por defecto → segunda
--      barrera que protege incluso a roles que excedan los privilegios habituales.
--
-- NOTA OPERATIVA: en producción los servicios deben conectar como `orpycamcp_app`,
-- no como el superusuario `orpycamcp`. Ver docs/es/deployment.md §credenciales.

-- ---------------------------------------------------------------------------
-- 1. Enum CHECK en canal — valores válidos: api | ui | mcp | system
--    - api    : acceso REST directo (default)
--    - ui     : petición originada en el frontend SvelteKit (derivado por gateway)
--    - mcp    : petición originada en el MCP-server (derivado por gateway)
--    - system : operación interna de un job/worker (sin interacción de usuario)
--
-- Idempotente: el DO-block comprueba pg_constraint antes de intentar el ALTER.
-- El CHECK en la tabla particionada aplica a todas sus particiones (pg >= 12).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname    = 'chk_audit_log_canal'
          AND  conrelid   = 'public.audit_log'::regclass
    ) THEN
        ALTER TABLE public.audit_log
            ADD CONSTRAINT chk_audit_log_canal
            CHECK (canal IN ('api', 'ui', 'mcp', 'system'));
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. Rol de aplicación y permisos mínimos
--    El rol orpycamcp_app es el rol con el que deben conectar los microservicios
--    en producción. Tiene sólo INSERT + SELECT sobre audit_log; no puede
--    actualizar, borrar ni truncar.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        -- Contraseña placeholder: DEBE cambiarse antes del primer despliegue productivo.
        CREATE ROLE orpycamcp_app LOGIN PASSWORD 'CHANGE_IN_PRODUCTION';
    END IF;
END;
$$;

-- Permisos mínimos para la tabla padre y la partición por defecto existente.
-- Las particiones mensuales que cree el job (ADR-008) deben recibir los mismos
-- permisos; el runner de migraciones se encarga de ello al crearlas.
GRANT USAGE ON SCHEMA public TO orpycamcp_app;
GRANT INSERT, SELECT ON TABLE public.audit_log         TO orpycamcp_app;
GRANT INSERT, SELECT ON TABLE public.audit_log_default TO orpycamcp_app;

-- REVOKE de operaciones destructivas desde PUBLIC.
-- Esto afecta a cualquier rol que no sea superusuario (los superusuarios
-- omiten los chequeos de permisos, de ahí la tercera capa: el trigger).
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log         FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log_default FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 3. Trigger anti-TRUNCATE
--    Los triggers BEFORE TRUNCATE no se heredan automáticamente en tablas
--    particionadas (a diferencia de los triggers de fila), de modo que se
--    instala tanto en la tabla padre como en la partición por defecto.
--    El job de creación de particiones mensuales debe replicar este trigger
--    en cada nueva partición.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.reject_audit_log_truncate()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'TRUNCATE on public.audit_log is prohibited (ADR-008, Ley 594/2000, D-03)';
END;
$$;

-- Tabla padre: bloquea TRUNCATE que intente vaciar la tabla completa o sus
-- particiones a través del nodo raíz.
DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON public.audit_log;
CREATE TRIGGER trg_audit_log_no_truncate
    BEFORE TRUNCATE ON public.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();

-- Partición por defecto: bloquea TRUNCATE directo sobre la partición.
DROP TRIGGER IF EXISTS trg_audit_log_default_no_truncate ON public.audit_log_default;
CREATE TRIGGER trg_audit_log_default_no_truncate
    BEFORE TRUNCATE ON public.audit_log_default
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();
