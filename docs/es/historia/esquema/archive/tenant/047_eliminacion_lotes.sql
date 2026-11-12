-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, RF-TRD-07, 2026-08-28): el LOTE de eliminación.
--
-- Se elimina POR LOTES, no expediente a expediente: el acta y el aval del
-- Comité son del lote (Ac. AGN 001/2024 art. 4.3.2.5; Ley 594/2000 art. 24).
-- Un lote recorre cinco estados y el borrado es el ÚLTIMO:
--
--     borrador -> publicado -> avalado -> ejecutado
--         \___________\___________\____> anulado
--
-- Esta migración crea la estructura de las dos primeras fases. **Nada de lo
-- que hay aquí borra nada**: el estado `ejecutado` existe en el CHECK para que
-- la máquina de estados esté completa desde el principio, pero el camino que
-- lleva a él (RF-TRD-07, borrado seguro) todavía no está construido.
--
-- El INVENTARIO se congela al proponer, en `eliminacion_lote_items`. No es
-- una copia por comodidad: es lo que se publica para que un tercero pueda
-- oponerse, y lo que el acta describirá. Si se leyera en vivo del expediente,
-- el inventario publicado y el eliminado podrían no ser el mismo -- y el
-- expediente, además, va a dejar de existir.

CREATE TABLE IF NOT EXISTS eliminacion_lotes (
    id                  UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    codigo              TEXT        NOT NULL,
    estado              TEXT        NOT NULL DEFAULT 'borrador',
    observaciones       TEXT,

    creado_por          UUID        NOT NULL,
    creado_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Publicidad previa: el inventario se publica para que quien tenga algo
    -- que oponer pueda hacerlo ANTES, no después.
    publicado_at        TIMESTAMPTZ,
    publicado_hasta     DATE,

    -- Aval del Comité Institucional de Gestión y Desempeño.
    acta_comite         TEXT,
    fecha_aval          DATE,
    acto_administrativo TEXT,
    avalado_por         UUID,
    avalado_at          TIMESTAMPTZ,

    ejecutado_at        TIMESTAMPTZ,
    ejecutado_por       UUID,

    anulado_at          TIMESTAMPTZ,
    anulado_por         UUID,
    anulado_motivo      TEXT,

    CONSTRAINT eliminacion_lotes_codigo_key UNIQUE (codigo),
    CONSTRAINT eliminacion_lotes_estado_check
        CHECK (estado IN ('borrador', 'publicado', 'avalado', 'ejecutado', 'anulado')),
    -- Un lote publicado sin fecha de fin de publicidad no da plazo para
    -- oponerse: la oposición necesita saber hasta cuándo.
    CONSTRAINT eliminacion_lotes_publicado_forma_check
        CHECK (estado <> 'publicado' OR (publicado_at IS NOT NULL AND publicado_hasta IS NOT NULL)),
    -- El aval no se afirma sin sus tres constancias. Mismo criterio que la
    -- convalidación de una versión de TRD (migración 038): una aprobación sin
    -- acta ni fecha es una afirmación no verificada.
    CONSTRAINT eliminacion_lotes_avalado_forma_check
        CHECK (
            estado NOT IN ('avalado', 'ejecutado')
            OR (acta_comite IS NOT NULL AND fecha_aval IS NOT NULL
                AND acto_administrativo IS NOT NULL AND avalado_por IS NOT NULL)
        ),
    CONSTRAINT eliminacion_lotes_anulado_forma_check
        CHECK (
            estado <> 'anulado'
            OR (anulado_at IS NOT NULL AND anulado_por IS NOT NULL
                AND anulado_motivo IS NOT NULL AND length(btrim(anulado_motivo)) >= 10)
        )
);

CREATE INDEX IF NOT EXISTS ix_eliminacion_lotes_estado
    ON eliminacion_lotes (estado, creado_at DESC);


CREATE TABLE IF NOT EXISTS eliminacion_lote_items (
    id                     UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    lote_id                UUID        NOT NULL REFERENCES eliminacion_lotes(id) ON DELETE CASCADE,
    expediente_id          UUID        NOT NULL REFERENCES expedientes(id),

    -- Inventario CONGELADO al proponer (ver cabecera).
    expediente_code        TEXT        NOT NULL,
    expediente_nombre      TEXT,
    trd_serie_code         TEXT,
    disposition_code       TEXT        NOT NULL,
    fecha_vencimiento      DATE        NOT NULL,
    fecha_extrema_inicial  DATE,
    fecha_extrema_final    DATE,
    nivel_seguridad        SMALLINT    NOT NULL,
    radicados_incluidos    INTEGER,
    -- El candado WORM efectivo comprobado al proponer: el MÁXIMO de los tres
    -- que puede tener un expediente (índice firmado, acta de transferencia y
    -- AIP). Manda el más largo, porque mientras UNO siga vigente no se puede
    -- borrar nada. NULL = ninguno de los tres tiene candado.
    worm_retain_until      TIMESTAMPTZ,

    CONSTRAINT eliminacion_lote_items_unicos UNIQUE (lote_id, expediente_id)
);

CREATE INDEX IF NOT EXISTS ix_eliminacion_lote_items_exp
    ON eliminacion_lote_items (expediente_id);

-- El inventario NO se edita nunca, y solo se puede QUITAR una fila mientras el
-- lote siga en `borrador`. En cuanto se publica queda congelado: lo que se
-- publicó es sobre lo que un tercero decidió no oponerse, y cambiarlo después
-- vaciaría de sentido la publicidad previa.
CREATE OR REPLACE FUNCTION reject_eliminacion_item_mutation() RETURNS trigger AS $$
DECLARE
    estado_lote TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'eliminacion_lote_items no se edita: el inventario se congela al proponer';
    END IF;
    SELECT estado INTO estado_lote FROM eliminacion_lotes WHERE id = OLD.lote_id;
    -- Un lote borrado en cascada (`ON DELETE CASCADE`) ya no tiene fila que
    -- consultar: se permite, porque el lote entero se está yendo.
    IF estado_lote IS NOT NULL AND estado_lote <> 'borrador' THEN
        RAISE EXCEPTION
            'el inventario de un lote % ya no se puede modificar (se congela al publicar)',
            estado_lote;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_eliminacion_items_congelado ON eliminacion_lote_items;
CREATE TRIGGER trg_eliminacion_items_congelado
    BEFORE UPDATE OR DELETE ON eliminacion_lote_items
    FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_item_mutation();
