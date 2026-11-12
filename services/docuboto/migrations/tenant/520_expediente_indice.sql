-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Electronic index of a case file and its AIP versions.

-- ── expediente_indice ─────────────────────────────────────────────────────────

CREATE TABLE expediente_indice (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id uuid NOT NULL,
    version integer NOT NULL,
    xml text NOT NULL,
    xml_sha256 character(64) NOT NULL,
    content_hash character(64) NOT NULL,
    algoritmo text DEFAULT 'SHA-256'::text NOT NULL,
    xsd_version text DEFAULT 'v1'::text NOT NULL,
    num_documentos integer NOT NULL,
    estado text DEFAULT 'vigente'::text NOT NULL,
    firma_id uuid,
    minio_key_firmado text,
    generado_por text,
    generado_ts timestamp with time zone DEFAULT now() NOT NULL,
    reconcile_intentos integer DEFAULT 0 NOT NULL,
    reconcile_proximo_at timestamp with time zone,
    reconcile_ultimo_error text,
    reconcile_agotado boolean DEFAULT false NOT NULL,
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
    CONSTRAINT expediente_indice_estado_check CHECK ((estado = ANY (ARRAY['vigente'::text, 'pendiente_firma'::text, 'firmado'::text]))),
    CONSTRAINT expediente_indice_expediente_id_version_key UNIQUE (expediente_id, version),
    CONSTRAINT expediente_indice_pkey PRIMARY KEY (id),
    CONSTRAINT expediente_indice_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);

COMMENT ON COLUMN expediente_indice.reconcile_intentos IS 'Reintentos consumidos por el job de reconciliación del sellado (residual Ac. AGN 001/2024 art. 4.3.2.4).';

COMMENT ON COLUMN expediente_indice.reconcile_proximo_at IS 'Próximo intento elegible del job (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN expediente_indice.reconcile_ultimo_error IS 'Motivo del último fallo de sellado — visible en GET /expedientes/indices/pendientes-firma.';

COMMENT ON COLUMN expediente_indice.reconcile_agotado IS 'TRUE cuando se agotó indice_reconciliation_max_intentos: permanece pendiente_firma, requiere intervención manual (POST /indice/firmar).';

COMMENT ON COLUMN expediente_indice.worm_protegido_at IS 'Instante en que storage-service confirmó la copia WORM/Object-Lock del índice (E10 Inc.1); NULL = todavía no protegido.';

COMMENT ON COLUMN expediente_indice.worm_object_id IS 'id de la fila preservacion_worm_objeto (storage-service) que registra la copia inmutable — foránea lógica, cruza servicio.';

COMMENT ON COLUMN expediente_indice.worm_reconcile_intentos IS 'Reintentos consumidos por run_once_worm (job de reconciliación) intentando confirmar la protección WORM.';

COMMENT ON COLUMN expediente_indice.worm_reconcile_proximo_at IS 'Próximo intento elegible de run_once_worm (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN expediente_indice.worm_reconcile_ultimo_error IS 'Motivo del último fallo al proteger bajo WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado).';

COMMENT ON COLUMN expediente_indice.worm_reconcile_agotado IS 'TRUE cuando se agotaron los reintentos automáticos: permanece sin proteger, requiere intervención manual.';

COMMENT ON COLUMN expediente_indice.worm_retain_until IS 'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service) -- poblado al proteger (mark_worm_protegido) y actualizado al renovar (mark_worm_renovado). NULL mientras worm_protegido_at sea NULL.';

COMMENT ON COLUMN expediente_indice.worm_renovacion_intentos IS 'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender retain_until antes de su vencimiento, para series CT.';

COMMENT ON COLUMN expediente_indice.worm_renovacion_proximo_at IS 'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN expediente_indice.worm_renovacion_ultimo_error IS 'Motivo del último fallo al renovar la retención WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';

COMMENT ON COLUMN expediente_indice.worm_renovacion_agotado IS 'TRUE cuando se agotaron los reintentos automáticos de renovación: el índice de una serie CT queda sin extender, requiere intervención manual URGENTE (riesgo de vencimiento del lock COMPLIANCE sobre un registro de conservación permanente).';

