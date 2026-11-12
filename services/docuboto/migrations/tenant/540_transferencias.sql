-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Transfers and their signed acts.

-- ── transferencias ────────────────────────────────────────────────────────────

CREATE TABLE transferencias (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tipo character varying(12) NOT NULL,
    estado character varying(12) DEFAULT 'preparada'::character varying NOT NULL,
    expediente_id uuid NOT NULL,
    ubicacion_origen_id uuid,
    ubicacion_destino_id uuid,
    creada_por text,
    observacion text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    enviada_at timestamp with time zone,
    recibida_at timestamp with time zone,
    decidida_por text,
    enviada_por text,
    motivo_rechazo text,
    rechazada_at timestamp with time zone,
    objeto text,
    oficina_productora_codigo integer,
    oficina_productora_nombre text,
    unidad_administrativa_codigo integer,
    unidad_administrativa_nombre text,
    fixity_fallos_intentos integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_transferencias_fixity_fallos_intentos CHECK ((fixity_fallos_intentos >= 0)),
    CONSTRAINT transferencias_estado_check CHECK ((estado IN ('preparada', 'enviada', 'recibida', 'rechazada'))),
    CONSTRAINT transferencias_tipo_check CHECK ((tipo IN ('primaria', 'secundaria'))),
    CONSTRAINT transferencias_pkey PRIMARY KEY (id),
    CONSTRAINT transferencias_ubicacion_destino_id_fkey FOREIGN KEY (ubicacion_destino_id) REFERENCES ubicacion(id) ON DELETE SET NULL,
    CONSTRAINT transferencias_ubicacion_origen_id_fkey FOREIGN KEY (ubicacion_origen_id) REFERENCES ubicacion(id) ON DELETE SET NULL
);

ALTER TABLE transferencias
    ADD CONSTRAINT ck_transferencias_objeto CHECK (((objeto IS NOT NULL) AND (length(btrim(objeto)) >= 3)));

COMMENT ON COLUMN transferencias.objeto IS 'Objeto/proposito de la transferencia (Anexo FUID, Ac. AGN 001/2024). Lo escribe quien prepara: es un dato humano que ninguna derivacion puede inventar. NULL solo en las transferencias anteriores a esta migracion.';

COMMENT ON COLUMN transferencias.oficina_productora_nombre IS 'Snapshot de la dependencia ACTIVA de quien prepara la transferencia (auth_users.active_depe_id -> dependencias). Es quien ENTREGA los documentos, no quien los recibe.';

COMMENT ON COLUMN transferencias.unidad_administrativa_nombre IS 'Snapshot de la dependencia PADRE de la oficina productora (dependencias.parent_id); si la oficina no tiene padre, se repite la propia -- una oficina raiz ES su unidad administrativa.';

CREATE INDEX ix_transferencias_estado ON transferencias USING btree (estado);
CREATE INDEX ix_transferencias_expediente ON transferencias USING btree (expediente_id);
CREATE UNIQUE INDEX ux_transferencias_activa_por_expediente ON transferencias USING btree (expediente_id) WHERE (estado IN ('preparada', 'enviada'));

-- ── transferencia_acta ────────────────────────────────────────────────────────

CREATE TABLE transferencia_acta (
    transferencia_id uuid NOT NULL,
    acta_fuid_xml text NOT NULL,
    acta_fuid_sha256 text NOT NULL,
    elaborado_por text,
    entregado_por text,
    recibido_por text,
    elaborado_at timestamp with time zone,
    entregado_at timestamp with time zone,
    recibido_at timestamp with time zone NOT NULL,
    indice_version integer,
    congelada_at timestamp with time zone DEFAULT now() NOT NULL,
    indice_firma_id uuid,
    indice_sha256 text,
    CONSTRAINT transferencia_acta_pkey PRIMARY KEY (transferencia_id),
    CONSTRAINT transferencia_acta_transferencia_id_fkey FOREIGN KEY (transferencia_id) REFERENCES transferencias(id) ON DELETE CASCADE
);

COMMENT ON COLUMN transferencia_acta.indice_firma_id IS 'expediente_indice.firma_id de la versión indice_version, leído en la MISMA tx de recepción que la fixity: el sello XAdES que ampara el índice entregado. NULL en las actas anteriores a la migración 053.';

COMMENT ON COLUMN transferencia_acta.indice_sha256 IS 'expediente_indice.xml_sha256 de esa versión (huella C14N del XML que la firma cubre). NULL en las actas anteriores a la migración 053.';

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE transferencia_acta FROM orpycamcp_app;

-- ── transferencia_acta_firma ──────────────────────────────────────────────────

