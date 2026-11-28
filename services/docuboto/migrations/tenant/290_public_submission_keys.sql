-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Idempotency keys of the public counter and its one-use ALTCHA challenges.

-- ── public_submission_keys ────────────────────────────────────────────────────

CREATE TABLE public_submission_keys (
    key text NOT NULL,
    radicado_id uuid,
    huella text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT public_submission_keys_pkey PRIMARY KEY (key),
    CONSTRAINT public_submission_keys_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE
);

COMMENT ON TABLE public_submission_keys IS 'Idempotency-Key de la ventanilla pública (AREA-02-07): clave del cliente -> radicado.';

-- ── altcha_usados ─────────────────────────────────────────────────────────────

CREATE TABLE altcha_usados (
    challenge text NOT NULL,
    expira_en timestamp with time zone NOT NULL,
    CONSTRAINT altcha_usados_pkey PRIMARY KEY (challenge)
);

COMMENT ON TABLE altcha_usados IS 'Retos ALTCHA ya consumidos por la ventanilla pública (C16): cada uno sirve una vez (ADR-049).';

CREATE INDEX ix_altcha_usados_expira ON altcha_usados USING btree (expira_en);
