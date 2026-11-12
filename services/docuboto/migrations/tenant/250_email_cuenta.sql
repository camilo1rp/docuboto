-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Mail accounts and the messages they receive.

-- ── email_app_oauth ───────────────────────────────────────────────────────────

CREATE TABLE email_app_oauth (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    proveedor character varying(20) NOT NULL,
    client_id character varying(255) NOT NULL,
    secreto_cifrado bytea NOT NULL,
    secreto_nonce bytea NOT NULL,
    azure_directory_id character varying(255),
    redirect_uri character varying(500) NOT NULL,
    notas text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_email_app_azure_directory CHECK (((((proveedor)::text = 'microsoft'::text) AND (azure_directory_id IS NOT NULL)) OR (((proveedor)::text = 'gmail'::text) AND (azure_directory_id IS NULL)))),
    CONSTRAINT chk_email_app_proveedor CHECK ((proveedor IN ('gmail', 'microsoft'))),
    CONSTRAINT email_app_oauth_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ux_email_app_oauth_proveedor ON email_app_oauth USING btree (proveedor);

-- ── email_cuenta ──────────────────────────────────────────────────────────────

CREATE TABLE email_cuenta (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nombre character varying(200) NOT NULL,
    tipo character varying(20) NOT NULL,
    proveedor character varying(20) NOT NULL,
    host character varying(255) NOT NULL,
    puerto integer DEFAULT 993 NOT NULL,
    usa_ssl boolean DEFAULT true NOT NULL,
    direccion character varying(320) NOT NULL,
    usuario_login character varying(320),
    auth_tipo character varying(20) NOT NULL,
    secreto_cifrado bytea,
    secreto_nonce bytea,
    app_oauth_id uuid,
    oauth_refresh_cifrado bytea,
    oauth_refresh_nonce bytea,
    carpeta_origen character varying(255) DEFAULT 'INBOX'::character varying NOT NULL,
    carpeta_radicados character varying(255) DEFAULT 'Radicados'::character varying NOT NULL,
    dependencia_destino character varying(255),
    tipo_documental_id uuid,
    modo character varying(20) DEFAULT 'asistida'::character varying NOT NULL,
    activa boolean DEFAULT true NOT NULL,
    propietario_user_id uuid,
    ultimo_sondeo_at timestamp with time zone,
    ultimo_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    responsable_user_id uuid,
    acuse_al_remitente boolean DEFAULT false NOT NULL,
    dest_dept_code integer,
    doc_class character varying(50),
    CONSTRAINT chk_email_cuenta_app_segun_auth CHECK (((((auth_tipo)::text = 'oauth2'::text) AND (app_oauth_id IS NOT NULL)) OR (((auth_tipo)::text = 'password'::text) AND (app_oauth_id IS NULL)))),
    CONSTRAINT chk_email_cuenta_auth CHECK ((auth_tipo IN ('password', 'oauth2'))),
    CONSTRAINT chk_email_cuenta_automatica_con_responsable CHECK ((((modo)::text <> 'automatica'::text) OR (responsable_user_id IS NOT NULL))),
    CONSTRAINT chk_email_cuenta_carpetas_distintas CHECK (((carpeta_origen)::text <> (carpeta_radicados)::text)),
    CONSTRAINT chk_email_cuenta_modo CHECK ((modo IN ('asistida', 'automatica'))),
    CONSTRAINT chk_email_cuenta_oauth_par CHECK (((oauth_refresh_cifrado IS NULL) = (oauth_refresh_nonce IS NULL))),
    CONSTRAINT chk_email_cuenta_password_tiene_secreto CHECK ((((auth_tipo)::text <> 'password'::text) OR ((tipo)::text = 'personal'::text) OR (secreto_cifrado IS NOT NULL))),
    CONSTRAINT chk_email_cuenta_personal_sin_secreto CHECK ((((tipo)::text <> 'personal'::text) OR ((secreto_cifrado IS NULL) AND (oauth_refresh_cifrado IS NULL)))),
    CONSTRAINT chk_email_cuenta_propietario CHECK (((((tipo)::text = 'personal'::text) AND (propietario_user_id IS NOT NULL)) OR (((tipo)::text = 'institucional'::text) AND (propietario_user_id IS NULL)))),
    CONSTRAINT chk_email_cuenta_proveedor CHECK ((proveedor IN ('imap', 'gmail', 'microsoft'))),
    CONSTRAINT chk_email_cuenta_puerto CHECK (((puerto >= 1) AND (puerto <= 65535))),
    CONSTRAINT chk_email_cuenta_secreto_par CHECK (((secreto_cifrado IS NULL) = (secreto_nonce IS NULL))),
    CONSTRAINT chk_email_cuenta_tipo CHECK ((tipo IN ('institucional', 'personal'))),
    CONSTRAINT email_cuenta_pkey PRIMARY KEY (id),
    CONSTRAINT fk_email_cuenta_app_oauth FOREIGN KEY (app_oauth_id) REFERENCES email_app_oauth(id) ON DELETE RESTRICT
);

ALTER TABLE email_cuenta
    ADD CONSTRAINT chk_email_cuenta_automatica_con_minimos CHECK ((((modo)::text <> 'automatica'::text) OR ((dest_dept_code IS NOT NULL) AND (doc_class IS NOT NULL))));

COMMENT ON COLUMN email_cuenta.responsable_user_id IS 'Funcionario bajo cuya autoría radica una cuenta en modo automático (Ac. AGN 060/2001 arts. 2 y 13). NULL en modo asistida: allí responde quien pulsa.';

COMMENT ON COLUMN email_cuenta.acuse_al_remitente IS 'Enviar constancia de radicación al remitente. Opt-in: la Ley 1755/2015 art. 15 exige la constancia a PETICIÓN del interesado, no de oficio.';

COMMENT ON COLUMN email_cuenta.dest_dept_code IS 'Código de la dependencia que recibe lo que radica una cuenta automática.';

COMMENT ON COLUMN email_cuenta.doc_class IS 'Código del tipo documental con el que radica una cuenta automática.';

CREATE INDEX ix_email_cuenta_activa ON email_cuenta USING btree (activa, tipo);
CREATE UNIQUE INDEX ux_email_cuenta_institucional_direccion ON email_cuenta USING btree (lower((direccion)::text)) WHERE ((tipo)::text = 'institucional'::text);
CREATE UNIQUE INDEX ux_email_cuenta_personal ON email_cuenta USING btree (propietario_user_id, lower((direccion)::text)) WHERE ((tipo)::text = 'personal'::text);

-- ── email_oauth_state ─────────────────────────────────────────────────────────

CREATE TABLE email_oauth_state (
    state character varying(128) NOT NULL,
    cuenta_id uuid NOT NULL,
    iniciado_por uuid NOT NULL,
    code_verifier character varying(128) NOT NULL,
    redirect_uri character varying(500) NOT NULL,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    expira_at timestamp with time zone NOT NULL,
    usado_at timestamp with time zone,
    CONSTRAINT chk_email_oauth_state_vigencia CHECK ((expira_at > creado_at)),
    CONSTRAINT email_oauth_state_pkey PRIMARY KEY (state),
    CONSTRAINT email_oauth_state_cuenta_id_fkey FOREIGN KEY (cuenta_id) REFERENCES email_cuenta(id) ON DELETE CASCADE
);

CREATE INDEX ix_email_oauth_state_cuenta ON email_oauth_state USING btree (cuenta_id);
CREATE INDEX ix_email_oauth_state_expira ON email_oauth_state USING btree (expira_at) WHERE (usado_at IS NULL);

-- ── email_cuenta_usuario ──────────────────────────────────────────────────────

CREATE TABLE email_cuenta_usuario (
    cuenta_id uuid NOT NULL,
    user_id uuid NOT NULL,
    asignado_at timestamp with time zone DEFAULT now() NOT NULL,
    asignado_by uuid,
    CONSTRAINT email_cuenta_usuario_pkey PRIMARY KEY (cuenta_id, user_id),
    CONSTRAINT email_cuenta_usuario_cuenta_id_fkey FOREIGN KEY (cuenta_id) REFERENCES email_cuenta(id) ON DELETE CASCADE
);

CREATE INDEX ix_email_cuenta_usuario_user ON email_cuenta_usuario USING btree (user_id);

-- ── email_mensaje ─────────────────────────────────────────────────────────────

CREATE TABLE email_mensaje (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    cuenta_id uuid NOT NULL,
    message_id character varying(998) NOT NULL,
    carpeta character varying(255) NOT NULL,
    uidvalidity bigint NOT NULL,
    uid bigint NOT NULL,
    remitente_nombre character varying(500),
    remitente_email character varying(320),
    asunto character varying(2000),
    fecha_recibo timestamp with time zone NOT NULL,
    num_adjuntos integer DEFAULT 0 NOT NULL,
    crudo_file_id uuid,
    estado character varying(20) DEFAULT 'pendiente'::character varying NOT NULL,
    reservado_por uuid,
    reservado_hasta timestamp with time zone,
    radicado_id uuid,
    tracking_number character varying(50),
    radicado_por uuid,
    radicado_at timestamp with time zone,
    descartado_por uuid,
    descartado_at timestamp with time zone,
    motivo_descarte text,
    visto_at timestamp with time zone DEFAULT now() NOT NULL,
    acuse_enviado_at timestamp with time zone,
    acuse_error text,
    CONSTRAINT chk_email_mensaje_descartado_forma CHECK (((((estado)::text = 'descartado'::text) AND (motivo_descarte IS NOT NULL) AND (descartado_por IS NOT NULL)) OR (((estado)::text <> 'descartado'::text) AND (motivo_descarte IS NULL)))),
    CONSTRAINT chk_email_mensaje_estado CHECK ((estado IN ('pendiente', 'radicado', 'descartado'))),
    CONSTRAINT chk_email_mensaje_radicado_forma CHECK (((((estado)::text = 'radicado'::text) AND (tracking_number IS NOT NULL) AND (radicado_por IS NOT NULL)) OR (((estado)::text <> 'radicado'::text) AND (tracking_number IS NULL) AND (radicado_at IS NULL)))),
    CONSTRAINT chk_email_mensaje_reserva_par CHECK (((reservado_por IS NULL) = (reservado_hasta IS NULL))),
    CONSTRAINT chk_email_mensaje_uid_positivo CHECK (((uid > 0) AND (uidvalidity > 0))),
    CONSTRAINT email_mensaje_pkey PRIMARY KEY (id),
    CONSTRAINT email_mensaje_cuenta_id_fkey FOREIGN KEY (cuenta_id) REFERENCES email_cuenta(id) ON DELETE CASCADE
);

CREATE INDEX ix_email_mensaje_bandeja ON email_mensaje USING btree (cuenta_id, fecha_recibo) WHERE ((estado)::text = 'pendiente'::text);
CREATE INDEX ix_email_mensaje_localizador ON email_mensaje USING btree (cuenta_id, carpeta, uidvalidity, uid);
CREATE UNIQUE INDEX ux_email_mensaje_identidad ON email_mensaje USING btree (cuenta_id, message_id);
