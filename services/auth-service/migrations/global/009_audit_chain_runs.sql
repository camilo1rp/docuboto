-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- 009 — Resultado de cada corrida de `app.ops.verify_audit_chains` (C18, ADR-041).
--
-- `/metrics` de auth-service NO ejecuta `verify_chain` (recorre el registro
-- entero): lee la última corrida COMPLETA que dejó el CLI de respaldo/cron aquí.
-- Es un agregado sin datos de tenant por fila, y NO es un asiento de la cadena:
-- escribirlo en `audit_log` movería el extremo que `backup.sh` ancla en la misma
-- corrida.
--
-- Solo se añade: una corrida pasada no se corrige ni se borra. `alcance` separa
-- la verificación completa (cuenta) de la parcial (`--slug` / `--excluir-prefijo`,
-- NO cuenta: verificó menos de lo que dice). Idempotente.

CREATE TABLE IF NOT EXISTS public.audit_chain_runs (
    id                 BIGSERIAL PRIMARY KEY,
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ NOT NULL,
    alcance            TEXT        NOT NULL CHECK (alcance IN ('completo', 'parcial')),
    origen             TEXT        NOT NULL CHECK (origen IN ('backup', 'restore', 'cron', 'manual')),
    tenants            INT         NOT NULL CHECK (tenants >= 0),
    asientos           BIGINT      NOT NULL CHECK (asientos >= 0),
    roturas_nuevas     INT         NOT NULL CHECK (roturas_nuevas >= 0),
    roturas_declaradas INT         NOT NULL CHECK (roturas_declaradas >= 0),
    retrocesos         INT         NOT NULL CHECK (retrocesos >= 0),
    anclas_alteradas   INT         NOT NULL CHECK (anclas_alteradas >= 0),
    codigo_salida      SMALLINT    NOT NULL
);

-- Lo único que consulta /metrics: la última corrida completa.
CREATE INDEX IF NOT EXISTS ix_audit_chain_runs_completo
    ON public.audit_chain_runs (id DESC) WHERE alcance = 'completo';

-- Las `DEFAULT PRIVILEGES` de 008 conceden UPDATE/DELETE a toda tabla nueva del
-- propietario; aquí solo se añade (INSERT + SELECT). Mismo criterio que audit_log.
-- No se toca el rol: solo esta tabla. Condicional por si el rol aún no existe.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_chain_runs FROM orpycamcp_app;
    END IF;
END;
$$;
