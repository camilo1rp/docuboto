-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 054: outbox of work that leaves the process (Docuboto 2.0).
--
-- `eventos_salientes` still publishes domain events to Redis. This table is the
-- claim loop for mail, webhooks, intelligence, veraPDF, render, and LTA. One
-- `event_id` is unique: a duplicate enqueue does not create a second delivery.
-- `cola` is `app` or `worker`. Failed rows keep `ultimo_error` so an admin can
-- release them.
--
-- Idempotent. Rollback: DROP TABLE IF EXISTS trabajo_fuera;

CREATE TABLE IF NOT EXISTS trabajo_fuera (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id       TEXT        NOT NULL UNIQUE,
    tipo           TEXT        NOT NULL,
    cola           TEXT        NOT NULL CHECK (cola IN ('app', 'worker')),
    tenant_slug    TEXT        NOT NULL,
    payload        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    creado_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reclamado_at   TIMESTAMPTZ,
    reclamado_por  TEXT,
    hecho_at       TIMESTAMPTZ,
    intentos       INTEGER     NOT NULL DEFAULT 0,
    ultimo_error   TEXT
);

CREATE INDEX IF NOT EXISTS ix_trabajo_fuera_pendientes
    ON trabajo_fuera (cola, id)
    WHERE hecho_at IS NULL;

COMMENT ON TABLE trabajo_fuera IS
    'Outbox of outside work (Docuboto 2.0): app vs worker claim by type.';
