-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Webhook subscriptions and their deliveries.

-- ── webhook_subscription ──────────────────────────────────────────────────────

CREATE TABLE webhook_subscription (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    url text NOT NULL,
    event_types text[] DEFAULT '{}'::text[] NOT NULL,
    secret text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    secret_cifrado bytea,
    secret_nonce bytea,
    secret_kek_version integer,
    CONSTRAINT chk_webhook_secret_cifrado_completo CHECK ((((secret_cifrado IS NULL) AND (secret_nonce IS NULL) AND (secret_kek_version IS NULL)) OR ((secret_cifrado IS NOT NULL) AND (secret_nonce IS NOT NULL) AND (secret_kek_version IS NOT NULL) AND (secret_kek_version >= 1)))),
    CONSTRAINT chk_webhook_secret_un_solo_formato CHECK (((secret IS NULL) OR (secret_cifrado IS NULL))),
    CONSTRAINT webhook_subscription_pkey PRIMARY KEY (id)
);

ALTER TABLE webhook_subscription
    ADD CONSTRAINT chk_webhook_secret_sin_claro CHECK ((secret IS NULL));

COMMENT ON COLUMN webhook_subscription.secret IS 'OBSOLETO: secreto en claro heredado. Debe quedar siempre NULL; ver secret_cifrado.';

COMMENT ON COLUMN webhook_subscription.secret_cifrado IS 'Secreto HMAC cifrado con AES-256-GCM; AAD tenant:{slug}:webhook:{id}:secret.';

CREATE INDEX ix_webhook_active ON webhook_subscription USING btree (active);

-- ── webhook_entregas ──────────────────────────────────────────────────────────

CREATE TABLE webhook_entregas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entrega_id uuid NOT NULL,
    fase character varying(9) NOT NULL,
    webhook_id uuid NOT NULL,
    url_destino text NOT NULL,
    url_sha256 character(64) NOT NULL,
    event_id text,
    event_type text NOT NULL,
    intento smallint NOT NULL,
    body_sha256 character(64) NOT NULL,
    recortado boolean DEFAULT false NOT NULL,
    firmado boolean DEFAULT false NOT NULL,
    resultado character varying(24),
    http_status smallint,
    error character varying(500),
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_webhook_entregas_fase_resultado CHECK (((((fase)::text = 'envio'::text) AND (resultado IS NULL)) OR (((fase)::text = 'resultado'::text) AND (resultado IS NOT NULL)))),
    CONSTRAINT chk_webhook_entregas_resultado CHECK (((resultado IS NULL) OR (resultado IN ('entregado', 'http_error', 'error_red', 'destino_rechazado', 'secreto_inutilizable')))),
    CONSTRAINT webhook_entregas_fase_check CHECK ((fase IN ('envio', 'resultado'))),
    CONSTRAINT webhook_entregas_intento_check CHECK ((intento >= 1)),
    CONSTRAINT webhook_entregas_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_webhook_entregas_event ON webhook_entregas USING btree (event_id);
CREATE INDEX ix_webhook_entregas_webhook ON webhook_entregas USING btree (webhook_id, at DESC);

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE webhook_entregas FROM orpycamcp_app;

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_webhook_entregas_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'webhook_entregas is append-only (traza tecnica de entregas, inmutable)';
END;
$$;

CREATE FUNCTION reject_webhook_entregas_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'webhook_entregas is append-only: TRUNCATE denegado';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_webhook_entregas_immutable BEFORE DELETE OR UPDATE ON webhook_entregas FOR EACH ROW EXECUTE FUNCTION reject_webhook_entregas_mutation();
CREATE TRIGGER trg_webhook_entregas_no_truncate BEFORE TRUNCATE ON webhook_entregas FOR EACH STATEMENT EXECUTE FUNCTION reject_webhook_entregas_truncate();