CREATE INDEX idx_indice_exp ON expediente_indice USING btree (expediente_id, version DESC);
CREATE INDEX ix_expediente_indice_reconcile_pendiente ON expediente_indice USING btree (reconcile_proximo_at) WHERE ((estado = 'pendiente_firma'::text) AND (reconcile_agotado = false));
CREATE INDEX ix_expediente_indice_worm_pendiente ON expediente_indice USING btree (worm_reconcile_proximo_at) WHERE ((estado = 'firmado'::text) AND (worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));
CREATE INDEX ix_expediente_indice_worm_renovacion_pendiente ON expediente_indice USING btree (worm_renovacion_proximo_at) WHERE ((worm_protegido_at IS NOT NULL) AND (worm_renovacion_agotado = false));

-- ── expediente_indice_item ────────────────────────────────────────────────────

CREATE TABLE expediente_indice_item (
    indice_id uuid NOT NULL,
    document_id uuid NOT NULL,
    orden integer NOT NULL,
    titulo text,
    tipologia text,
    fecha_declaracion date,
    fecha_archivado timestamp with time zone,
    valor_huella character(64),
    funcion_resumen text DEFAULT 'SHA-256'::text NOT NULL,
    formato text,
    tamano_bytes bigint,
    folios integer,
    orden_documental integer,
    nivel_seguridad_snapshot smallint,
    estado text,
    dependencia_productora_codigo integer,
    dependencia_productora_nombre text,
    CONSTRAINT ck_expediente_indice_item_estado CHECK (((estado IS NULL) OR (estado = ANY (ARRAY['vinculado'::text, 'excluido'::text])))),
    CONSTRAINT expediente_indice_item_folios_check CHECK (((folios IS NULL) OR (folios >= 0))),
    CONSTRAINT expediente_indice_item_tamano_bytes_check CHECK (((tamano_bytes IS NULL) OR (tamano_bytes >= 0))),
    CONSTRAINT expediente_indice_item_pkey PRIMARY KEY (indice_id, document_id),
    CONSTRAINT expediente_indice_item_indice_id_fkey FOREIGN KEY (indice_id) REFERENCES expediente_indice(id) ON DELETE CASCADE
);

COMMENT ON COLUMN expediente_indice_item.fecha_declaracion IS 'DEPRECADA desde E15 perfil v4 (R2): nunca poblada. La fecha de declaración se deriva de fecha_archivado/added_at (equivalencia declaracion == incorporacion; Orfeo SGD_EXP_FECH vs fech_radi) — una sola fuente de verdad, ver <fechaDeclaracion> en el XML (index_builder.build_xml). Columna conservada NULL por compatibilidad histórica de la tabla; no usar.';

COMMENT ON COLUMN expediente_indice_item.folios IS 'Snapshot inmutable de folios en la versión del índice (E15 RT-15); NULL = no declarado.';

COMMENT ON COLUMN expediente_indice_item.orden_documental IS 'Snapshot del orden_documental ESTABLE del vínculo al generar esta versión del índice (E15 perfil v3); NULL en versiones anteriores a v3.';

COMMENT ON COLUMN expediente_indice_item.nivel_seguridad_snapshot IS 'Snapshot de expedientes.nivel_seguridad al generar esta versión (listaControlAcceso, E15 perfil v3); NULL en versiones anteriores a v3.';

COMMENT ON COLUMN expediente_indice_item.estado IS 'Snapshot de expediente_radicados.estado (vinculado/excluido) al generar esta versión, para reconstruir el hueco trazable en versiones históricas; NULL en versiones anteriores a v3.';

COMMENT ON COLUMN expediente_indice_item.dependencia_productora_codigo IS 'Snapshot inmutable de expediente_radicados.dependencia_productora_codigo en la versión del índice (E15 perfil v4); NULL en versiones anteriores a v4 o en vínculos sin productora declarada.';

COMMENT ON COLUMN expediente_indice_item.dependencia_productora_nombre IS 'Snapshot inmutable de expediente_radicados.dependencia_productora_nombre; ver dependencia_productora_codigo.';

-- ── expediente_aip ────────────────────────────────────────────────────────────

