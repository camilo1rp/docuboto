-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Idempotency keys of the public counter.

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
