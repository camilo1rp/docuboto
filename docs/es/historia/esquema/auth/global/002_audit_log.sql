-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 002: audit_log canónico (ADR-008 + ADR-010)
-- Tabla transversal en `public`, append-only, particionada por mes, con cadena
-- de hash por tenant. La escribe la librería compartida `orpycamcp_common.audit`.
-- auth-service la crea por ser el servicio dueño del dominio de seguridad (E08).

CREATE TABLE IF NOT EXISTS public.audit_log (
    id           BIGINT      GENERATED ALWAYS AS IDENTITY,
    tenant_slug  TEXT        NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    service      TEXT        NOT NULL,            -- 'auth-service', 'document-service', ...
    actor        TEXT,                            -- user_id (o NULL / 'system')
    canal        TEXT        NOT NULL DEFAULT 'api',  -- 'api' | 'ui' | 'mcp'
    action       TEXT        NOT NULL,            -- 'auth.login_success', 'radicado.creado', ...
    object_type  TEXT        NOT NULL,
    object_ref   TEXT,                            -- tracking/uuid del objeto
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    TEXT,                            -- hash de la fila previa de ESTE tenant
    hash         TEXT        NOT NULL,            -- sha256(prev_hash || contenido_canónico)
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- Partición por defecto: captura todo hasta que un job cree particiones mensuales
-- (ADR-008). Garantiza que los INSERT no fallen por falta de partición.
CREATE TABLE IF NOT EXISTS public.audit_log_default
    PARTITION OF public.audit_log DEFAULT;

-- Índices de trazabilidad: por tenant+tiempo (consulta y verificación de cadena)
-- y por objeto.
CREATE INDEX IF NOT EXISTS ix_audit_tenant_ts ON public.audit_log (tenant_slug, ts);
CREATE INDEX IF NOT EXISTS ix_audit_object    ON public.audit_log (object_type, object_ref);

-- Inmutabilidad: rechaza UPDATE y DELETE (segunda barrera además de los permisos
-- del rol de aplicación). Los row-triggers en la tabla particionada se aplican a
-- todas sus particiones (PostgreSQL >= 11).
CREATE OR REPLACE FUNCTION public.reject_audit_log_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'public.audit_log is append-only (Ley 594/2000, ADR-008)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON public.audit_log;
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON public.audit_log
    FOR EACH ROW EXECUTE FUNCTION public.reject_audit_log_mutation();

-- Deprecación de la auditoría previa (ADR-010): auth-service deja de escribir en
-- `auth_audit_logs` y pasa a `audit_log`. La tabla vieja se conserva en
-- solo-lectura para histórico y se retirará en una limpieza posterior.
COMMENT ON TABLE public.auth_audit_logs IS
    'OBSOLETA desde ADR-010 (2026-06): auth-service audita en public.audit_log. '
    'Solo-lectura para histórico; pendiente de retirar.';
