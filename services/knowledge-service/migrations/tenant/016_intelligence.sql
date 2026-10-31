-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-045 §2 y §4, fase D1): libro, credenciales y estado
-- de la pasarela. Esta migración no envía nada. El consumidor y el punto de paso
-- llegan después, y leen estas tablas.
--
-- `intelligence_send_log` es de solo inserción, con la misma retención que
-- `audit_log` (ADR-008): sin purga automática. Guarda la referencia, el nivel
-- enviado, el SHA-256 del cuerpo, el destino y el resultado. Nunca el cuerpo.
--
-- `intelligence_credentials` guarda secretos cifrados (AES-256-GCM). La KEK no
-- está en la base. El AAD es `tenant:{slug}:intelligence:{purpose}:{slot}`: una
-- fila copiada a otra entidad o a otra finalidad no descifra. `client_id` no es
-- secreto. Las finalidades son el cliente de servicio, las dos ranuras de la
-- clave HMAC de retorno (rotación) y la clave de seudónimo.
--
-- `intelligence_state` es una sola fila: huella de `capabilities` aceptada,
-- suspensión del envío y cursor de la carga inicial.
--
-- Idempotente. Rollback manual:
--   DROP TABLE IF EXISTS intelligence_send_log;
--   DROP TABLE IF EXISTS intelligence_credentials;
--   DROP TABLE IF EXISTS intelligence_state;
--   DROP FUNCTION IF EXISTS reject_intelligence_send_log_mutation();
--   DROP FUNCTION IF EXISTS reject_intelligence_send_log_truncate();

CREATE TABLE IF NOT EXISTS intelligence_send_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation    TEXT NOT NULL CHECK (operation IN ('push', 'purge')),
    record_ref   TEXT NOT NULL CHECK (char_length(record_ref) BETWEEN 1 AND 200),
    level        SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 3),
    body_sha256  TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    destination  TEXT NOT NULL CHECK (char_length(destination) BETWEEN 1 AND 500),
    result       TEXT NOT NULL CHECK (char_length(result) BETWEEN 1 AND 40),
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_intelligence_send_log_ref
    ON intelligence_send_log (record_ref, at DESC);

CREATE OR REPLACE FUNCTION reject_intelligence_send_log_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'intelligence_send_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_intelligence_send_log_immutable ON intelligence_send_log;
CREATE TRIGGER trg_intelligence_send_log_immutable
    BEFORE UPDATE OR DELETE ON intelligence_send_log
    FOR EACH ROW EXECUTE FUNCTION reject_intelligence_send_log_mutation();

-- TRUNCATE no dispara los triggers de fila.
CREATE OR REPLACE FUNCTION reject_intelligence_send_log_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'intelligence_send_log is append-only: TRUNCATE denegado';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_intelligence_send_log_no_truncate ON intelligence_send_log;
CREATE TRIGGER trg_intelligence_send_log_no_truncate
    BEFORE TRUNCATE ON intelligence_send_log
    FOR EACH STATEMENT EXECUTE FUNCTION reject_intelligence_send_log_truncate();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I.intelligence_send_log FROM orpycamcp_app',
            current_schema()
        );
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS intelligence_credentials (
    purpose      TEXT NOT NULL,
    slot         SMALLINT NOT NULL,
    client_id    TEXT,
    ciphertext   BYTEA NOT NULL,
    nonce        BYTEA NOT NULL,
    kek_version  INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (purpose, slot),
    CONSTRAINT chk_intelligence_credentials_purpose
        CHECK (purpose IN ('service_client', 'hmac_return', 'pseudonym')),
    CONSTRAINT chk_intelligence_credentials_slot
        CHECK (
            (purpose = 'hmac_return' AND slot IN (1, 2))
            OR (purpose IN ('service_client', 'pseudonym') AND slot = 1)
        ),
    CONSTRAINT chk_intelligence_credentials_kek
        CHECK (kek_version >= 1 AND octet_length(nonce) = 12),
    CONSTRAINT chk_intelligence_credentials_client
        CHECK (
            (purpose = 'service_client' AND client_id IS NOT NULL)
            OR (purpose <> 'service_client' AND client_id IS NULL)
        )
);

CREATE TABLE IF NOT EXISTS intelligence_state (
    id                   SMALLINT PRIMARY KEY DEFAULT 1,
    capabilities_sha256  TEXT,
    send_suspended       BOOLEAN NOT NULL DEFAULT false,
    backfill_cursor      TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_intelligence_state_singleton CHECK (id = 1),
    CONSTRAINT chk_intelligence_state_huella CHECK (
        capabilities_sha256 IS NULL OR capabilities_sha256 ~ '^[0-9a-f]{64}$'
    )
);

INSERT INTO intelligence_state (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
