-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Third parties: senders and recipients.

-- ── tercero ───────────────────────────────────────────────────────────────────

CREATE TABLE tercero (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tipo text NOT NULL,
    nombre text NOT NULL,
    entidad text,
    tipo_documento text,
    numero_documento text,
    email text,
    telefono text,
    direccion text,
    municipio text,
    departamento text,
    pais text DEFAULT 'CO'::text NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    creado_por uuid,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    actualizado_at timestamp with time zone DEFAULT now() NOT NULL,
    departamento_codigo character(2),
    municipio_codigo character(5),
    fusionado_en uuid,
    fusionado_at timestamp with time zone,
    fusionado_por uuid,
    identidad text DEFAULT 'confirmada'::text NOT NULL,
    documento_declarado_tipo text,
    documento_declarado_numero text,
    conciliacion text,
    conciliada_at timestamp with time zone,
    conciliada_por uuid,
    conciliacion_motivo text,
    conciliacion_medio text,
    CONSTRAINT chk_tercero_conciliacion_autor CHECK (((conciliacion IS NULL) OR ((conciliacion = 'pendiente'::text) = ((conciliada_at IS NULL) AND (conciliada_por IS NULL))))),
    CONSTRAINT chk_tercero_conciliacion_medio CHECK ((((conciliacion_medio IS NULL) OR (conciliacion_medio = ANY (ARRAY['presencial_documento'::text, 'respuesta_canal_registrado'::text, 'llamada_telefono_registrado'::text, 'otro'::text]))) AND ((conciliacion <> ALL (ARRAY['vinculada'::text, 'confirmada'::text])) OR (conciliacion_medio IS NOT NULL)) AND ((conciliacion_medio IS DISTINCT FROM 'otro'::text) OR (conciliacion_motivo IS NOT NULL)))),
    CONSTRAINT chk_tercero_conciliacion_motivo CHECK ((((conciliacion_motivo IS NULL) OR (length(btrim(conciliacion_motivo)) >= 10)) AND ((conciliacion IS DISTINCT FROM 'descartada'::text) OR (conciliacion_motivo IS NOT NULL)))),
    CONSTRAINT chk_tercero_conciliacion_valores CHECK (((conciliacion IS NULL) OR (conciliacion = ANY (ARRAY['pendiente'::text, 'vinculada'::text, 'confirmada'::text, 'descartada'::text])))),
    CONSTRAINT chk_tercero_conciliacion_vinculada CHECK (((conciliacion IS DISTINCT FROM 'vinculada'::text) OR (fusionado_en IS NOT NULL))),
    CONSTRAINT chk_tercero_confirmada_conciliacion CHECK (((identidad <> 'confirmada'::text) OR (conciliacion IS NULL) OR (conciliacion = 'confirmada'::text))),
    CONSTRAINT chk_tercero_fusion_completa CHECK ((((fusionado_en IS NULL) AND (fusionado_at IS NULL)) OR ((fusionado_en IS NOT NULL) AND (fusionado_at IS NOT NULL)))),
    CONSTRAINT chk_tercero_fusion_no_a_si_mismo CHECK (((fusionado_en IS NULL) OR (fusionado_en <> id))),
    CONSTRAINT chk_tercero_identidad_valores CHECK ((identidad = ANY (ARRAY['confirmada'::text, 'provisional'::text]))),
    CONSTRAINT chk_tercero_provisional_sin_identidad CHECK (((identidad <> 'provisional'::text) OR ((tipo_documento IS NULL) AND (numero_documento IS NULL) AND (documento_declarado_numero IS NOT NULL) AND (conciliacion = ANY (ARRAY['pendiente'::text, 'vinculada'::text, 'descartada'::text]))))),
    CONSTRAINT tercero_divipola_coherencia_check CHECK (((municipio_codigo IS NULL) OR ((departamento_codigo IS NOT NULL) AND ("left"((municipio_codigo)::text, 2) = (departamento_codigo)::text)))),
    CONSTRAINT tercero_documento_pareja_check CHECK ((((tipo_documento IS NULL) AND (numero_documento IS NULL)) OR ((tipo_documento IS NOT NULL) AND (numero_documento IS NOT NULL)))),
    CONSTRAINT tercero_nombre_forma_check CHECK ((length(btrim(nombre)) >= 2)),
    CONSTRAINT tercero_tipo_check CHECK ((tipo = ANY (ARRAY['ciudadano'::text, 'empresa'::text, 'direccion_libre'::text, 'usuario_interno'::text, 'entidad_publica'::text]))),
    CONSTRAINT tercero_pkey PRIMARY KEY (id),
    CONSTRAINT tercero_departamento_codigo_fkey FOREIGN KEY (departamento_codigo) REFERENCES public.divipola_departamento(codigo),
    CONSTRAINT tercero_fusionado_en_fkey FOREIGN KEY (fusionado_en) REFERENCES tercero(id),
    CONSTRAINT tercero_municipio_codigo_fkey FOREIGN KEY (municipio_codigo) REFERENCES public.divipola_municipio(codigo)
);

