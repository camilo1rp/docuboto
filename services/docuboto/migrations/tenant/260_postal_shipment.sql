-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Postal shipments and the operator's callbacks.

-- ── postal_operator_credential ────────────────────────────────────────────────

CREATE TABLE postal_operator_credential (
    operador character varying(20) NOT NULL,
    webhook_secret text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    secreto_cifrado bytea,
    secreto_nonce bytea,
    anterior_cifrado bytea,
    anterior_nonce bytea,
    anterior_caduca_at timestamp with time zone,
    rotado_por uuid,
    CONSTRAINT postal_operator_credential_pkey PRIMARY KEY (operador)
);

-- ── postal_shipment ───────────────────────────────────────────────────────────

CREATE TABLE postal_shipment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    operador character varying(20) NOT NULL,
    guia character varying(60) NOT NULL,
    estado character varying(16) DEFAULT 'registrado'::character varying NOT NULL,
    destinatario text,
    direccion text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    motivo_devolucion text,
    acuse_file_id uuid,
    acuse_sha256 character(64),
    CONSTRAINT postal_shipment_estado_check CHECK ((estado IN ('registrado', 'en_transito', 'entregado', 'devuelto', 'fallido'))),
    CONSTRAINT postal_shipment_pkey PRIMARY KEY (id),
    CONSTRAINT postal_shipment_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE
);

COMMENT ON COLUMN postal_shipment.acuse_file_id IS 'file_id (storage) del acuse de recibo: constancia de entrega que aporta quien registra la entrega a mano.';

CREATE INDEX ix_postal_radicado ON postal_shipment USING btree (radicado_id);
CREATE UNIQUE INDEX postal_shipment_acuse_file_id_uq ON postal_shipment USING btree (acuse_file_id) WHERE (acuse_file_id IS NOT NULL);
CREATE UNIQUE INDEX ux_postal_guia ON postal_shipment USING btree (operador, guia);

-- ── postal_shipment_event ─────────────────────────────────────────────────────

CREATE TABLE postal_shipment_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid NOT NULL,
    estado character varying(16) NOT NULL,
    descripcion text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    ocurrido_operador_at timestamp with time zone,
    CONSTRAINT postal_shipment_event_pkey PRIMARY KEY (id),
    CONSTRAINT postal_shipment_event_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES postal_shipment(id) ON DELETE CASCADE
);

CREATE INDEX ix_postal_event_shipment ON postal_shipment_event USING btree (shipment_id, ts);

-- ── postal_webhook_delivery ───────────────────────────────────────────────────

CREATE TABLE postal_webhook_delivery (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operador character varying(20) NOT NULL,
    event_id character varying(120) NOT NULL,
    guia character varying(60) NOT NULL,
    estado_operador character varying(40) NOT NULL,
    estado_interno character varying(16),
    shipment_id uuid,
    aplicada boolean DEFAULT false NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT postal_webhook_delivery_operador_event_id_key UNIQUE (operador, event_id),
    CONSTRAINT postal_webhook_delivery_pkey PRIMARY KEY (id),
    CONSTRAINT postal_webhook_delivery_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES postal_shipment(id) ON DELETE SET NULL
);

CREATE INDEX ix_postal_delivery_guia ON postal_webhook_delivery USING btree (guia);

-- ── postal_callback_diferido ──────────────────────────────────────────────────

CREATE TABLE postal_callback_diferido (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    operador character varying(20) NOT NULL,
    event_id character varying(120) NOT NULL,
    guia character varying(60) NOT NULL,
    cuerpo bytea NOT NULL,
    cuerpo_sha256 character(64) NOT NULL,
    firma_hmac text NOT NULL,
    estado_interno character varying(16) NOT NULL,
    recibido_at timestamp with time zone DEFAULT now() NOT NULL,
    estado character varying(12) DEFAULT 'pendiente'::character varying NOT NULL,
    intentos integer DEFAULT 0 NOT NULL,
    ultimo_error text,
    resultado character varying(24),
    procesado_at timestamp with time zone,
    CONSTRAINT postal_callback_diferido_cuerpo_check CHECK ((octet_length(cuerpo) <= 65536)),
    CONSTRAINT postal_callback_diferido_estado_check CHECK ((estado IN ('pendiente', 'procesado', 'descartado'))),
    CONSTRAINT postal_diferido_cierre CHECK ((((estado)::text = 'pendiente'::text) = (procesado_at IS NULL))),
    CONSTRAINT postal_callback_diferido_operador_event_id_key UNIQUE (operador, event_id),
    CONSTRAINT postal_callback_diferido_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_postal_diferido_pendiente ON postal_callback_diferido USING btree (recibido_at, id) WHERE ((estado)::text = 'pendiente'::text);