CREATE TABLE expediente_aip (
    expediente_id uuid NOT NULL,
    estado text DEFAULT 'pendiente'::text NOT NULL,
    aip_worm_object_id uuid,
    empaquetado_at timestamp with time zone,
    intentos integer DEFAULT 0 NOT NULL,
    proximo_intento_at timestamp with time zone,
    ultimo_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    worm_retain_until timestamp with time zone,
    worm_renovacion_intentos integer DEFAULT 0 NOT NULL,
    worm_renovacion_proximo_at timestamp with time zone,
    worm_renovacion_ultimo_error text,
    worm_renovacion_agotado boolean DEFAULT false NOT NULL,
    aip_vigente_id uuid,
    indice_xml_sha256 character(64),
    reempaque_intentos integer DEFAULT 0 NOT NULL,
    reempaque_proximo_at timestamp with time zone,
    reempaque_ultimo_error text,
    reempaque_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT expediente_aip_estado_check CHECK ((estado = ANY (ARRAY['pendiente'::text, 'empaquetado'::text, 'omitido'::text, 'agotado'::text]))),
    CONSTRAINT expediente_aip_pkey PRIMARY KEY (expediente_id),
    CONSTRAINT expediente_aip_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);

COMMENT ON COLUMN expediente_aip.worm_retain_until IS 'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service, tipo=aip) -- poblado al empaquetar (mark_empaquetado) y actualizado al renovar (mark_worm_renovado). NULL mientras estado <> ''empaquetado''.';

COMMENT ON COLUMN expediente_aip.worm_renovacion_intentos IS 'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender worm_retain_until del AIP antes de su vencimiento, para series CT.';

COMMENT ON COLUMN expediente_aip.worm_renovacion_proximo_at IS 'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';

COMMENT ON COLUMN expediente_aip.worm_renovacion_ultimo_error IS 'Motivo del último fallo al renovar la retención WORM del AIP (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';

COMMENT ON COLUMN expediente_aip.worm_renovacion_agotado IS 'TRUE cuando se agotaron los reintentos automáticos de renovación: el AIP de una serie CT queda sin extender, requiere intervención manual URGENTE (riesgo de vencimiento del lock COMPLIANCE sobre un registro de conservación permanente).';

CREATE INDEX ix_expediente_aip_pendiente ON expediente_aip USING btree (proximo_intento_at) WHERE (estado = 'pendiente'::text);
CREATE INDEX ix_expediente_aip_reempaque ON expediente_aip USING btree (reempaque_proximo_at) WHERE ((estado = 'empaquetado'::text) AND (reempaque_agotado = false));
CREATE INDEX ix_expediente_aip_worm_renovacion_pendiente ON expediente_aip USING btree (worm_renovacion_proximo_at) WHERE ((estado = 'empaquetado'::text) AND (worm_renovacion_agotado = false));

-- ── expediente_aip_version ────────────────────────────────────────────────────

CREATE TABLE expediente_aip_version (
    expediente_id uuid NOT NULL,
    version integer NOT NULL,
    aip_id uuid NOT NULL,
    indice_file_id uuid NOT NULL,
    indice_xml_sha256 character(64) NOT NULL,
    firma_id uuid,
    firma_ordinal smallint,
    motivo text NOT NULL,
    sustituye_a_aip_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expediente_aip_version_check CHECK (((version = 1) = (sustituye_a_aip_id IS NULL))),
    CONSTRAINT expediente_aip_version_motivo_check CHECK ((motivo = ANY (ARRAY['ingesta'::text, 'renovacion_lta'::text]))),
    CONSTRAINT expediente_aip_version_version_check CHECK ((version >= 1)),
    CONSTRAINT expediente_aip_version_aip_id_key UNIQUE (aip_id),
    CONSTRAINT expediente_aip_version_expediente_id_indice_xml_sha256_key UNIQUE (expediente_id, indice_xml_sha256),
    CONSTRAINT expediente_aip_version_pkey PRIMARY KEY (expediente_id, version),
    CONSTRAINT expediente_aip_version_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expediente_aip(expediente_id)
);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_expediente_aip_version_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'expediente_aip_version is append-only (historia de versiones del AIP inmutable)';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_expediente_aip_version_immutable BEFORE DELETE OR UPDATE ON expediente_aip_version FOR EACH ROW EXECUTE FUNCTION reject_expediente_aip_version_mutation();