COMMENT ON TABLE tercero IS 'Catálogo de terceros de la comunicación (E01, RF-RAD-05): remitentes y destinatarios CON identidad, reutilizables entre radicados.';

COMMENT ON COLUMN tercero.municipio_codigo IS 'Código DIVIPOLA (5 dígitos) del municipio. NULL para direcciones fuera de Colombia y para fichas anteriores al catálogo. Es lo que impide que «Bogotá» y «BOGOTA» sean dos municipios.';

COMMENT ON COLUMN tercero.fusionado_en IS 'Si no es NULL, esta ficha se fusionó en la que apunta: es un duplicado ya consolidado. No se borra para no perder el rastro de los radicado_actor que se repuntaron. Las fichas fusionadas se excluyen de la búsqueda.';

COMMENT ON COLUMN tercero.identidad IS 'confirmada: la identidad (tipo+número de documento) fue verificada o es del catálogo. provisional: la declaró un anónimo en la ventanilla pública y NO ocupa el documento.';

COMMENT ON COLUMN tercero.documento_declarado_numero IS 'Documento que el anónimo DECLARÓ. Evidencia: inmutable (trigger). No identifica a nadie hasta que un funcionario concilia.';

COMMENT ON COLUMN tercero.conciliacion IS 'pendiente | vinculada (fusionada en una ficha existente) | confirmada (pasó a ficha definitiva) | descartada. NULL en fichas que nunca fueron provisionales.';

COMMENT ON COLUMN tercero.conciliada_por IS 'auth_users.id de quien concilió; nunca el sub de Keycloak.';

COMMENT ON COLUMN tercero.conciliacion_medio IS 'presencial_documento | respuesta_canal_registrado | llamada_telefono_registrado | otro. Obligatorio al vincular o confirmar; con `otro`, el motivo es obligatorio.';

CREATE INDEX ix_tercero_activo_nombre ON tercero USING btree (lower(nombre)) WHERE (fusionado_en IS NULL);
CREATE INDEX ix_tercero_documento ON tercero USING btree (tipo_documento, numero_documento) WHERE ((numero_documento IS NOT NULL) AND (fusionado_en IS NULL));
CREATE INDEX ix_tercero_nombre_norm ON tercero USING gin (to_tsvector('public.spanish_unaccent'::regconfig, ((COALESCE(nombre, ''::text) || ' '::text) || COALESCE(entidad, ''::text))));
CREATE INDEX ix_tercero_por_conciliar ON tercero USING btree (creado_at) WHERE (conciliacion = 'pendiente'::text);
CREATE INDEX ix_tercero_tipo ON tercero USING btree (tipo) WHERE activo;
CREATE UNIQUE INDEX ux_tercero_documento ON tercero USING btree (tipo_documento, numero_documento) WHERE (tipo_documento IS NOT NULL);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_tercero_conciliacion_rewrite() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.documento_declarado_tipo IS DISTINCT FROM OLD.documento_declarado_tipo
       OR NEW.documento_declarado_numero IS DISTINCT FROM OLD.documento_declarado_numero
    THEN
        RAISE EXCEPTION
            'el documento declarado por el remitente es evidencia y no se edita (C17, ADR-038)';
    END IF;
    IF OLD.conciliacion IN ('vinculada', 'confirmada', 'descartada')
       AND NEW.conciliacion IS DISTINCT FROM OLD.conciliacion
    THEN
        RAISE EXCEPTION
            'una conciliación terminada no se rehace (C17, ADR-038)';
    END IF;
    IF OLD.identidad = 'confirmada' AND NEW.identidad = 'provisional' THEN
        RAISE EXCEPTION
            'una ficha confirmada no vuelve a provisional (C17, ADR-038)';
    END IF;
    RETURN NEW;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_tercero_conciliacion BEFORE UPDATE ON tercero FOR EACH ROW EXECUTE FUNCTION reject_tercero_conciliacion_rewrite();
