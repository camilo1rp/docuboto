-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Signatures: requests, challenges, chain, archive seals and provider config.

-- ── signer_provider_config ────────────────────────────────────────────────────

CREATE TABLE signer_provider_config (
    tenant_id text NOT NULL,
    provider text DEFAULT 'xades_local'::text NOT NULL,
    cert_fingerprint text,
    acreditado boolean DEFAULT false NOT NULL,
    actualizado_ts timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT signer_provider_config_pkey PRIMARY KEY (tenant_id)
);

COMMENT ON TABLE signer_provider_config IS 'Bookkeeping informativo del sello institucional activo por tenant (Inc.1, recomendado). NO custodia clave ni certificado — solo su huella. Se actualiza en cada firma xades_local exitosa; no es la fuente de verdad histórica (esa es firma.certificado_fingerprint por fila, migración 008).';

COMMENT ON COLUMN signer_provider_config.tenant_id IS 'Slug del tenant (mismo valor que X-Tenant-Slug). Fila única por tenant: aunque el schema ya aísla por tenant, se guarda explícito para que el valor sea auto-descriptivo si la fila se exporta o audita fuera de contexto.';

-- ── firma_solicitud ───────────────────────────────────────────────────────────

CREATE TABLE firma_solicitud (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    objeto_tipo character varying(40) NOT NULL,
    objeto_id text NOT NULL,
    titulo text,
    payload_sha256 character(64),
    estado character varying(20) DEFAULT 'pendiente'::character varying NOT NULL,
    creado_por uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    nivel_seguridad smallint DEFAULT 1 NOT NULL,
    invalidada_at timestamp with time zone,
    invalidada_motivo text,
    CONSTRAINT firma_solicitud_estado_check CHECK ((estado IN ('pendiente', 'firmado', 'rechazado', 'invalidada'))),
    CONSTRAINT firma_solicitud_nivel_seguridad_check CHECK (((nivel_seguridad >= 1) AND (nivel_seguridad <= 3))),
    CONSTRAINT firma_solicitud_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE firma_solicitud IS 'Cabecera de una cadena de firma sobre un objeto documental (E06, ADR-016). Un solo objeto puede tener una cadena abierta simultáneamente.';

COMMENT ON COLUMN firma_solicitud.payload_sha256 IS 'SHA-256 del documento que se firma. Vincula cada turno al contenido exacto. Nunca es NULL para borrador/documento desde E23 (RF-PRO-09): sin huella la firma recaeria sobre el NOMBRE del objeto y sobreviviria al cambio del texto. De donde sale, que no es lo mismo en los dos casos: para objeto_tipo documento es OBLIGATORIO en el cuerpo de la peticion, porque no hay productor a quien preguntarle; para borrador la resuelve el SERVIDOR contra document-service y la del cuerpo se ignora (fuente autoritativa, ver CADENA_PAYLOAD_AUTORITATIVO) — una huella que el llamante elige no vincula a nadie. Los demas tipos aun admiten NULL.';

COMMENT ON COLUMN firma_solicitud.nivel_seguridad IS 'Snapshot del nivel de seguridad (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) del objeto firmado. Se captura al crear la cadena desde radicados.nivel_seguridad (o declarado para otros tipos). Todas las lecturas se gatean contra el clearance del actor (RF-SEG-08, D-15, no-read-up).';

COMMENT ON COLUMN firma_solicitud.invalidada_motivo IS 'Por que decayo la cadena (E23, RF-PRO-11), normalmente porque el texto cambio. Un turno que desaparece sin explicacion hace desconfiar de la herramienta.';

CREATE UNIQUE INDEX ux_firma_solicitud_objeto_abierta ON firma_solicitud USING btree (objeto_tipo, objeto_id) WHERE ((estado)::text = 'pendiente'::text);

-- ── firma_challenge ───────────────────────────────────────────────────────────

CREATE TABLE firma_challenge (
    challenge_id uuid DEFAULT gen_random_uuid() NOT NULL,
    solicitud_id uuid NOT NULL,
    firmante_id uuid NOT NULL,
    hash_documento character(64) NOT NULL,
    factor_tipo text DEFAULT 'otp_email'::text NOT NULL,
    otp_hash text NOT NULL,
    estado text DEFAULT 'pendiente'::text NOT NULL,
    intentos integer DEFAULT 0 NOT NULL,
    max_intentos integer NOT NULL,
    expira_at timestamp with time zone NOT NULL,
    verificado_at timestamp with time zone,
    consumido_at timestamp with time zone,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    canal_masked text NOT NULL,
    tenant_slug text NOT NULL,
    key_version smallint NOT NULL,
    CONSTRAINT firma_challenge_estado_check CHECK ((estado = ANY (ARRAY['pendiente'::text, 'verificado'::text, 'consumido'::text, 'expirado'::text, 'bloqueado'::text]))),
    CONSTRAINT firma_challenge_factor_tipo_check CHECK ((factor_tipo = 'otp_email'::text)),
    CONSTRAINT firma_challenge_key_version_check CHECK ((key_version = ANY (ARRAY[0, 1]))),
    CONSTRAINT firma_challenge_pkey PRIMARY KEY (challenge_id)
);

COMMENT ON TABLE firma_challenge IS 'Segundo factor (OTP por correo, RF-FIR-13) para la firma personal de un turno de firma_cadena. El OTP en claro NUNCA se persiste — solo su HMAC (otp_hash). Un challenge se emite atado a un hash_documento concreto y se consume atómicamente junto con el marcado del turno (anti-replay).';

COMMENT ON COLUMN firma_challenge.solicitud_id IS 'FK lógica a firma_solicitud.id (no enforced — mismo patrón que firma_cadena.firma_id).';

COMMENT ON COLUMN firma_challenge.firmante_id IS 'Debe coincidir con firma_cadena.firmante_id del turno activo en el momento de emitir el challenge (RF-FIR-11) — el emisor solo puede ser el propio firmante.';

COMMENT ON COLUMN firma_challenge.hash_documento IS 'Snapshot de firma_solicitud.payload_sha256 al emitir el challenge. Ata el segundo factor al documento exacto (protección cross-documento, RF-FIR-13).';

COMMENT ON COLUMN firma_challenge.otp_hash IS 'HMAC-SHA256(otp, signature_secret). El OTP en claro NUNCA se persiste, ni se loguea, ni se registra en audit_log/Redis, ni se devuelve en respuesta alguna.';

COMMENT ON COLUMN firma_challenge.estado IS 'pendiente: emitido, esperando OTP. verificado: reservado para un futuro flujo de verificación desacoplada del acto de firma (no usado en Inc.2 — /sign verifica y consume en un solo paso). consumido: OTP correcto y ya aplicado a una firma (terminal, anti-replay). expirado: venció expira_at sin verificarse. bloqueado: agotó max_intentos.';

COMMENT ON COLUMN firma_challenge.intentos IS 'Contador de verificaciones fallidas. Se incrementa y persiste de forma durable aunque el intento de firma se aborte (RF-FIR-13) — nunca se pierde con un rollback del acto de firma que lo originó.';

COMMENT ON COLUMN firma_challenge.max_intentos IS 'Snapshot de settings.otp_max_intentos al emitir el challenge (cambios de configuración posteriores no alteran challenges ya emitidos).';

COMMENT ON COLUMN firma_challenge.canal_masked IS 'Canal de entrega enmascarado para exhibición segura (p.ej. j***@dominio.gov.co). NUNCA el email completo en claro en esta columna.';

COMMENT ON COLUMN firma_challenge.tenant_slug IS 'Slug del tenant al momento de emitir el challenge — trazabilidad explícita en la fila (redundante con el search_path del schema, igual que otras tablas de auditoría del proyecto).';

COMMENT ON COLUMN firma_challenge.key_version IS 'Esquema del HMAC del OTP: 1 = clave HKDF por entidad que ata el challenge_id. 0 = esquema retirado (no verifica).';

CREATE INDEX ix_firma_challenge_expira ON firma_challenge USING btree (estado, expira_at);
CREATE UNIQUE INDEX uq_firma_challenge_activo ON firma_challenge USING btree (solicitud_id, firmante_id, hash_documento) WHERE (estado = ANY (ARRAY['pendiente'::text, 'verificado'::text]));

-- ── firma ─────────────────────────────────────────────────────────────────────

CREATE TABLE firma (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    objeto_tipo character varying(40) NOT NULL,
    objeto_id text,
    formato_solicitado character varying(20),
    firmante_id uuid,
    payload_sha256 character(64) NOT NULL,
    firma_blob text NOT NULL,
    provider character varying(30) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sello_tiempo timestamp with time zone DEFAULT now() NOT NULL,
    tipo_firma character varying(30) DEFAULT 'nativa'::character varying NOT NULL,
    certificado_pem text,
    minio_key_firmado text,
    nivel_conformidad text,
    xades_level text,
    certificado_fingerprint text,
    signed_xml_sha256 text,
    tsa_token text,
    tsa_timestamp timestamp with time zone,
    tsa_cert_fingerprint text,
    tsa_acreditado boolean DEFAULT false NOT NULL,
    lt_material_present boolean DEFAULT false NOT NULL,
    ca_fingerprint text,
    crl_fingerprint text,
    crl_this_update timestamp with time zone,
    crl_next_update timestamp with time zone,
    archive_timestamp_present boolean DEFAULT false NOT NULL,
    archive_timestamp_at timestamp with time zone,
    archive_tsa_cert_fingerprint text,
    revocation_provenance text,
    archive_timestamp_token text,
    ocsp_present boolean DEFAULT false NOT NULL,
    ocsp_responder_fingerprint text,
    ocsp_cert_status text,
    ocsp_produced_at timestamp with time zone,
    ocsp_this_update timestamp with time zone,
    ocsp_next_update timestamp with time zone,
    ocsp_response_der text,
    key_version smallint NOT NULL,
    CONSTRAINT firma_key_version_check CHECK ((key_version = ANY (ARRAY[0, 1]))),
    CONSTRAINT firma_nativa_exige_key_version CHECK ((((provider)::text <> 'nativa'::text) OR (key_version = 1))),
    CONSTRAINT firma_nivel_conformidad_check CHECK (((nivel_conformidad IS NULL) OR (nivel_conformidad = ANY (ARRAY['firma_electronica_nativa'::text, 'xades_b_no_acreditado'::text, 'xades_b_acreditado'::text, 'xades_lt_no_acreditado'::text, 'xades_lt_acreditado'::text, 'xades_lta_no_acreditado'::text, 'xades_lta_acreditado'::text, 'xades_personal_no_acreditado'::text])))),
    CONSTRAINT firma_xades_level_check CHECK (((xades_level IS NULL) OR (xades_level = ANY (ARRAY['XAdES-B'::text, 'XAdES-T'::text, 'XAdES-LT'::text, 'XAdES-LTA'::text])))),
    CONSTRAINT firma_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN firma.sello_tiempo IS 'Timestamp del servidor incorporado en el HMAC (firma_blob). Requerido para re-verificar la firma. No debe ser provisto por el cliente.';

COMMENT ON COLUMN firma.tipo_firma IS 'Tipo de acto de firma: ''nativa'' (HMAC de servicio) | ''sello_sistema'' (XAdES-B con el sello institucional del tenant, atribuido a la persona que actúa vía firmante_id + audit_log, RF-FIR-11) | ''personal'' (XAdES con la credencial PKI por-usuario, E17/F4). No confundir con provider, que identifica la IMPLEMENTACION pluggable. El DEFAULT se alineo a este vocabulario en la migracion 019: la 002 lo habia fijado en ''electronica-nativa'' y la 008 no pudo cambiarlo por su IF NOT EXISTS.';

COMMENT ON COLUMN firma.certificado_pem IS 'Certificado X.509 PÚBLICO (PEM) del sello institucional usado para firmar. NUNCA la clave privada — esa nunca se persiste ni se loguea (RT-16).';

COMMENT ON COLUMN firma.minio_key_firmado IS 'object_key en MinIO (storage-service, bucket orpycamcp-{slug}-documents) del XML COMPLETO ya firmado (XAdES-B). NULL para firmas nativa (el HMAC no se incrusta en un documento separado).';

COMMENT ON COLUMN firma.nivel_conformidad IS 'Perfil de conformidad de la firma. ''firma_electronica_nativa'' = HMAC de retrocompatibilidad; ''xades_b_*''/''xades_lt_*''/''xades_lta_*'' = sello INSTITUCIONAL del tenant; ''xades_personal_no_acreditado'' = firma PERSONAL por-usuario (E17), con certificado de la sub-CA de usuarios y segundo factor TOTP. El sufijo ''_acreditado'' lo produce ÚNICAMENTE un sello institucional cuyo directorio declara la acreditación en acreditacion.json (material de un operador acreditado ONAC aportado por la entidad); el sello de DESARROLLO nunca lo alcanza. La firma personal no tiene variante acreditada: con custodia en servidor no hay control exclusivo del firmante (art. 28), independientemente del certificado. Cuando el firmador añade material LT/LTA promueve el valor a ''xades_lta_*'' (app/providers/local.py), también en la firma personal: la distinción personal/institucional se conserva en `firma.tipo_firma`.';

COMMENT ON COLUMN firma.xades_level IS 'Nivel XAdES realmente alcanzado: XAdES-B | XAdES-T (sello RFC 3161) | XAdES-LT | XAdES-LTA. NULL para firmas nativa (HMAC, sin XML-DSig). Todos tienen productor; ver app.providers.base.SignatureFormat.';

COMMENT ON COLUMN firma.certificado_fingerprint IS 'Huella SHA-256 (hex) del certificado usado para firmar, capturada al momento de la firma. Fuente de verdad para /verify al cotejar el certificado embebido en una firma XAdES — no depende de releer el sello actual del tenant (que pudo rotar desde entonces).';

COMMENT ON COLUMN firma.signed_xml_sha256 IS 'SHA-256 (hex) del XML YA FIRMADO (SignedDoc.signed_xml) persistido al momento de /sign-xml para provider=''xades_local''. /verify lo recalcula sobre el payload_xml recibido para atar la verificación a ESTA firma_id concreta (evita que el signed_xml de otro índice, firmado con el mismo sello del tenant, se reporte como válido para un firma_id ajeno). NULL para firmas ''nativa'' o filas anteriores a esta migración.';

COMMENT ON COLUMN firma.tsa_token IS 'TimeStampToken RFC 3161 (CMS SignedData, DER en base64) que respalda el xades:SignatureTimeStamp incrustado en el XML firmado. Material PÚBLICO (certificado + firma sobre un messageImprint), no un secreto — persistirlo es correcto, a diferencia de una clave privada (RT-16). NULL para firmas nativa o xades_local sin sello de tiempo.';

COMMENT ON COLUMN firma.tsa_timestamp IS 'genTime declarado por la TSA (local o remota) dentro del TimeStampToken — fecha del acto de firma con trazabilidad temporal VERIFICABLE (criptográficamente atada al ds:SignatureValue), NO acreditada / NO cualificada (TSA local sin acreditación ONAC — ver firma.tsa_acreditado y app.core.tsa, RF-FIR-15): no equivale a la fecha cierta oponible en sentido legal fuerte del régimen de estampado cronológico (DUR 1074/2015 Cap.47/48), reservada a entidades de certificación acreditadas. Distinto de firma.sello_tiempo (reloj del propio signature-service al momento del acto, migración 002): este campo es el reloj de la TSA, verificable criptográficamente vía tsa_token. NULL si no hay sello de tiempo.';

COMMENT ON COLUMN firma.tsa_cert_fingerprint IS 'Huella SHA-256 (hex) del certificado de la TSA que emitió tsa_token, capturada al momento de sellar — mismo patrón que firma.certificado_fingerprint (migración 008) pero para la TSA en vez del sello institucional del tenant. NULL si no hay sello de tiempo.';

COMMENT ON COLUMN firma.tsa_acreditado IS 'RF-FIR-15: nunca sobre-declarar conformidad. La TSA local del Incremento 4 es un par autofirmado sin acreditación ONAC — SIEMPRE false. Reservado para cuando (si alguna vez) se integre una TSA acreditada real.';

COMMENT ON COLUMN firma.lt_material_present IS 'True cuando la firma incluye xades:CertificateValues/RevocationValues (material de una CA local de DESARROLLO, scripts/gen-dev-ca.sh) — siempre false para firmas nativa/XAdES-B/XAdES-T o cuando settings.signer_lt_enabled=false / el tenant no tiene ca.crt/crl.der montado junto a su sello (degradación observable, ver firma.lt_downgrade_no_ca en audit_log).';

COMMENT ON COLUMN firma.ca_fingerprint IS 'Huella SHA-256 (hex) del certificado de la CA local embebido en xades:CertificateValues al momento de firmar — ancla de confianza persistida para /verify (mismo patrón de pinning que firma.tsa_cert_fingerprint, migración 012): sin cotejar contra este valor, cualquiera podría anexar CertificateValues de su propia CA.';

COMMENT ON COLUMN firma.crl_fingerprint IS 'Huella SHA-256 (hex) de la CRL (crl.der) embebida al momento de firmar — trazabilidad/auditoría; la autoridad para /verify es la CRL REALMENTE embebida en el XML firmado (MinIO), no esta columna.';

COMMENT ON COLUMN firma.crl_this_update IS 'thisUpdate declarado por la CRL de la CA local al momento de firmar.';

COMMENT ON COLUMN firma.crl_next_update IS 'nextUpdate declarado por la CRL de la CA local al momento de firmar — las CRL de desarrollo caducan con frecuencia; un vencimiento NO invalida por sí solo una verificación posterior en este perfil no acreditado (se expone para diagnóstico, ver VerifyResponse.crl_next_update).';

COMMENT ON COLUMN firma.archive_timestamp_present IS 'True cuando la firma incluye xadesv141:ArchiveTimeStamp (XAdES-LTA simplificado, no acreditado) — requiere lt_material_present=true. False para XAdES-B/-T/-LT o firmas nativa.';

COMMENT ON COLUMN firma.archive_timestamp_at IS 'genTime declarado por el TimeStampToken RFC 3161 del ArchiveTimeStamp (misma TSA local que sella XAdES-T, ver firma.tsa_timestamp) — NULL si archive_timestamp_present=false.';

COMMENT ON COLUMN firma.archive_tsa_cert_fingerprint IS 'Huella SHA-256 (hex) del certificado de la TSA que emitió el ArchiveTimeStamp — hoy siempre igual a firma.tsa_cert_fingerprint (la MISMA TSA local sella tanto SignatureTimeStamp como ArchiveTimeStamp), columna separada por si un incremento futuro usa TSAs distintas.';

COMMENT ON COLUMN firma.revocation_provenance IS 'Procedencia del material de revocación embebido: SIEMPRE ''dev'' en este incremento (CRL de una CA local de desarrollo, jamás de una PKI acreditada) — RF-FIR-15, no ocultar la procedencia. NULL si lt_material_present=false.';

COMMENT ON COLUMN firma.archive_timestamp_token IS 'TimeStampToken RFC 3161 del ArchiveTimeStamp (CMS SignedData, DER en base64) — material PÚBLICO (igual que firma.tsa_token, migración 012), persistido por conveniencia de consulta; la autoridad es el XML firmado en MinIO. NULL si archive_timestamp_present=false.';

COMMENT ON COLUMN firma.ocsp_present IS 'True cuando la firma incluye xades:RevocationValues/xades:OCSPValues (OCSP stapled, responder delegado emitido por la CA local de DESARROLLO, scripts/gen-dev-ocsp-responder.sh) — siempre false para firmas nativa/XAdES-B/XAdES-T/XAdES-LT/XAdES-LTA sin OCSP o cuando settings.signer_ocsp_enabled=false / el tenant no tiene ocsp.crt/ocsp.key montado junto a su sello (degradación observable, ver firma.ocsp_downgrade_no_responder en audit_log).';

COMMENT ON COLUMN firma.ocsp_responder_fingerprint IS 'Huella SHA-256 (hex) del certificado del responder OCSP embebido en xades:OCSPValues al momento de firmar — ancla de confianza persistida para /verify (mismo patrón de pinning que firma.tsa_cert_fingerprint migración 012 y firma.ca_fingerprint migración 013): sin cotejar contra este valor, cualquiera podría anexar una respuesta OCSP firmada con su propio responder.';

COMMENT ON COLUMN firma.ocsp_cert_status IS 'certStatus declarado por la respuesta OCSP al momento de firmar (''good''|''revoked''), derivado de la MISMA CRL embebida en xades:CRLValues — coherencia CRL<->OCSP, nunca dos fuentes de verdad de revocación divergentes dentro del mismo acto de firma.';

COMMENT ON COLUMN firma.ocsp_produced_at IS 'producedAt declarado por la BasicOCSPResponse al momento de firmar.';

COMMENT ON COLUMN firma.ocsp_this_update IS 'thisUpdate declarado por la respuesta OCSP al momento de firmar.';

COMMENT ON COLUMN firma.ocsp_next_update IS 'nextUpdate declarado por la respuesta OCSP al momento de firmar (settings.signer_ocsp_validity_days desde thisUpdate) — un vencimiento NO invalida por sí solo una verificación posterior en este perfil no acreditado (se expone para diagnóstico, mismo criterio que firma.crl_next_update).';

COMMENT ON COLUMN firma.ocsp_response_der IS 'BasicOCSPResponse (RFC 6960, DER en base64) — material PÚBLICO (igual que firma.tsa_token/archive_timestamp_token), persistido por conveniencia de consulta; la autoridad es el XML firmado en MinIO. NULL si ocsp_present=false. NUNCA la clave privada del responder OCSP (RT-16, misma regla no-fuga que seal.key).';

COMMENT ON COLUMN firma.key_version IS 'Esquema del HMAC de la firma NATIVA: 1 = clave HKDF por entidad y mensaje que ata entidad+tipo+objeto. 0 = sin HMAC de servicio (firmas no nativas). Una fila nativa con 0 (esquema anterior a la 020, retirado en B9) no verifica.';

CREATE INDEX ix_firma_objeto ON firma USING btree (objeto_tipo, objeto_id);

-- ── firma_cadena ──────────────────────────────────────────────────────────────

CREATE TABLE firma_cadena (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    solicitud_id uuid NOT NULL,
    orden integer NOT NULL,
    firmante_id uuid NOT NULL,
    estado character varying(20) DEFAULT 'pendiente'::character varying NOT NULL,
    firma_id uuid,
    comentario text,
    decidido_at timestamp with time zone,
    rol character varying(12) DEFAULT 'firma'::character varying NOT NULL,
    CONSTRAINT firma_cadena_estado_check CHECK ((estado IN ('pendiente', 'firmado', 'rechazado', 'invalidada'))),
    CONSTRAINT firma_cadena_rol_check CHECK ((rol IN ('visto_bueno', 'firma'))),
    CONSTRAINT firma_cadena_pkey PRIMARY KEY (id),
    CONSTRAINT firma_cadena_solicitud_id_orden_key UNIQUE (solicitud_id, orden),
    CONSTRAINT firma_cadena_solicitud_id_fkey FOREIGN KEY (solicitud_id) REFERENCES firma_solicitud(id) ON DELETE CASCADE
);

COMMENT ON TABLE firma_cadena IS 'Turno de firma dentro de una cadena (firma_solicitud). El turno activo es el de menor orden con estado=''pendiente''.';

COMMENT ON COLUMN firma_cadena.firma_id IS 'UUID de la fila en firma(id) producida al resolver este turno. FK lógica (no enforced); queda NULL hasta que el firmante actúa.';

COMMENT ON COLUMN firma_cadena.rol IS 'visto_bueno | firma (E23, RF-PRO-10). El turno de rol=firma es el que compromete a la entidad y produce el documento definitivo; los vistos buenos son evidencia del tramite, no firmas.';

CREATE INDEX ix_firma_cadena_pendiente ON firma_cadena USING btree (firmante_id, estado);
CREATE INDEX ix_firma_cadena_solicitud ON firma_cadena USING btree (solicitud_id);

-- ── firma_sello_archivo ───────────────────────────────────────────────────────

CREATE TABLE firma_sello_archivo (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    firma_id uuid NOT NULL,
    ordinal smallint NOT NULL,
    minio_key text NOT NULL,
    xml_sha256 character(64) NOT NULL,
    gen_time timestamp with time zone NOT NULL,
    tsa_cert_fingerprint character(64) NOT NULL,
    tsa_cert_not_after timestamp with time zone NOT NULL,
    estado character varying(16) DEFAULT 'vigente'::character varying NOT NULL,
    ultimo_aviso character varying(80),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tsa_cadena_not_after timestamp with time zone,
    tsa_revocacion_fuente character varying(12),
    revocacion_tsa_previa character varying(12),
    CONSTRAINT firma_sello_archivo_estado_check CHECK ((estado IN ('pendiente_worm', 'vigente', 'caducado'))),
    CONSTRAINT firma_sello_archivo_ordinal_check CHECK ((ordinal >= 0)),
    CONSTRAINT fsa_revocacion_tsa_previa_check CHECK ((revocacion_tsa_previa IN ('crl', 'ocsp', 'autofirmada'))),
    CONSTRAINT fsa_tsa_revocacion_fuente_check CHECK ((tsa_revocacion_fuente IN ('crl', 'ocsp', 'crl_ocsp', 'autofirmada', 'ninguna'))),
    CONSTRAINT firma_sello_archivo_firma_id_ordinal_key UNIQUE (firma_id, ordinal),
    CONSTRAINT firma_sello_archivo_pkey PRIMARY KEY (id),
    CONSTRAINT firma_sello_archivo_firma_id_fkey FOREIGN KEY (firma_id) REFERENCES firma(id)
);

ALTER TABLE firma_sello_archivo
    ADD CONSTRAINT fsa_previa_solo_renovados CHECK (((ordinal = 0) = (revocacion_tsa_previa IS NULL)));

CREATE INDEX ix_fsa_vence ON firma_sello_archivo USING btree (LEAST(tsa_cert_not_after, COALESCE(tsa_cadena_not_after, tsa_cert_not_after)));
CREATE INDEX ix_fsa_vencimiento ON firma_sello_archivo USING btree (tsa_cert_not_after);

-- ── firma_artefacto_vigente ───────────────────────────────────────────────────

CREATE VIEW firma_artefacto_vigente AS
 SELECT DISTINCT ON (f.id) f.id AS firma_id,
    COALESCE(s.minio_key, f.minio_key_firmado) AS minio_key,
    COALESCE(s.xml_sha256, (f.signed_xml_sha256)::bpchar) AS xml_sha256,
    COALESCE((s.ordinal)::integer, 0) AS ordinal
   FROM (firma f
     LEFT JOIN firma_sello_archivo s ON (((s.firma_id = f.id) AND (s.estado IN ('vigente', 'caducado')))))
  ORDER BY f.id, s.ordinal DESC NULLS LAST;
