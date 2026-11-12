-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Users, their second factor and their signing keys.

-- ── auth_users ────────────────────────────────────────────────────────────────

CREATE TABLE auth_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying(80) NOT NULL,
    ldap_username character varying(80),
    email character varying(255),
    keycloak_sub character varying(255),
    is_root boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    failed_logins smallint DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    password_migrated boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    active_depe_id integer,
    CONSTRAINT auth_users_keycloak_sub_key UNIQUE (keycloak_sub),
    CONSTRAINT auth_users_pkey PRIMARY KEY (id),
    CONSTRAINT auth_users_username_key UNIQUE (username)
);

-- ── auth_user_totp ────────────────────────────────────────────────────────────

CREATE TABLE auth_user_totp (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    secret_cifrado bytea NOT NULL,
    secret_nonce bytea NOT NULL,
    algoritmo text DEFAULT 'SHA1'::text NOT NULL,
    digitos smallint DEFAULT 6 NOT NULL,
    periodo smallint DEFAULT 30 NOT NULL,
    estado text DEFAULT 'pendiente_activacion'::text NOT NULL,
    last_timestep bigint DEFAULT 0 NOT NULL,
    failed_attempts smallint DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    key_version smallint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT auth_user_totp_algoritmo_check CHECK ((algoritmo = 'SHA1'::text)),
    CONSTRAINT auth_user_totp_estado_check CHECK ((estado = ANY (ARRAY['pendiente_activacion'::text, 'activo'::text, 'revocado'::text]))),
    CONSTRAINT auth_user_totp_pkey PRIMARY KEY (id),
    CONSTRAINT auth_user_totp_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

COMMENT ON TABLE auth_user_totp IS 'Credencial TOTP (RFC 6238) para el segundo factor de la firma personal (E06). El secreto se cifra en reposo (AESGCM) y solo se descifra en memoria al verificar — NUNCA se persiste, loguea, ni figura en respuesta o evento algunos. GET /me/totp jamás lo devuelve.';

COMMENT ON COLUMN auth_user_totp.secret_cifrado IS 'Ciphertext AESGCM del secreto (20 bytes). AAD = "tenant:{slug}:user:{user_id}:totp" — ata el ciphertext a la fila/tenant; movido a otra fila/tenant, el descifrado falla.';

COMMENT ON COLUMN auth_user_totp.last_timestep IS 'Último timestep (T = floor(unix/periodo)) aceptado. El verify solo acepta Tm > last_timestep (anti-replay) — avance atómico en un único UPDATE condicional (WHERE last_timestep < Tm).';

COMMENT ON COLUMN auth_user_totp.failed_attempts IS 'Contador de verificaciones fallidas (activate + verify comparten la misma fila). Al alcanzar totp_max_failed_attempts, locked_until = now() + totp_lockout_seconds; ninguno de los dos es reseteable por el caller.';

CREATE INDEX ix_auth_user_totp_user ON auth_user_totp USING btree (user_id);
CREATE UNIQUE INDEX uq_auth_user_totp_activo ON auth_user_totp USING btree (user_id) WHERE (estado <> 'revocado'::text);

-- ── auth_user_totp_recovery ───────────────────────────────────────────────────

CREATE TABLE auth_user_totp_recovery (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    totp_id uuid NOT NULL,
    user_id uuid NOT NULL,
    code_hash bytea NOT NULL,
    code_salt bytea NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT auth_user_totp_recovery_pkey PRIMARY KEY (id),
    CONSTRAINT auth_user_totp_recovery_totp_id_fkey FOREIGN KEY (totp_id) REFERENCES auth_user_totp(id) ON DELETE CASCADE,
    CONSTRAINT auth_user_totp_recovery_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

COMMENT ON TABLE auth_user_totp_recovery IS 'Códigos de recuperación de un solo uso del 2FA TOTP (E06). Sirven para RECUPERAR la cuenta (revocar/reenrolar el TOTP perdido), NUNCA para firmar: /internal/totp/verify no los acepta. Se guardan como hash scrypt con sal por código -- una copia de esta tabla no permite autenticarse.';

COMMENT ON COLUMN auth_user_totp_recovery.code_hash IS 'scrypt(code_utf8, salt) con los parámetros de app/core/recovery_codes.py. El código en claro se muestra UNA vez, al generarlo, y no se persiste.';

COMMENT ON COLUMN auth_user_totp_recovery.used_at IS 'NULL = disponible. El consumo es un UPDATE condicional WHERE used_at IS NULL, de modo que dos peticiones concurrentes con el mismo código no lo gastan dos veces (una gana, la otra ve 0 filas y lo trata como inválido).';

CREATE INDEX ix_auth_user_totp_recovery_disponibles ON auth_user_totp_recovery USING btree (user_id) WHERE (used_at IS NULL);
CREATE INDEX ix_auth_user_totp_recovery_totp ON auth_user_totp_recovery USING btree (totp_id);

-- ── auth_user_signing_key ─────────────────────────────────────────────────────

CREATE TABLE auth_user_signing_key (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    cert_pem text,
    csr_pem text,
    private_key_cifrado bytea,
    private_key_nonce bytea,
    subject_dn text,
    cargo text,
    serial text,
    not_before timestamp with time zone,
    not_after timestamp with time zone,
    key_version smallint DEFAULT 1 NOT NULL,
    estado text DEFAULT 'pendiente_emision'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revocado_at timestamp with time zone,
    revocado_por uuid,
    motivo_revocacion text,
    certificado_fingerprint text,
    user_ca_fingerprint text,
    private_key_purgado_at timestamp with time zone,
    CONSTRAINT auth_user_signing_key_estado_check CHECK ((estado = ANY (ARRAY['pendiente_emision'::text, 'activo'::text, 'revocado'::text]))),
    CONSTRAINT auth_user_signing_key_motivo_revocacion_vocab CHECK (((motivo_revocacion IS NULL) OR (motivo_revocacion = ANY (ARRAY['key_compromise'::text, 'superseded'::text, 'cessation_of_operation'::text, 'affiliation_changed'::text, 'privilege_withdrawn'::text, 'unspecified'::text])))),
    CONSTRAINT chk_auth_user_signing_key_purga CHECK ((((private_key_cifrado IS NOT NULL) AND (private_key_nonce IS NOT NULL) AND (private_key_purgado_at IS NULL)) OR ((private_key_cifrado IS NULL) AND (private_key_nonce IS NULL) AND (private_key_purgado_at IS NOT NULL) AND (estado = 'revocado'::text)))),
    CONSTRAINT auth_user_signing_key_pkey PRIMARY KEY (id),
    CONSTRAINT auth_user_signing_key_revocado_por_fkey FOREIGN KEY (revocado_por) REFERENCES auth_users(id) ON DELETE SET NULL,
    CONSTRAINT auth_user_signing_key_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

COMMENT ON TABLE auth_user_signing_key IS 'Clave de firma PKI personal por-usuario (E17/F4, Incremento 1, custodia servidor). La clave privada se cifra en reposo (AESGCM) y solo se descifra en memoria dentro de auth-service, para firmar UN digest — NUNCA se persiste en claro, ni se loguea, ni figura en respuesta o evento algunos. GET /me/signing-key jamás la devuelve.';

COMMENT ON COLUMN auth_user_signing_key.cert_pem IS 'Certificado X.509 emitido OFFLINE por scripts/gen-user-cert.sh (o por una CA de test en memoria en los tests) contra el CSR de esta fila. NULL mientras estado=''pendiente_emision''. Material PÚBLICO.';

COMMENT ON COLUMN auth_user_signing_key.private_key_cifrado IS 'Ciphertext AESGCM de la clave privada RSA-2048 (PEM). AAD = "tenant:{slug}:user:{user_id}:signing" — ata el ciphertext a la fila/tenant; movido a otra fila/tenant, el descifrado falla.';

COMMENT ON COLUMN auth_user_signing_key.estado IS 'pendiente_emision: keygen+CSR listos, a la espera del certificado emitido offline. activo: certificado cargado, utilizable para firmar. revocado: reemplazada por un re-enroll o revocada explícitamente.';

COMMENT ON COLUMN auth_user_signing_key.revocado_at IS 'Momento de la revocación (self-revoke o admin-revoke) — NULL mientras estado <> ''revocado''. Fuente de verdad de GET .../revocation-status.';

COMMENT ON COLUMN auth_user_signing_key.revocado_por IS 'auth_users.id del actor que revocó (el propio titular en self-revoke, un administrador en admin-revoke). ON DELETE SET NULL: no bloquea borrar al admin, y la fila revocada en sí ya cae por CASCADE si se borra su PROPIO titular (user_id, ver 011).';

COMMENT ON COLUMN auth_user_signing_key.motivo_revocacion IS 'Código cerrado (RFC 5280 reasonCode): key_compromise | superseded | cessation_of_operation | affiliation_changed | privilege_withdrawn | unspecified. NULL = no informado (se trata como unspecified). key_compromise invalida hacia atrás las firmas previas en /verify.';

COMMENT ON COLUMN auth_user_signing_key.certificado_fingerprint IS 'SHA-256 hex del DER del certificado LEAF (mismo valor que certificado_fingerprint en signature-service.firma) — clave de búsqueda de GET /internal/signing-key/revocation-status. Poblada por activar_certificado() a partir del Incremento 2; backfilleada aquí para las filas emitidas antes.';

COMMENT ON COLUMN auth_user_signing_key.user_ca_fingerprint IS 'SHA-256 hex de la sub-CA de usuarios (ca-users.crt) que emitió este leaf — verificada por activar_certificado() vía leaf.verify_directly_issued_by(sub_ca), fail-closed. NULL en filas emitidas antes del Incremento 2 (grandfather estructural, sin backfill posible sin volver a verificar la cadena).';

COMMENT ON COLUMN auth_user_signing_key.private_key_purgado_at IS 'Marca de purga del material privado (E17/F4): non-NULL ⟺ private_key_cifrado/nonce anulados. Solo credenciales revocadas se purgan; lo público (cert_pem, huellas, serial, revocado_*) queda intacto para verificación/revocation-status.';

CREATE INDEX ix_auth_user_signing_key_certificado_fingerprint ON auth_user_signing_key USING btree (certificado_fingerprint) WHERE (certificado_fingerprint IS NOT NULL);
CREATE INDEX ix_auth_user_signing_key_key_version ON auth_user_signing_key USING btree (key_version);
CREATE INDEX ix_auth_user_signing_key_user ON auth_user_signing_key USING btree (user_id);
CREATE UNIQUE INDEX uq_auth_user_signing_key_activa ON auth_user_signing_key USING btree (user_id) WHERE (estado <> 'revocado'::text);

-- ── usua_historico ────────────────────────────────────────────────────────────

CREATE TABLE usua_historico (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    executor_id uuid,
    target_id uuid,
    accion character varying(40) NOT NULL,
    ip inet,
    navegador text,
    permisos_json jsonb,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT usua_historico_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_usua_historico_target ON usua_historico USING btree (target_id, ts DESC);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reserva_meses_entre(desde timestamp with time zone, hasta timestamp with time zone) RETURNS integer
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT GREATEST(0, (
        EXTRACT(YEAR  FROM age(hasta AT TIME ZONE 'UTC', desde AT TIME ZONE 'UTC'))::int * 12
      + EXTRACT(MONTH FROM age(hasta AT TIME ZONE 'UTC', desde AT TIME ZONE 'UTC'))::int
    ))
$$;

CREATE FUNCTION unidad_lock_arbol(p uuid) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('unidad_arbol:' || current_schema() || ':' || COALESCE(unidad_raiz(p), p)::text, 0));
END;
$$;
