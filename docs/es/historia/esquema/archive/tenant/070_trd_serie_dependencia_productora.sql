-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 070 (C-4 / C8, ADR-040). search_path = tenant_{slug}.
--
-- Dependencia(s) PRODUCTORA(S) de una serie TRD: el dato que convierte el
-- organigrama (E14) en el cuadro de clasificación (fondo > sección > subsección
-- > serie > subserie). Es contenido de la TRD, por eso cuelga de la VERSIÓN de la
-- serie (no del código): una reorganización posterior no reescribe lo que el
-- Consejo convalidó.
--
-- La dependencia se cita por `dependencias.codigo` (ADR-032) y SIN FK: la tabla
-- es de tenant-service. La columna se llama `dependencia_codigo` a propósito: casa
-- con el barrido `_REF_COLUMNAS` de tenant-service, que así rechaza (409
-- `dependencia_con_historia`) eliminar una dependencia que una TRD cita.

CREATE TABLE IF NOT EXISTS trd_serie_dependencia_productora (
    trd_serie_id        UUID         NOT NULL REFERENCES trd_series(id) ON DELETE CASCADE,
    dependencia_codigo  INTEGER      NOT NULL,
    -- Instantánea del nombre al asignar (ADR-032 §1).
    dependencia_nombre  VARCHAR(100) NOT NULL,
    -- [{codigo,sigla,nombre}] de la raíz a la productora, fijada UNA vez al
    -- convalidar: lo que el organigrama decía cuando el Consejo aprobó.
    ruta_convalidada    JSONB,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (trd_serie_id, dependencia_codigo)
);
CREATE INDEX IF NOT EXISTS ix_trd_productora_codigo
    ON trd_serie_dependencia_productora (dependencia_codigo);

COMMENT ON TABLE trd_serie_dependencia_productora IS
    'Dependencias productoras de una versión de serie TRD (C-4/C8, ADR-040). Contenido '
    'de la versión: solo se escribe en borrador; ruta_convalidada se fija una vez al convalidar.';

CREATE OR REPLACE FUNCTION reject_trd_productora_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE
    v_id      UUID;
    v_estado  VARCHAR;
    v_parent  UUID;
    v_tipo    VARCHAR(3);
    v_code    VARCHAR;
    v_visible BOOLEAN;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_id := OLD.trd_serie_id;
    ELSE
        v_id := NEW.trd_serie_id;
    END IF;

    SELECT estado, parent_id, tipo_instrumento, code
      INTO v_estado, v_parent, v_tipo, v_code
      FROM trd_series WHERE id = v_id FOR SHARE;
    v_visible := FOUND;

    IF NOT v_visible THEN
        -- DELETE en cascada desde un borrador ya borrado (el append-only de
        -- trd_series solo deja borrar borradores). En INSERT/UPDATE la FK manda.
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'trd_serie_dependencia_productora: la version % no existe', v_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF TG_OP <> 'DELETE' THEN
        IF v_parent IS NOT NULL OR v_tipo <> 'TRD' THEN
            RAISE EXCEPTION 'productora_solo_en_serie: la productora se declara en la SERIE TRD, no en subseries ni en TVD (codigo %; C-4/C8)', v_code
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- ruta_convalidada: NULL -> valor, una sola vez, y SOLO sobre una versión ya convalidada
    -- (la ruta describe lo que el Consejo aprobó: antes de convalidar no hay nada que congelar).
    IF TG_OP = 'UPDATE'
       AND NEW.trd_serie_id       IS NOT DISTINCT FROM OLD.trd_serie_id
       AND NEW.dependencia_codigo IS NOT DISTINCT FROM OLD.dependencia_codigo
       AND NEW.dependencia_nombre IS NOT DISTINCT FROM OLD.dependencia_nombre
       AND NEW.created_at         IS NOT DISTINCT FROM OLD.created_at
       AND OLD.ruta_convalidada IS NULL AND NEW.ruta_convalidada IS NOT NULL
       AND v_estado = 'convalidada' THEN
        RETURN NEW;
    END IF;

    -- Captura de un dato PREEXISTENTE: una versión `migrada` (backfill, nunca convalidada) puede
    -- recibir su productora al ratificarse. Solo se AÑADE (INSERT): quitarla o cambiarla en esa
    -- versión sigue siendo contenido que exige versión nueva. El servicio la admite una sola vez,
    -- dentro de la tx de la ratificación, y la asienta con su motivo.
    IF TG_OP = 'INSERT' AND v_estado = 'migrada' THEN
        RETURN NEW;
    END IF;

    IF v_estado <> 'borrador' THEN
        RAISE EXCEPTION 'trd_productora_inmutable: la version del codigo % esta en estado % y su dependencia productora es contenido convalidado (ADR-025/040): abra una version nueva',
            v_code, v_estado
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_trd_productora_append_only ON trd_serie_dependencia_productora;
CREATE TRIGGER trg_trd_productora_append_only
    BEFORE INSERT OR UPDATE OR DELETE ON trd_serie_dependencia_productora
    FOR EACH ROW EXECUTE FUNCTION reject_trd_productora_mutation();

CREATE OR REPLACE FUNCTION reject_trd_productora_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    RAISE EXCEPTION 'trd_serie_dependencia_productora es contenido convalidado: TRUNCATE denegado (ADR-040)';
END
$$;

DROP TRIGGER IF EXISTS trg_trd_productora_no_truncate ON trd_serie_dependencia_productora;
CREATE TRIGGER trg_trd_productora_no_truncate
    BEFORE TRUNCATE ON trd_serie_dependencia_productora
    FOR EACH STATEMENT EXECUTE FUNCTION reject_trd_productora_truncate();

-- Rollback (referencia manual): DROP TABLE trd_serie_dependencia_productora CASCADE;
--   DROP FUNCTION reject_trd_productora_mutation(), reject_trd_productora_truncate();
