-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Intelligence gateway (PPI): state, credentials, acceptance, meter and send log.

-- ── intelligence_state ────────────────────────────────────────────────────────

CREATE TABLE intelligence_state (
    id smallint DEFAULT 1 NOT NULL,
    capabilities_sha256 text,
    send_suspended boolean DEFAULT false NOT NULL,
    backfill_cursor text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    inventory_cursor text,
    CONSTRAINT chk_intelligence_state_huella CHECK (((capabilities_sha256 IS NULL) OR (capabilities_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT chk_intelligence_state_inventario CHECK (((inventory_cursor IS NULL) OR ((char_length(inventory_cursor) >= 1) AND (char_length(inventory_cursor) <= 512)))),
    CONSTRAINT chk_intelligence_state_singleton CHECK ((id = 1)),
    CONSTRAINT intelligence_state_pkey PRIMARY KEY (id)
);

INSERT INTO intelligence_state (id, capabilities_sha256, send_suspended, backfill_cursor, inventory_cursor) VALUES
    ('1', NULL, 'false', NULL, NULL);

-- ── intelligence_credentials ──────────────────────────────────────────────────

CREATE TABLE intelligence_credentials (
    purpose text NOT NULL,
    slot smallint NOT NULL,
    client_id text,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    kek_version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_intelligence_credentials_client CHECK ((((purpose = 'service_client'::text) AND (client_id IS NOT NULL)) OR ((purpose <> 'service_client'::text) AND (client_id IS NULL)))),
    CONSTRAINT chk_intelligence_credentials_kek CHECK (((kek_version >= 1) AND (octet_length(nonce) = 12))),
    CONSTRAINT chk_intelligence_credentials_purpose CHECK ((purpose = ANY (ARRAY['service_client'::text, 'hmac_return'::text, 'pseudonym'::text]))),
    CONSTRAINT chk_intelligence_credentials_slot CHECK ((((purpose = 'hmac_return'::text) AND (slot = ANY (ARRAY[1, 2]))) OR ((purpose = ANY (ARRAY['service_client'::text, 'pseudonym'::text])) AND (slot = 1)))),
    CONSTRAINT intelligence_credentials_pkey PRIMARY KEY (purpose, slot)
);

-- ── intelligence_level_acceptance ─────────────────────────────────────────────

CREATE TABLE intelligence_level_acceptance (
    level smallint NOT NULL,
    capabilities_sha256 text NOT NULL,
    accepted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT intelligence_level_acceptance_capabilities_sha256_check CHECK ((capabilities_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT intelligence_level_acceptance_level_check CHECK (((level >= 1) AND (level <= 3))),
    CONSTRAINT intelligence_level_acceptance_pkey PRIMARY KEY (level)
);

-- ── intelligence_meter ────────────────────────────────────────────────────────

CREATE TABLE intelligence_meter (
    kind text NOT NULL,
    reason text NOT NULL,
    n bigint DEFAULT 0 NOT NULL,
    CONSTRAINT chk_intelligence_meter_motivo CHECK ((((kind = 'barrier'::text) AND (reason = ANY (ARRAY['suspendido'::text, 'ausente'::text, 'nivel'::text]))) OR ((kind = 'fallback'::text) AND (reason = 'local'::text)))),
    CONSTRAINT chk_intelligence_meter_n CHECK ((n >= 0)),
    CONSTRAINT intelligence_meter_pkey PRIMARY KEY (kind, reason)
);

-- ── intelligence_send_log ─────────────────────────────────────────────────────

CREATE TABLE intelligence_send_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operation text NOT NULL,
    record_ref text NOT NULL,
    level smallint,
    body_sha256 text NOT NULL,
    destination text NOT NULL,
    result text NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT intelligence_send_log_body_sha256_check CHECK ((body_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT intelligence_send_log_destination_check CHECK (((char_length(destination) >= 1) AND (char_length(destination) <= 500))),
    CONSTRAINT intelligence_send_log_level_check CHECK (((level IS NULL) OR ((level >= 1) AND (level <= 3)))),
    CONSTRAINT intelligence_send_log_operation_check CHECK ((operation = ANY (ARRAY['push'::text, 'purge'::text]))),
    CONSTRAINT intelligence_send_log_record_ref_check CHECK (((char_length(record_ref) >= 1) AND (char_length(record_ref) <= 200))),
    CONSTRAINT intelligence_send_log_result_check CHECK (((char_length(result) >= 1) AND (char_length(result) <= 40))),
    CONSTRAINT intelligence_send_log_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_intelligence_send_log_ref ON intelligence_send_log USING btree (record_ref, at DESC);

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE intelligence_send_log FROM orpycamcp_app;

-- ── ppi_nonces ────────────────────────────────────────────────────────────────

CREATE TABLE ppi_nonces (
    nonce text NOT NULL,
    expira_at timestamp with time zone NOT NULL,
    CONSTRAINT ppi_nonces_pkey PRIMARY KEY (nonce)
);

COMMENT ON TABLE ppi_nonces IS 'PPI return-path nonces (one-use). Replaces Redis orpycamcp:ppi:nonce:*.';

CREATE INDEX ix_ppi_nonces_expira ON ppi_nonces USING btree (expira_at);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_intelligence_send_log_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'intelligence_send_log is append-only';
END;
$$;

CREATE FUNCTION reject_intelligence_send_log_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'intelligence_send_log is append-only: TRUNCATE denegado';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_intelligence_send_log_immutable BEFORE DELETE OR UPDATE ON intelligence_send_log FOR EACH ROW EXECUTE FUNCTION reject_intelligence_send_log_mutation();
CREATE TRIGGER trg_intelligence_send_log_no_truncate BEFORE TRUNCATE ON intelligence_send_log FOR EACH STATEMENT EXECUTE FUNCTION reject_intelligence_send_log_truncate();
