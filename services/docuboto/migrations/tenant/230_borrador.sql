-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Drafts and document templates.

-- ── plantilla ─────────────────────────────────────────────────────────────────

CREATE TABLE plantilla (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nombre character varying(150) NOT NULL,
    tipo_documental character varying(100),
    cuerpo text NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    es_global boolean DEFAULT true NOT NULL,
    creado_por uuid,
    CONSTRAINT plantilla_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN plantilla.es_global IS 'TRUE = la ve cualquier dependencia (E23, RF-PRO-06). FALSE = solo las listadas en plantilla_dependencia. Por defecto TRUE para no hacer desaparecer las plantillas existentes de todas las pantallas a la vez.';

-- ── plantilla_dependencia ─────────────────────────────────────────────────────

CREATE TABLE plantilla_dependencia (
    plantilla_id uuid NOT NULL,
    depe_codigo integer NOT NULL,
    CONSTRAINT plantilla_dependencia_pkey PRIMARY KEY (plantilla_id, depe_codigo),
    CONSTRAINT plantilla_dependencia_plantilla_id_fkey FOREIGN KEY (plantilla_id) REFERENCES plantilla(id) ON DELETE CASCADE
);

COMMENT ON TABLE plantilla_dependencia IS 'Dependencias que alcanzan una plantilla NO global (paridad legado: sgd_plan_plantillas.depe_codi). Vacía sobre una plantilla no global = no la ve nadie, que es un estado valido: una plantilla retirada de circulacion sin borrar su historial.';

CREATE INDEX ix_plantilla_dependencia_depe ON plantilla_dependencia USING btree (depe_codigo);

-- ── borrador ──────────────────────────────────────────────────────────────────

CREATE TABLE borrador (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    doc_type character varying(1) NOT NULL,
    subject text NOT NULL,
    dest_dept character varying(100),
    cuerpo text,
    plantilla_id uuid,
    estado character varying(12) DEFAULT 'borrador'::character varying NOT NULL,
    radicado_id uuid,
    creado_por uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    radicado_padre_id uuid,
    documento_file_id uuid,
    documento_sha256 character(64),
    documento_formato text,
    combinacion jsonb DEFAULT '{}'::jsonb NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    cuerpo_sha256 character(64),
    faltantes text[] DEFAULT '{}'::text[] NOT NULL,
    nivel_seguridad smallint DEFAULT 1 NOT NULL,
    dest_dept_code integer,
    CONSTRAINT borrador_doc_type_check CHECK ((doc_type IN ('S', 'I'))),
    CONSTRAINT borrador_estado_check CHECK ((estado IN ('borrador', 'aprobado', 'radicado'))),
    CONSTRAINT ck_borrador_nivel_seguridad CHECK (((nivel_seguridad >= 1) AND (nivel_seguridad <= 3))),
    CONSTRAINT borrador_pkey PRIMARY KEY (id),
    CONSTRAINT borrador_plantilla_id_fkey FOREIGN KEY (plantilla_id) REFERENCES plantilla(id) ON DELETE SET NULL,
    CONSTRAINT borrador_radicado_padre_id_fkey FOREIGN KEY (radicado_padre_id) REFERENCES radicados(id) ON DELETE SET NULL
);

COMMENT ON COLUMN borrador.radicado_padre_id IS 'Radicado de Entrada que este borrador responde (E23, RF-PRO-02). Fuente del contexto de combinación: remitente, asunto, número y fecha del padre. Se lee SIEMPRE con el clearance del llamante: combinar sin gate exfiltraría el asunto de un radicado reservado al cuerpo de un documento legible.';

COMMENT ON COLUMN borrador.documento_file_id IS 'UUID en storage-service del PDF/A producido (E23, RF-PRO-03). FK lógica: no hay integridad referencial entre servicios.';

COMMENT ON COLUMN borrador.documento_sha256 IS 'Huella del documento producido. Es lo que un visto bueno afirma haber leído y lo que una firma compromete (E23, RF-PRO-09).';

COMMENT ON COLUMN borrador.combinacion IS 'Contexto CONGELADO con el que se combinó la plantilla. Cambiar la plantilla o la ficha de un tercero después NO reescribe lo ya producido: el dato usado pertenece al acto, no al sujeto (mismo criterio que radicado_actor).';

COMMENT ON COLUMN borrador.version IS 'Sube en cada edición del cuerpo. Cambiar el texto invalida el circuito de aprobación: la invalidación es DERIVADA del hash, no un acto que recordar.';

COMMENT ON COLUMN borrador.cuerpo_sha256 IS 'Huella del cuerpo, calculada por TRIGGER (E23, RF-PRO-09/11). Ningún camino de escritura puede guardar texto nuevo conservando la huella vieja.';

COMMENT ON COLUMN borrador.faltantes IS 'Campos del vocabulario que no tenían dato al combinar la plantilla (E23, RF-PRO-01). Hecho histórico de la combinación: no se recalcula al editar.';

COMMENT ON COLUMN borrador.nivel_seguridad IS 'Nivel de seguridad heredado del radicado padre al crear (RF-SEG-08). El acceso exige nivel <= clearance del llamante, en el WHERE.';

COMMENT ON COLUMN borrador.dest_dept_code IS 'Código de la dependencia destino (ADR-032). Llave de correlación; dest_dept es la instantánea del nombre.';

CREATE INDEX ix_borrador_estado ON borrador USING btree (estado);
CREATE INDEX ix_borrador_radicado_padre ON borrador USING btree (radicado_padre_id) WHERE (radicado_padre_id IS NOT NULL);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION borrador_sellar_version() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.cuerpo_sha256 := CASE
            WHEN NEW.cuerpo IS NULL THEN NULL
            ELSE encode(sha256(convert_to(NEW.cuerpo, 'UTF8')), 'hex')
        END;
        RETURN NEW;
    END IF;

    IF NEW.cuerpo IS DISTINCT FROM OLD.cuerpo THEN
        NEW.version       := OLD.version + 1;
        NEW.cuerpo_sha256 := CASE
            WHEN NEW.cuerpo IS NULL THEN NULL
            ELSE encode(sha256(convert_to(NEW.cuerpo, 'UTF8')), 'hex')
        END;
        -- El documento producido deja de corresponder al texto: se retira.
        -- No se conserva «por si acaso» — esos son justo los bytes que nadie
        -- debe firmar creyendo que son los del texto que tiene delante.
        NEW.documento_file_id := NULL;
        NEW.documento_sha256  := NULL;
        NEW.documento_formato := NULL;
    END IF;
    RETURN NEW;
END $$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_borrador_sellar_version BEFORE INSERT OR UPDATE ON borrador FOR EACH ROW EXECUTE FUNCTION borrador_sellar_version();
