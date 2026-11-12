-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- archive-service schema: expedientes, TRD series, and sequence tables.
-- All tables live inside the tenant schema (SET search_path is applied per session).

CREATE TABLE IF NOT EXISTS expediente_sequences (
    year     INTEGER NOT NULL PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS expedientes (
    id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    code         VARCHAR(50) NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    description  TEXT,
    trd_serie_id UUID,
    status       VARCHAR(20) NOT NULL DEFAULT 'open',
    opened_by    UUID,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_expedientes_code   ON expedientes (code);
CREATE INDEX IF NOT EXISTS ix_expedientes_status ON expedientes (status);

CREATE TABLE IF NOT EXISTS expediente_radicados (
    expediente_id   UUID        NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    radicado_id     UUID        NOT NULL,
    tracking_number VARCHAR(50) NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (expediente_id, radicado_id)
);

CREATE INDEX IF NOT EXISTS ix_exp_rad_radicado_id ON expediente_radicados (radicado_id);

CREATE TABLE IF NOT EXISTS trd_series (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    code            VARCHAR(20) NOT NULL UNIQUE,
    name            TEXT        NOT NULL,
    description     TEXT,
    retention_years INTEGER     NOT NULL DEFAULT 5,
    total_retention INTEGER     NOT NULL DEFAULT 10,
    disposition     VARCHAR(20) NOT NULL DEFAULT 'eliminate',
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_trd_series_code ON trd_series (code);
