-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Notifications and the events already processed.

-- ── notifications ─────────────────────────────────────────────────────────────

CREATE TABLE notifications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    recipient_email character varying(320) NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    status character varying(20) NOT NULL,
    event_type character varying(100),
    sent_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    last_error text,
    sensible boolean DEFAULT false NOT NULL,
    CONSTRAINT notifications_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN notifications.sensible IS 'true cuando body fue redactado a propósito antes de persistir (RF-FIR-13, p. ej. OTP de 2FA); el correo se envió con el body real por SMTP pero esta fila NUNCA lo contuvo.';

CREATE INDEX ix_notifications_sent_at ON notifications USING btree (sent_at DESC);

-- ── notification_processed_events ─────────────────────────────────────────────

CREATE TABLE notification_processed_events (
    event_id text NOT NULL,
    processed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notification_processed_events_pkey PRIMARY KEY (event_id)
);

COMMENT ON TABLE notification_processed_events IS 'Registro de event_id de eventos ya procesados por notification-service. Patrón insert-then-send (ADR-021 Fase 3): el INSERT precede a cualquier efecto externo (email, webhook). ON CONFLICT DO NOTHING garantiza idempotencia. Requiere job de retención externo para evitar crecimiento ilimitado (sugerencia: DELETE WHERE processed_at < now() - interval ''7 days'').';

CREATE INDEX ix_notification_processed_events_at ON notification_processed_events USING btree (processed_at);
