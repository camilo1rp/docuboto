-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Dead-letter queue shared by the event consumers.

-- ── evento_dead_letter ────────────────────────────────────────────────────────

CREATE TABLE evento_dead_letter (
    event_id text NOT NULL,
    event_type text,
    correlation_id text,
    tenant_slug text NOT NULL,
    payload jsonb NOT NULL,
    failure jsonb NOT NULL,
    origin_stream text,
    origin_group text NOT NULL,
    dead_lettered_at timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT evento_dead_letter_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'resolved'::text, 'discarded'::text]))),
    CONSTRAINT evento_dead_letter_pkey PRIMARY KEY (origin_group, event_id)
);

COMMENT ON TABLE evento_dead_letter IS 'Copia durable (Postgres) de los eventos aparcados en el dead-letter de Redis. Tabla COMPARTIDA por los consumidores del tenant: la PK (origin_group, event_id) deja que cada grupo aparque el mismo evento una vez; repetir el mismo par es idempotente (ON CONFLICT (origin_group, event_id) DO NOTHING). El INSERT y el audit_log se escriben en la misma transacción (R-4, ADR-021).';

COMMENT ON COLUMN evento_dead_letter.payload IS 'Sobre íntegro del evento (event_id, event_type, tenant_slug, occurred_at, actor, payload de negocio). Autosuficiente para reconstrucción forense (Ley 594/2000 art. 19).';

COMMENT ON COLUMN evento_dead_letter.failure IS 'failure_meta del helper reclaim_stale: dead_letter_reason, dead_letter_last_error, dead_letter_delivery_count, dead_lettered_at, origin stream/group/consumer.';

COMMENT ON COLUMN evento_dead_letter.status IS 'Ciclo de vida del dead-letter: pending (inicial) → resolved (replay OK) | discarded (descarte deliberado con motivo). ADR-021 Fase 5.';

COMMENT ON COLUMN evento_dead_letter.resolved_at IS 'Momento de la transición a resolved/discarded (NULL mientras pending).';

CREATE INDEX ix_edl_keyset ON evento_dead_letter USING btree (tenant_slug, origin_group, status, dead_lettered_at DESC, event_id DESC);
CREATE INDEX ix_edl_pending ON evento_dead_letter USING btree (dead_lettered_at) WHERE (status = 'pending'::text);
CREATE INDEX ix_edl_resolved ON evento_dead_letter USING btree (resolved_at) WHERE (status = ANY (ARRAY['resolved'::text, 'discarded'::text]));
CREATE INDEX ix_edl_tenant_group_status ON evento_dead_letter USING btree (tenant_slug, origin_group, status);
CREATE INDEX ix_evento_dead_letter_at ON evento_dead_letter USING btree (dead_lettered_at);
