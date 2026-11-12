-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 073 (C-5 / C7; ADR-043, enmienda ADR-026 D4.4). search_path = tenant_{slug}.
--
-- Fondo acumulado con calendario vencido: en vez de un COMPLIANCE irreversible de ~400 días
-- (el clamp que se retira), el contenido queda bajo un legal hold `valoracion_pendiente` SIN
-- retención hasta que el Comité registre la VALORACIÓN de la TVD que lo gobierna. Esta
-- migración crea las dos tablas de archive que sostienen ese acto:
--
--   * tvd_valoracion: el acto del Comité sobre UNA versión de TVD (append-only; una por
--     versión). Congela la disposición decidida (CT/E/S/M) y el acta.
--   * expediente_valoracion_hold: la amarra expediente <-> hold <-> versión de TVD y el
--     estado de su aplicación (pendiente -> valorada -> aplicada | agotada). Es la cola del
--     job `run_once_valoracion_holds`.
--
-- Solo referencia `trd_series(id)`. Idempotente: IF NOT EXISTS / DROP TRIGGER IF EXISTS.

CREATE TABLE IF NOT EXISTS tvd_valoracion (
    id            UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    trd_serie_id  UUID        NOT NULL UNIQUE REFERENCES trd_series(id),
    disposicion   VARCHAR(2)  NOT NULL CHECK (disposicion IN ('CT', 'E', 'S', 'M')),
    acta_comite   TEXT        NOT NULL CHECK (length(btrim(acta_comite)) >= 1),
    fecha_acta    DATE        NOT NULL,
    fundamento    TEXT        NOT NULL CHECK (length(btrim(fundamento)) >= 20),
    decidida_por  UUID        NOT NULL,
    decidida_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE tvd_valoracion IS
    'Valoración del Comité sobre una versión de TVD (ADR-043). Append-only: una por versión; `disposicion` congela la decisión (CT/E/S/M) tal como estaba en la serie.';

CREATE OR REPLACE FUNCTION reject_tvd_valoracion_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'tvd_valoracion is append-only (ADR-043)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tvd_valoracion_immutable ON tvd_valoracion;
CREATE TRIGGER trg_tvd_valoracion_immutable
    BEFORE UPDATE OR DELETE ON tvd_valoracion
    FOR EACH ROW EXECUTE FUNCTION reject_tvd_valoracion_mutation();

DROP TRIGGER IF EXISTS trg_tvd_valoracion_no_truncate ON tvd_valoracion;
CREATE TRIGGER trg_tvd_valoracion_no_truncate
    BEFORE TRUNCATE ON tvd_valoracion
    FOR EACH STATEMENT EXECUTE FUNCTION reject_tvd_valoracion_mutation();

CREATE TABLE IF NOT EXISTS expediente_valoracion_hold (
    expediente_id    UUID        NOT NULL PRIMARY KEY REFERENCES expedientes(id) ON DELETE CASCADE,
    -- id del hold `valoracion_pendiente` en storage (preservacion_legal_hold.id; sin FK: otra tabla de
    -- otro servicio, contrato SQL declarado en ADR-043).
    hold_id          UUID        NOT NULL,
    -- La versión de TVD que gobierna al expediente cuando se fijó el hold.
    trd_serie_id     UUID        NOT NULL REFERENCES trd_series(id),
    fijado_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    valoracion_id    UUID        REFERENCES tvd_valoracion(id),
    aplicada_at      TIMESTAMPTZ,
    resultado        VARCHAR(16),
    intentos         INTEGER     NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ,
    last_error       TEXT,
    agotada          BOOLEAN     NOT NULL DEFAULT FALSE,
    CONSTRAINT ck_valoracion_hold_resultado
        CHECK (resultado IS NULL OR resultado IN ('retencion_ct', 'sin_retencion')),
    -- Una valoración se aplica sobre una valoración registrada, y se aplica con su resultado.
    CONSTRAINT ck_valoracion_hold_aplicada_coherente
        CHECK ((aplicada_at IS NULL) = (resultado IS NULL)
               AND (aplicada_at IS NULL OR valoracion_id IS NOT NULL)),
    -- Agotada = reintentos agotados, sin aplicar: intervención manual.
    CONSTRAINT ck_valoracion_hold_agotada_sin_aplicar
        CHECK (NOT agotada OR aplicada_at IS NULL)
);

CREATE INDEX IF NOT EXISTS ix_valoracion_hold_pendientes
    ON expediente_valoracion_hold (next_attempt_at NULLS FIRST)
 WHERE valoracion_id IS NOT NULL AND aplicada_at IS NULL AND NOT agotada;

CREATE INDEX IF NOT EXISTS ix_valoracion_hold_serie
    ON expediente_valoracion_hold (trd_serie_id);

-- Inmutables: el hold y la versión. Fill-once: valoración y aplicación. Nunca se borra la fila
-- (salvo en cascada con el expediente).
CREATE OR REPLACE FUNCTION guard_expediente_valoracion_hold() RETURNS trigger AS $$
BEGIN
    IF NEW.hold_id IS DISTINCT FROM OLD.hold_id
       OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id
       OR NEW.expediente_id IS DISTINCT FROM OLD.expediente_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: hold_id, trd_serie_id y expediente_id son inmutables';
    END IF;
    IF OLD.valoracion_id IS NOT NULL AND NEW.valoracion_id IS DISTINCT FROM OLD.valoracion_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: valoracion_id ya registrada es inmutable';
    END IF;
    IF OLD.aplicada_at IS NOT NULL AND (NEW.aplicada_at IS DISTINCT FROM OLD.aplicada_at
                                        OR NEW.resultado IS DISTINCT FROM OLD.resultado) THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: una valoración aplicada es inmutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expediente_valoracion_hold_guard ON expediente_valoracion_hold;
CREATE TRIGGER trg_expediente_valoracion_hold_guard
    BEFORE UPDATE ON expediente_valoracion_hold
    FOR EACH ROW EXECUTE FUNCTION guard_expediente_valoracion_hold();

COMMENT ON TABLE expediente_valoracion_hold IS
    'Cola de la valoración (ADR-043): expediente de fondo acumulado con calendario vencido, su hold `valoracion_pendiente` en storage y el estado de aplicación. pendiente (valoracion_id NULL) -> valorada -> aplicada | agotada.';
