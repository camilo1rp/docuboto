-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Catalogs the entity administers.

-- ── causales ──────────────────────────────────────────────────────────────────

CREATE TABLE causales (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(15) NOT NULL,
    nombre character varying(150) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    ambito character varying(20) DEFAULT 'general'::character varying NOT NULL,
    CONSTRAINT causales_ambito_check CHECK ((ambito IN ('devolucion', 'anulacion', 'reasignacion', 'devolucion_postal', 'general'))),
    CONSTRAINT causales_codigo_key UNIQUE (codigo),
    CONSTRAINT causales_pkey PRIMARY KEY (id)
);

INSERT INTO causales (codigo, nombre, activo, ambito) VALUES
    ('ANU-DUP', 'Anulación: radicado duplicado', 'true', 'anulacion'),
    ('ANU-ERR', 'Anulación: error de radicación', 'true', 'anulacion'),
    ('DEV-DEP', 'Devolución: dirigido a otra dependencia', 'true', 'devolucion'),
    ('DEV-INC', 'Devolución: información/anexos incompletos', 'true', 'devolucion'),
    ('DEV-NOC', 'Devolución: no es competencia de la entidad', 'true', 'devolucion'),
    ('POS-DES', 'Postal: destinatario desconocido', 'true', 'devolucion_postal'),
    ('POS-DIR', 'Postal: dirección incorrecta', 'true', 'devolucion_postal'),
    ('POS-NOR', 'Postal: no reclamado', 'true', 'devolucion_postal'),
    ('POS-OTR', 'Postal: otra causa', 'true', 'devolucion_postal'),
    ('POS-REH', 'Postal: rehusado por el destinatario', 'true', 'devolucion_postal'),
    ('REA-COM', 'Reasignación: por competencia', 'true', 'reasignacion');

-- ── formas_envio ──────────────────────────────────────────────────────────────

CREATE TABLE formas_envio (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(15) NOT NULL,
    nombre character varying(150) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT formas_envio_codigo_key UNIQUE (codigo),
    CONSTRAINT formas_envio_pkey PRIMARY KEY (id)
);

INSERT INTO formas_envio (codigo, nombre, activo) VALUES
    ('EMA', 'Correo electrónico', 'true'),
    ('FAX', 'Fax', 'true'),
    ('FIS', 'Físico', 'true'),
    ('POS', 'Operador postal', 'true');

-- ── medios_recepcion ──────────────────────────────────────────────────────────

CREATE TABLE medios_recepcion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(10) NOT NULL,
    nombre character varying(100) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT medios_recepcion_codigo_key UNIQUE (codigo),
    CONSTRAINT medios_recepcion_pkey PRIMARY KEY (id)
);

INSERT INTO medios_recepcion (codigo, nombre, activo) VALUES
    ('COR', 'Correo', 'true'),
    ('EMA', 'Email', 'true'),
    ('FAX', 'Fax', 'true'),
    ('VEN', 'Ventanilla', 'true'),
    ('WEB', 'Web', 'true');

-- ── soportes ──────────────────────────────────────────────────────────────────

CREATE TABLE soportes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(15) NOT NULL,
    nombre character varying(150) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT soportes_codigo_key UNIQUE (codigo),
    CONSTRAINT soportes_pkey PRIMARY KEY (id)
);

INSERT INTO soportes (codigo, nombre, activo) VALUES
    ('ELE', 'Electrónico', 'true'),
    ('HIB', 'Híbrido', 'true'),
    ('PAP', 'Papel', 'true');

-- ── tipos_anexo ───────────────────────────────────────────────────────────────

CREATE TABLE tipos_anexo (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(10) NOT NULL,
    nombre character varying(100) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT tipos_anexo_codigo_key UNIQUE (codigo),
    CONSTRAINT tipos_anexo_pkey PRIMARY KEY (id)
);

-- ── tipos_identificacion ──────────────────────────────────────────────────────

CREATE TABLE tipos_identificacion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(10) NOT NULL,
    nombre character varying(100) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT tipos_identificacion_codigo_key UNIQUE (codigo),
    CONSTRAINT tipos_identificacion_pkey PRIMARY KEY (id)
);

-- ── tipos_remitente ───────────────────────────────────────────────────────────

CREATE TABLE tipos_remitente (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(10) NOT NULL,
    nombre character varying(100) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT tipos_remitente_codigo_key UNIQUE (codigo),
    CONSTRAINT tipos_remitente_pkey PRIMARY KEY (id)
);

-- ── mensajes_rapidos ──────────────────────────────────────────────────────────

CREATE TABLE mensajes_rapidos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo character varying(15) NOT NULL,
    nombre character varying(150) NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    CONSTRAINT mensajes_rapidos_codigo_key UNIQUE (codigo),
    CONSTRAINT mensajes_rapidos_pkey PRIMARY KEY (id)
);

-- ── non_business_days ─────────────────────────────────────────────────────────

CREATE TABLE non_business_days (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    fecha date NOT NULL,
    descripcion character varying(200) NOT NULL,
    tipo character varying(20) DEFAULT 'festivo'::character varying NOT NULL,
    is_national boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT non_business_days_tipo_check CHECK ((tipo IN ('festivo', 'puente', 'especial'))),
    CONSTRAINT non_business_days_fecha_key UNIQUE (fecha),
    CONSTRAINT non_business_days_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_nbd_fecha ON non_business_days USING btree (fecha);
