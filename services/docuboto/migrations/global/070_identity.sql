-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Docuboto issues identity itself (ADR-048): the keys that sign its JWTs and
-- the service accounts that obtain tokens with client_credentials. Both live in
-- `public` because the issuer is one per installation, not one per schema.

-- ── auth_jwt_keys ─────────────────────────────────────────────────────────────

CREATE TABLE public.auth_jwt_keys (
    kid text NOT NULL,
    private_key_cifrado bytea NOT NULL,
    private_key_nonce bytea NOT NULL,
    kek_version smallint DEFAULT 1 NOT NULL,
    public_jwk jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_at timestamp with time zone,
    CONSTRAINT auth_jwt_keys_pkey PRIMARY KEY (kid),
    CONSTRAINT auth_jwt_keys_nonce_chk CHECK ((octet_length(private_key_nonce) = 12))
);

COMMENT ON TABLE public.auth_jwt_keys IS 'Pares ES256 con los que Docuboto firma sus JWT (ADR-048). Se firma con la clave más reciente sin retired_at. JWKS publica también las retiradas hace menos de la ventana de solapamiento, para que un token emitido antes de rotar siga verificando. La privada va sellada con AES-GCM (IDENTITY_KEY_ENCRYPTION_KEY, AAD = "jwt-key:{kid}").';

-- ── auth_service_accounts ─────────────────────────────────────────────────────

CREATE TABLE public.auth_service_accounts (
    client_id text NOT NULL,
    secret_hash text,
    tenant_slug character varying(100) NOT NULL,
    audience text NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT auth_service_accounts_pkey PRIMARY KEY (client_id),
    CONSTRAINT auth_service_accounts_tenant_fkey FOREIGN KEY (tenant_slug) REFERENCES public.tenants(slug) ON DELETE CASCADE
);

COMMENT ON TABLE public.auth_service_accounts IS 'Cuentas de servicio por entidad (ADR-048). POST /oauth/token con client_credentials emite un JWT con sub = client_id, aud = audience y principal_type = service. secret_hash es argon2id; NULL = la cuenta solo la usa el propio proceso (la pasarela PPI) y no sirve en /oauth/token.';