CREATE TABLE transferencia_acta_firma (
    transferencia_id uuid NOT NULL,
    estado text DEFAULT 'pendiente_firma'::text NOT NULL,
    firma_id uuid,
    minio_key_firmado text,
    xades_level text,
    acreditado boolean DEFAULT false NOT NULL,
    last_error text,
    reconcile_intentos integer DEFAULT 0 NOT NULL,
    reconcile_proximo_at timestamp with time zone,
    reconcile_agotado boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    firmado_at timestamp with time zone,
    worm_protegido_at timestamp with time zone,
    worm_object_id uuid,
    worm_reconcile_intentos integer DEFAULT 0 NOT NULL,
    worm_reconcile_proximo_at timestamp with time zone,
    worm_reconcile_ultimo_error text,
    worm_reconcile_agotado boolean DEFAULT false NOT NULL,
    worm_retain_until timestamp with time zone,
    worm_renovacion_intentos integer DEFAULT 0 NOT NULL,
    worm_renovacion_proximo_at timestamp with time zone,
    worm_renovacion_ultimo_error text,
    worm_renovacion_agotado boolean DEFAULT false NOT NULL,
    fixity_premis_detalle jsonb,
    fixity_premis_at timestamp with time zone,
    fixity_premis_intentos integer DEFAULT 0 NOT NULL,
    fixity_premis_proximo_at timestamp with time zone,
    fixity_premis_ultimo_error text,
    fixity_premis_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT transferencia_acta_firma_estado_check CHECK ((estado = ANY (ARRAY['pendiente_firma'::text, 'firmado'::text]))),
    CONSTRAINT transferencia_acta_firma_pkey PRIMARY KEY (transferencia_id),
    CONSTRAINT transferencia_acta_firma_transferencia_id_fkey FOREIGN KEY (transferencia_id) REFERENCES transferencia_acta(transferencia_id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma IS 'Estado MUTABLE del sellado XAdES del acta de entrega (H-H, E06/F4) -- 1:1 con transferencia_acta (append-only), NUNCA la misma tabla.';

COMMENT ON COLUMN transferencia_acta_firma.estado IS 'pendiente_firma (default, insertada atómicamente al congelar el acta en recibir()) | firmado (inmutable en la práctica, mark_acta_firmada exige estado <> firmado).';

COMMENT ON COLUMN transferencia_acta_firma.firma_id IS 'firma.id de signature-service una vez sellado -- NULL mientras pendiente_firma.';

COMMENT ON COLUMN transferencia_acta_firma.last_error IS 'Motivo del último intento fallido de sellado (sello_ausente | sellado_xades_no_obtenido) -- visible al reintento manual y a la reconciliación.';

COMMENT ON COLUMN transferencia_acta_firma.reconcile_intentos IS 'Reintentos consumidos por el job de reconciliación de actas (gemelo de expediente_indice.reconcile_intentos, migración 025).';

COMMENT ON COLUMN transferencia_acta_firma.reconcile_proximo_at IS 'Próximo intento elegible del job (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN transferencia_acta_firma.reconcile_agotado IS 'TRUE cuando se agotó indice_reconciliation_max_intentos: permanece pendiente_firma, requiere reintento manual (POST /transferencias/{id}/acta/firmar).';

COMMENT ON COLUMN transferencia_acta_firma.worm_protegido_at IS 'Instante en que storage-service confirmó la copia WORM/Object-Lock del acta (E10 Inc.2 wiring); NULL = todavía no protegido.';

COMMENT ON COLUMN transferencia_acta_firma.worm_object_id IS 'id de la fila preservacion_worm_objeto (storage-service, tipo=acta_transferencia) que registra la copia inmutable — foránea lógica, cruza servicio.';

COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_intentos IS 'Reintentos consumidos por run_once_actas_worm (job de reconciliación) intentando confirmar la protección WORM.';

COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_proximo_at IS 'Próximo intento elegible de run_once_actas_worm (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_ultimo_error IS 'Motivo del último fallo al proteger bajo WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado).';

COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_agotado IS 'TRUE cuando se agotaron los reintentos automáticos: permanece sin proteger, requiere intervención manual.';

COMMENT ON COLUMN transferencia_acta_firma.worm_retain_until IS 'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service, tipo=acta_transferencia) -- poblado al proteger (mark_acta_worm_protegido) y actualizado al renovar (mark_acta_worm_renovado). NULL mientras worm_protegido_at sea NULL.';

COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_intentos IS 'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender retain_until antes de su vencimiento, para el acta de expedientes de series CT.';

COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_proximo_at IS 'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_ultimo_error IS 'Motivo del último fallo al renovar la retención WORM del acta (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';

COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_agotado IS 'TRUE cuando se agotaron los reintentos automáticos de renovación: el acta de una serie CT queda sin extender, requiere intervención manual URGENTE.';

COMMENT ON COLUMN transferencia_acta_firma.fixity_premis_detalle IS 'Detalle de la fixity de la recepción (documentos_ok, huella_indice_ok, documentos_sin_huella...) tal como se verificó en la tx de recibir(); NULL en las recepciones anteriores a la migración 056.';

COMMENT ON COLUMN transferencia_acta_firma.fixity_premis_at IS 'Instante en que storage-service confirmó el evento PREMIS del fixity-check; NULL = pendiente.';

CREATE INDEX ix_transferencia_acta_firma_fixity_premis_pendiente ON transferencia_acta_firma USING btree (fixity_premis_proximo_at) WHERE ((fixity_premis_detalle IS NOT NULL) AND (fixity_premis_at IS NULL) AND (fixity_premis_agotado = false));
CREATE INDEX ix_transferencia_acta_firma_pendiente ON transferencia_acta_firma USING btree (reconcile_proximo_at) WHERE ((estado = 'pendiente_firma'::text) AND (reconcile_agotado = false));
CREATE INDEX ix_transferencia_acta_firma_worm_pendiente ON transferencia_acta_firma USING btree (worm_reconcile_proximo_at) WHERE ((estado = 'firmado'::text) AND (worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));
CREATE INDEX ix_transferencia_acta_firma_worm_renovacion_pendiente ON transferencia_acta_firma USING btree (worm_renovacion_proximo_at) WHERE ((worm_protegido_at IS NOT NULL) AND (worm_renovacion_agotado = false));

-- ── transferencia_acta_firma_personal ─────────────────────────────────────────

CREATE TABLE transferencia_acta_firma_personal (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    transferencia_id uuid NOT NULL,
    rol text NOT NULL,
    firmante_id uuid NOT NULL,
    firma_id uuid NOT NULL,
    minio_key_firmado text,
    xades_level text,
    ca_fingerprint text,
    acreditado boolean DEFAULT false NOT NULL,
    creada_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT transferencia_acta_firma_personal_rol_check CHECK ((rol = ANY (ARRAY['recibe'::text, 'entrega'::text, 'elabora'::text]))),
    CONSTRAINT transferencia_acta_firma_personal_pkey PRIMARY KEY (id),
    CONSTRAINT transferencia_acta_firma_personal_transferencia_id_rol_key UNIQUE (transferencia_id, rol),
    CONSTRAINT transferencia_acta_firma_personal_transferencia_id_fkey FOREIGN KEY (transferencia_id) REFERENCES transferencias(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_personal IS 'Firma PERSONAL PKI por-usuario del acta de entrega (E17/F4 Inc.1) -- ADITIVA sobre el sello institucional (transferencia_acta_firma, migración 026); 1:N por transferencia (una fila por rol), append-only.';

COMMENT ON COLUMN transferencia_acta_firma_personal.rol IS 'recibe (archivista receptor, Inc.1) | entrega (remitente, Inc.2) | elabora (elaborador = creada_por, Inc.3, firmante ADITIVO -- no condiciona firmada_completa).';

COMMENT ON COLUMN transferencia_acta_firma_personal.firmante_id IS 'auth_users.id del firmante -- SIEMPRE derivado del actor autenticado (JWT/X-User-Id), nunca de un campo de cliente sin verificar.';

COMMENT ON COLUMN transferencia_acta_firma_personal.firma_id IS 'firma.id de signature-service (provider=xades_personal) -- INSERT idempotente sobre este valor (UNIQUE (transferencia_id, rol)) protege contra una carrera manual-vs-reintento que ya firmó pero perdió la escritura local (misma clase de riesgo S1 que el sello institucional).';

COMMENT ON COLUMN transferencia_acta_firma_personal.acreditado IS 'RF-FIR-15: SIEMPRE false en este incremento -- art. 7 Ley 527/1999 (no acreditada ONAC), nunca art. 28 (firma digital cualificada).';

CREATE INDEX ix_transferencia_acta_firma_personal_transferencia ON transferencia_acta_firma_personal USING btree (transferencia_id);

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE transferencia_acta_firma_personal FROM orpycamcp_app;

-- ── transferencia_acta_firma_personal_worm ────────────────────────────────────

CREATE TABLE transferencia_acta_firma_personal_worm (
    firma_personal_id uuid NOT NULL,
    worm_protegido_at timestamp with time zone,
    worm_object_id uuid,
    worm_retain_until timestamp with time zone,
    worm_reconcile_intentos integer DEFAULT 0 NOT NULL,
    worm_reconcile_proximo_at timestamp with time zone,
    worm_reconcile_ultimo_error text,
    worm_reconcile_agotado boolean DEFAULT false NOT NULL,
    worm_renovacion_intentos integer DEFAULT 0 NOT NULL,
    worm_renovacion_proximo_at timestamp with time zone,
    worm_renovacion_ultimo_error text,
    worm_renovacion_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT transferencia_acta_firma_personal_worm_pkey PRIMARY KEY (firma_personal_id),
    CONSTRAINT transferencia_acta_firma_personal_worm_firma_personal_id_fkey FOREIGN KEY (firma_personal_id) REFERENCES transferencia_acta_firma_personal(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_personal_worm IS 'Estado del cableado WORM (Object-Lock) del XML de cada firma personal del acta de transferencia (AREA-07-10). 1:1 con transferencia_acta_firma_personal; mutable por diseño (backoff), a diferencia de aquella.';

COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_object_id IS 'id de la fila preservacion_worm_objeto (storage-service, tipo acta_firma_<rol>) -- foránea lógica, cruza servicio.';

COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_renovacion_intentos IS 'Reintentos consumidos por run_once_worm_renovacion extendiendo retain_until del XML de la firma personal (series CT).';

COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_renovacion_agotado IS 'TRUE cuando se agotaron los reintentos automáticos: la firma personal de una serie CT queda sin extender, requiere intervención manual URGENTE.';

CREATE INDEX ix_transferencia_acta_firma_personal_worm_pendiente ON transferencia_acta_firma_personal_worm USING btree (worm_reconcile_proximo_at) WHERE ((worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));
CREATE INDEX ix_transferencia_acta_firma_personal_worm_renovacion_pendiente ON transferencia_acta_firma_personal_worm USING btree (worm_renovacion_proximo_at) WHERE ((worm_protegido_at IS NOT NULL) AND (worm_renovacion_agotado = false));

-- ── transferencia_acta_firma_excepcion ────────────────────────────────────────

CREATE TABLE transferencia_acta_firma_excepcion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    transferencia_id uuid NOT NULL,
    rol text NOT NULL,
    parte_id uuid NOT NULL,
    motivo text NOT NULL,
    fundamento text NOT NULL,
    referencia_documental text NOT NULL,
    declarada_por uuid NOT NULL,
    declarada_en timestamp with time zone DEFAULT now() NOT NULL,
    referencia_radicado_id uuid,
    referencia_anexo_id uuid,
    constancia_xml text,
    constancia_sha256 text,
    firma_id uuid,
    minio_key_firmado text,
    xades_level text,
    firma_personal_id uuid,
    minio_key_firmado_personal text,
    xades_level_personal text,
    firma_personal_requerida boolean DEFAULT false NOT NULL,
    ordinal integer DEFAULT 1 NOT NULL,
    sustituye_a uuid,
    constancia_version smallint DEFAULT 1 NOT NULL,
    referencia_radicado_numero text,
    referencia_anexo_sha256 text,
    declarante_nombre text,
    declarante_cargo text,
    declarante_fuente text,
    parte_nombre text,
    parte_cargo text,
    parte_fuente text,
    fundamento_hmac text,
    fundamento_hmac_key_version integer,
    fundamento_hmac_algoritmo text,
    fundamento_sal bytea,
    fundamento_compromiso text,
    fundamento_compromiso_algoritmo text,
    sobre_firma_id uuid,
    sobre_minio_key text,
    sobre_sha256 text,
    sobre_xades_level text,
    CONSTRAINT ck_firma_excepcion_declarante_no_es_la_parte CHECK ((declarada_por <> parte_id)),
    CONSTRAINT ck_firma_excepcion_firma_personal_coherente CHECK ((((firma_personal_id IS NULL) = (minio_key_firmado_personal IS NULL)) AND ((NOT firma_personal_requerida) OR (firma_personal_id IS NOT NULL)))),
    CONSTRAINT ck_firma_excepcion_ordinal_coherente CHECK (((ordinal >= 1) AND ((ordinal = 1) = (sustituye_a IS NULL)))),
    CONSTRAINT ck_firma_excepcion_sello_coherente CHECK ((((firma_id IS NULL) = (minio_key_firmado IS NULL)) AND ((firma_id IS NULL) = (constancia_xml IS NULL)) AND ((firma_id IS NULL) = (constancia_sha256 IS NULL)))),
    CONSTRAINT ck_firma_excepcion_v2_completa CHECK ((((ordinal = 1) OR (constancia_version >= 2)) AND ((constancia_version < 2) OR ((referencia_anexo_id IS NOT NULL) AND (referencia_radicado_numero IS NOT NULL) AND (referencia_anexo_sha256 IS NOT NULL) AND (declarante_fuente IS NOT NULL) AND (parte_fuente IS NOT NULL) AND (firma_id IS NOT NULL) AND ((constancia_version >= 3) OR ((fundamento_hmac IS NOT NULL) AND (fundamento_hmac_key_version IS NOT NULL) AND (fundamento_hmac_algoritmo IS NOT NULL))) AND ((constancia_version < 3) OR ((fundamento_sal IS NOT NULL) AND (octet_length(fundamento_sal) = 32) AND (fundamento_compromiso IS NOT NULL) AND (fundamento_compromiso_algoritmo = 'SHA-256-salted'::text))))))),
    CONSTRAINT transferencia_acta_firma_excepcion_fundamento_check CHECK ((length(btrim(fundamento)) >= 20)),
    CONSTRAINT transferencia_acta_firma_excepcion_motivo_check CHECK ((motivo = ANY (ARRAY['fallecimiento'::text, 'retiro'::text, 'incapacidad'::text, 'otro'::text]))),
    CONSTRAINT transferencia_acta_firma_excepcion_referencia_documental_check CHECK ((length(btrim(referencia_documental)) >= 3)),
    CONSTRAINT transferencia_acta_firma_excepcion_rol_check CHECK ((rol = ANY (ARRAY['recibe'::text, 'entrega'::text, 'elabora'::text]))),
    CONSTRAINT transferencia_acta_firma_excepcion_pkey PRIMARY KEY (id),
    CONSTRAINT uq_firma_excepcion_cadena UNIQUE (transferencia_id, rol, ordinal),
    CONSTRAINT uq_firma_excepcion_sustituye_a UNIQUE (sustituye_a),
    CONSTRAINT transferencia_acta_firma_excepcion_sustituye_a_fkey FOREIGN KEY (sustituye_a) REFERENCES transferencia_acta_firma_excepcion(id),
    CONSTRAINT transferencia_acta_firma_excepcion_transferencia_id_fkey FOREIGN KEY (transferencia_id) REFERENCES transferencias(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_excepcion IS 'Constancia inmutable de que una parte del acta de entrega NO PUDO firmar (acto administrativo, AREA-07-10). El acta pasa a firmada_con_excepcion, nunca a firmada_completa.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.parte_id IS 'Identidad (auth_users.id) de la parte que no firma, DERIVADA por el servidor de enviada_por/decidida_por/creada_por según rol; nunca un dato del cliente.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.declarada_por IS 'Quien declara la excepción (USUA_PERM_ADMIN crud>=3). Distinto de parte_id por CHECK.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.constancia_xml IS 'XML canónico que se firmó (urn:orpycamcp:acta-firma-excepcion:v1). NULL en constancias anteriores a la migración 061 (sin firma).';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_id IS 'Sello institucional XAdES-T de la constancia (signature-service). NULL = constancia legada sin firmar.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_personal_id IS 'Firma personal XAdES del declarante, si tenía credencial activa (o la política de la entidad la exigía).';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_personal_requerida IS 'Instantánea de la política XAdES de la entidad al declarar: TRUE = la firma personal del declarante era obligatoria.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.ordinal IS 'Posición en la cadena de constancias del rol: 1 = primera; n+1 = re-declaración tras revocar la n.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.sustituye_a IS 'Constancia (revocada) a la que sustituye esta; NULL solo en la primera de la cadena.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.declarante_fuente IS 'De dónde salió el nombre del declarante: certificado (CN del certificado de firma) | usuario (username) | no_resuelta.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_hmac IS 'HMAC-SHA256 del fundamento con la clave por entidad (signature-service, HKDF fundamento_excepcion). NULL en constancias v1.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_sal IS 'Sal aleatoria de 32 bytes del compromiso del fundamento (constancia v3). SECRETA: solo administración nivel 3; nunca en audit_log. No es WORM.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_compromiso IS 'SHA-256(sal || texto_utf8) en hex (constancia v3). NULL en constancias v1/v2 (v2 usa fundamento_hmac, legado).';

COMMENT ON COLUMN transferencia_acta_firma_excepcion.sobre_minio_key IS 'Clave en el almacén del sobre de revelación sellado (texto+sal del fundamento). NULL en constancias anteriores a la 064. Acceso solo mediado por archive-service (administración nivel 3, con asiento de lectura).';

CREATE INDEX ix_transferencia_acta_firma_excepcion_transferencia ON transferencia_acta_firma_excepcion USING btree (transferencia_id);

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE transferencia_acta_firma_excepcion FROM orpycamcp_app;

-- ── transferencia_acta_firma_excepcion_worm ───────────────────────────────────

CREATE TABLE transferencia_acta_firma_excepcion_worm (
    excepcion_id uuid NOT NULL,
    artefacto text NOT NULL,
    worm_protegido_at timestamp with time zone,
    worm_object_id uuid,
    worm_retain_until timestamp with time zone,
    worm_reconcile_intentos integer DEFAULT 0 NOT NULL,
    worm_reconcile_proximo_at timestamp with time zone,
    worm_reconcile_ultimo_error text,
    worm_reconcile_agotado boolean DEFAULT false NOT NULL,
    worm_renovacion_intentos integer DEFAULT 0 NOT NULL,
    worm_renovacion_proximo_at timestamp with time zone,
    worm_renovacion_ultimo_error text,
    worm_renovacion_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT transferencia_acta_firma_excepcion_worm_artefacto_check CHECK ((artefacto = ANY (ARRAY['sello'::text, 'personal'::text, 'sobre'::text]))),
    CONSTRAINT transferencia_acta_firma_excepcion_worm_pkey PRIMARY KEY (excepcion_id, artefacto),
    CONSTRAINT transferencia_acta_firma_excepcion_worm_excepcion_id_fkey FOREIGN KEY (excepcion_id) REFERENCES transferencia_acta_firma_excepcion(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_worm IS 'Estado del cableado WORM de los XML firmados de la constancia de firma inobtenible (AREA-07-10): protección inicial y renovación CT, con backoff. Mutable por diseño.';

CREATE INDEX ix_firma_excepcion_worm_pendiente ON transferencia_acta_firma_excepcion_worm USING btree (worm_reconcile_proximo_at) WHERE ((worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));
CREATE INDEX ix_firma_excepcion_worm_renovacion_pendiente ON transferencia_acta_firma_excepcion_worm USING btree (worm_renovacion_proximo_at) WHERE ((worm_protegido_at IS NOT NULL) AND (worm_renovacion_agotado = false));

-- ── transferencia_acta_firma_excepcion_revocacion ─────────────────────────────

CREATE TABLE transferencia_acta_firma_excepcion_revocacion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    excepcion_id uuid NOT NULL,
    revocada_por uuid NOT NULL,
    fundamento text NOT NULL,
    revocada_en timestamp with time zone DEFAULT now() NOT NULL,
    revocacion_version smallint DEFAULT 1 NOT NULL,
    revocacion_xml text,
    revocacion_sha256 text,
    firma_id uuid,
    minio_key_firmado text,
    xades_level text,
    firma_personal_id uuid,
    minio_key_firmado_personal text,
    xades_level_personal text,
    revocante_nombre text,
    revocante_cargo text,
    revocante_fuente text,
    fundamento_hmac text,
    fundamento_hmac_key_version integer,
    fundamento_hmac_algoritmo text,
    fundamento_sal bytea,
    fundamento_compromiso text,
    fundamento_compromiso_algoritmo text,
    causa text,
    sobre_firma_id uuid,
    sobre_minio_key text,
    sobre_sha256 text,
    sobre_xades_level text,
    CONSTRAINT ck_firma_excepcion_revocacion_causa CHECK (((causa IS NULL) OR (causa = ANY (ARRAY['error_de_declaracion'::text, 'parte_puede_firmar'::text, 'otro'::text])))),
    CONSTRAINT ck_firma_excepcion_revocacion_sello_coherente CHECK ((((firma_id IS NULL) = (minio_key_firmado IS NULL)) AND ((firma_id IS NULL) = (revocacion_xml IS NULL)) AND ((firma_id IS NULL) = (revocacion_sha256 IS NULL)) AND ((firma_personal_id IS NULL) = (minio_key_firmado_personal IS NULL)) AND ((firma_personal_id IS NULL) OR (firma_id IS NOT NULL)))),
    CONSTRAINT ck_firma_excepcion_revocacion_v2_completa CHECK (((revocacion_version < 2) OR ((firma_id IS NOT NULL) AND (revocante_fuente IS NOT NULL) AND ((revocacion_version >= 3) OR ((fundamento_hmac IS NOT NULL) AND (fundamento_hmac_key_version IS NOT NULL) AND (fundamento_hmac_algoritmo IS NOT NULL))) AND ((revocacion_version < 3) OR ((fundamento_sal IS NOT NULL) AND (octet_length(fundamento_sal) = 32) AND (fundamento_compromiso IS NOT NULL) AND (fundamento_compromiso_algoritmo = 'SHA-256-salted'::text) AND (causa IS NOT NULL)))))),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_fundamento_check CHECK ((length(btrim(fundamento)) >= 20)),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_excepcion_id_key UNIQUE (excepcion_id),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_pkey PRIMARY KEY (id),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_excepcion_id_fkey FOREIGN KEY (excepcion_id) REFERENCES transferencia_acta_firma_excepcion(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_revocacion IS 'Revocación de una constancia de firma inobtenible (AREA-07-10): hecho POSTERIOR, append-only. La constancia original no se toca; con la revocación la parte vuelve a poder firmar.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.revocacion_xml IS 'XML canónico firmado (urn:orpycamcp:acta-firma-excepcion-revocacion:v1). NULL en revocaciones anteriores a la 062 (sin firma, definitivas).';

COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.causa IS 'Causa de la revocación (vocabulario cerrado): error_de_declaracion | parte_puede_firmar | otro. NULL en revocaciones anteriores a la 063.';

COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.sobre_minio_key IS 'Clave en el almacén del sobre de revelación sellado de la revocación. NULL en revocaciones anteriores a la 064.';

-- Append-only for the application role.
REVOKE UPDATE, DELETE ON TABLE transferencia_acta_firma_excepcion_revocacion FROM orpycamcp_app;

-- ── transferencia_acta_firma_excepcion_revocacion_worm ────────────────────────

CREATE TABLE transferencia_acta_firma_excepcion_revocacion_worm (
    revocacion_id uuid NOT NULL,
    artefacto text NOT NULL,
    worm_protegido_at timestamp with time zone,
    worm_object_id uuid,
    worm_retain_until timestamp with time zone,
    worm_reconcile_intentos integer DEFAULT 0 NOT NULL,
    worm_reconcile_proximo_at timestamp with time zone,
    worm_reconcile_ultimo_error text,
    worm_reconcile_agotado boolean DEFAULT false NOT NULL,
    worm_renovacion_intentos integer DEFAULT 0 NOT NULL,
    worm_renovacion_proximo_at timestamp with time zone,
    worm_renovacion_ultimo_error text,
    worm_renovacion_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_worm_artefacto_ch CHECK ((artefacto = ANY (ARRAY['sello'::text, 'personal'::text, 'sobre'::text]))),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacion_worm_pkey PRIMARY KEY (revocacion_id, artefacto),
    CONSTRAINT transferencia_acta_firma_excepcion_revocacio_revocacion_id_fkey FOREIGN KEY (revocacion_id) REFERENCES transferencia_acta_firma_excepcion_revocacion(id) ON DELETE CASCADE
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_revocacion_worm IS 'Estado del cableado WORM de los XML firmados de la REVOCACIÓN de una constancia de firma inobtenible (protección inicial y renovación CT, con backoff). Mutable por diseño.';

CREATE INDEX ix_firma_excepcion_revocacion_worm_pendiente ON transferencia_acta_firma_excepcion_revocacion_worm USING btree (worm_reconcile_proximo_at) WHERE ((worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));
CREATE INDEX ix_firma_excepcion_revocacion_worm_renovacion_pendiente ON transferencia_acta_firma_excepcion_revocacion_worm USING btree (worm_renovacion_proximo_at) WHERE ((worm_protegido_at IS NOT NULL) AND (worm_renovacion_agotado = false));

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION firma_excepcion_guard_excepcion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_prev   transferencia_acta_firma_excepcion%ROWTYPE;
    v_rev    transferencia_acta_firma_excepcion_revocacion%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.id = NEW.transferencia_id
                  AND NEW.declarada_por::text IN (t.enviada_por, t.decidida_por, t.creada_por)) THEN
        RAISE EXCEPTION 'quien declara la excepción de firma no puede ser parte de la transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_declarante_es_parte';
    END IF;
    -- Cualquier firma personal del rol cierra la cadena: la parte pudo firmar en la ventana en que
    -- la excepción estuvo revocada, y la firma prevalece sobre toda constancia posterior.
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_personal fp
                WHERE fp.transferencia_id = NEW.transferencia_id AND fp.rol = NEW.rol) THEN
        RAISE EXCEPTION 'el rol % del acta ya firmó personalmente: no admite excepción de firma', NEW.rol
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_parte_ya_firmo';
    END IF;
    IF NEW.sustituye_a IS NOT NULL THEN
        SELECT * INTO v_prev FROM transferencia_acta_firma_excepcion WHERE id = NEW.sustituye_a;
        IF NOT FOUND OR v_prev.transferencia_id <> NEW.transferencia_id OR v_prev.rol <> NEW.rol
           OR NEW.ordinal <> v_prev.ordinal + 1 THEN
            RAISE EXCEPTION 'la constancia no continúa la cadena del rol %', NEW.rol
                USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_cadena_invalida';
        END IF;
        SELECT * INTO v_rev FROM transferencia_acta_firma_excepcion_revocacion WHERE excepcion_id = v_prev.id;
        -- La revocación debe existir, estar sellada y tener TODOS sus artefactos con Object-Lock
        -- confirmado: la constancia sustituida es prueba y su baja también.
        IF NOT FOUND OR v_rev.firma_id IS NULL
           OR NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                           WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'sello'
                             AND w.worm_protegido_at IS NOT NULL)
           OR (v_rev.firma_personal_id IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                    WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'personal'
                      AND w.worm_protegido_at IS NOT NULL))
           OR (v_rev.sobre_minio_key IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                    WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'sobre'
                      AND w.worm_protegido_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'la revocación de la constancia sustituida no está sellada y protegida WORM'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_revocacion_sin_proteger';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION firma_excepcion_guard_firma() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion e
                WHERE e.transferencia_id = NEW.transferencia_id AND e.rol = NEW.rol
                  AND NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion r
                                   WHERE r.excepcion_id = e.id)) THEN
        RAISE EXCEPTION 'el rol % del acta tiene una excepción de firma declarada: no admite firma personal', NEW.rol
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_personal_excepcion_vigente';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION firma_excepcion_revocacion_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_transferencia UUID;
    v_rol TEXT;
BEGIN
    SELECT e.transferencia_id, e.rol INTO v_transferencia, v_rol
      FROM transferencia_acta_firma_excepcion e WHERE e.id = NEW.excepcion_id;
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(v_transferencia::text || ':' || v_rol));
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.id = v_transferencia
                  AND NEW.revocada_por::text IN (t.enviada_por, t.decidida_por, t.creada_por)) THEN
        RAISE EXCEPTION 'quien revoca la excepción de firma no puede ser parte de la transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_revocante_es_parte';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_firma_excepcion_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion is append-only (constancia de excepción de firma inmutable)';
END;
$$;

CREATE FUNCTION reject_firma_excepcion_revocacion_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion_revocacion is append-only';
END;
$$;

CREATE FUNCTION reject_firma_excepcion_revocacion_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion_revocacion is append-only: TRUNCATE denegado';
END;
$$;

CREATE FUNCTION reject_firma_excepcion_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion is append-only: TRUNCATE denegado';
END;
$$;

CREATE FUNCTION reject_transferencia_acta_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta is append-only (acta de entrega inmutable; Ley 594/2000 art. 16, Ac. AGN 001/2024 Anexo FUID)';
END;
$$;

CREATE FUNCTION reject_transferencia_acta_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta is append-only: TRUNCATE denegado (Ley 594/2000 art. 16, Ac. AGN 001/2024 Anexo FUID)';
END;
$$;

CREATE FUNCTION transferencia_guard_tenencia() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_tenencia  UUID[];
    v_tipo_o    TEXT;
    v_tipo_d    TEXT;
    v_esp_o     TEXT;
    v_esp_d     TEXT;
    v_codigo    TEXT;
BEGIN
    -- Sin cambio alguno en las tres columnas: no es una afirmación nueva sobre la tenencia.
    IF TG_OP = 'UPDATE'
       AND NEW.expediente_id IS NOT DISTINCT FROM OLD.expediente_id
       AND NEW.ubicacion_origen_id IS NOT DISTINCT FROM OLD.ubicacion_origen_id
       AND NEW.ubicacion_destino_id IS NOT DISTINCT FROM OLD.ubicacion_destino_id
    THEN
        RETURN NEW;
    END IF;
    -- La exención «UPDATE que solo anula» es EXCLUSIVA de la acción referencial
    -- `ON DELETE SET NULL` (corre dentro de un trigger de RI: pg_trigger_depth() > 1). Un UPDATE
    -- manual que anule el par se valida como cualquier otro (B4, re-auditoría).
    IF TG_OP = 'UPDATE'
       AND pg_trigger_depth() > 1
       AND NEW.expediente_id IS NOT DISTINCT FROM OLD.expediente_id
       AND (NEW.ubicacion_origen_id IS NULL OR NEW.ubicacion_origen_id IS NOT DISTINCT FROM OLD.ubicacion_origen_id)
       AND (NEW.ubicacion_destino_id IS NULL OR NEW.ubicacion_destino_id IS NOT DISTINCT FROM OLD.ubicacion_destino_id)
    THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(array_agg(s.ubicacion_id), ARRAY[]::UUID[]) INTO v_tenencia
      FROM (SELECT u.ubicacion_id
              FROM expediente_unidad eu
              JOIN unidad_conservacion u ON u.id = eu.unidad_id
             WHERE eu.expediente_id = NEW.expediente_id
               AND u.ubicacion_id IS NOT NULL
               FOR SHARE OF eu) s;

    v_codigo := NULL;
    IF cardinality(v_tenencia) > 0 THEN
        IF NEW.ubicacion_origen_id IS NULL OR NEW.ubicacion_destino_id IS NULL THEN
            v_codigo := 'ubicaciones_requeridas_fisico';
        END IF;
    ELSIF (NEW.ubicacion_origen_id IS NULL) <> (NEW.ubicacion_destino_id IS NULL) THEN
        v_codigo := 'ubicaciones_par_incompleto';
    ELSIF NEW.ubicacion_origen_id IS NOT NULL THEN
        v_codigo := 'ubicacion_sin_tenencia';
    END IF;

    IF v_codigo IS NULL AND NEW.ubicacion_origen_id IS NOT NULL AND NEW.ubicacion_destino_id IS NOT NULL THEN
        IF NEW.ubicacion_origen_id = NEW.ubicacion_destino_id THEN
            v_codigo := 'ubicaciones_origen_destino_iguales';
        ELSIF NOT (NEW.ubicacion_origen_id = ANY (v_tenencia)) THEN
            v_codigo := 'ubicacion_origen_no_actual';
        ELSE
            SELECT tipo_archivo INTO v_tipo_o FROM ubicacion WHERE id = NEW.ubicacion_origen_id;
            SELECT tipo_archivo INTO v_tipo_d FROM ubicacion WHERE id = NEW.ubicacion_destino_id;
            v_esp_o := CASE NEW.tipo WHEN 'primaria' THEN 'gestion' ELSE 'central' END;
            v_esp_d := CASE NEW.tipo WHEN 'primaria' THEN 'central' ELSE 'historico' END;
            IF v_tipo_o IS DISTINCT FROM v_esp_o OR v_tipo_d IS DISTINCT FROM v_esp_d THEN
                v_codigo := 'ubicacion_incoherente_con_tipo';
            END IF;
        END IF;
    END IF;

    IF v_codigo IS NOT NULL THEN
        RAISE EXCEPTION '%', v_codigo
            USING ERRCODE = '23514', CONSTRAINT = 'ck_transferencias_tenencia_fisica';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION transferencia_guard_unidad_prestada() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    cod text;
BEGIN
    cod := expediente_unidad_prestada(NEW.expediente_id);
    IF cod IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_prestada: %', cod
            USING ERRCODE = '23514', CONSTRAINT = 'ck_transferencia_unidad_prestada';
    END IF;
    RETURN NEW;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_transferencia_guard_tenencia BEFORE INSERT OR UPDATE OF expediente_id, ubicacion_origen_id, ubicacion_destino_id ON transferencias FOR EACH ROW EXECUTE FUNCTION transferencia_guard_tenencia();
CREATE TRIGGER trg_transferencia_unidad_prestada BEFORE INSERT OR UPDATE OF estado ON transferencias FOR EACH ROW WHEN ((new.estado IN ('preparada', 'enviada'))) EXECUTE FUNCTION transferencia_guard_unidad_prestada();

CREATE TRIGGER trg_transferencia_acta_immutable BEFORE DELETE OR UPDATE ON transferencia_acta FOR EACH ROW EXECUTE FUNCTION reject_transferencia_acta_mutation();
CREATE TRIGGER trg_transferencia_acta_no_truncate BEFORE TRUNCATE ON transferencia_acta FOR EACH STATEMENT EXECUTE FUNCTION reject_transferencia_acta_truncate();

CREATE TRIGGER trg_firma_personal_excepcion_guard BEFORE INSERT ON transferencia_acta_firma_personal FOR EACH ROW EXECUTE FUNCTION firma_excepcion_guard_firma();

CREATE TRIGGER trg_firma_excepcion_guard BEFORE INSERT ON transferencia_acta_firma_excepcion FOR EACH ROW EXECUTE FUNCTION firma_excepcion_guard_excepcion();
CREATE TRIGGER trg_firma_excepcion_immutable BEFORE DELETE OR UPDATE ON transferencia_acta_firma_excepcion FOR EACH ROW EXECUTE FUNCTION reject_firma_excepcion_mutation();
CREATE TRIGGER trg_firma_excepcion_no_truncate BEFORE TRUNCATE ON transferencia_acta_firma_excepcion FOR EACH STATEMENT EXECUTE FUNCTION reject_firma_excepcion_truncate();

CREATE TRIGGER trg_firma_excepcion_revocacion_guard BEFORE INSERT ON transferencia_acta_firma_excepcion_revocacion FOR EACH ROW EXECUTE FUNCTION firma_excepcion_revocacion_guard();
CREATE TRIGGER trg_firma_excepcion_revocacion_immutable BEFORE DELETE OR UPDATE ON transferencia_acta_firma_excepcion_revocacion FOR EACH ROW EXECUTE FUNCTION reject_firma_excepcion_revocacion_mutation();
CREATE TRIGGER trg_firma_excepcion_revocacion_no_truncate BEFORE TRUNCATE ON transferencia_acta_firma_excepcion_revocacion FOR EACH STATEMENT EXECUTE FUNCTION reject_firma_excepcion_revocacion_truncate();
