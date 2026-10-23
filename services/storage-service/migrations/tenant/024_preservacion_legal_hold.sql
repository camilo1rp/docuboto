-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 024 (C-5 / C7, ADR-043): la retención legal se registra POR MOTIVO.
--
-- Hasta aquí el legal hold era UN booleano por objeto (`preservacion_worm_objeto.legal_hold`), sin
-- motivo: «levantar» con cualquier motivo liberaba TODO, de modo que una valoración del Comité
-- habría liberado también un hold judicial. Y un expediente SIN objetos protegidos aún no se podía
-- congelar, y un objeto creado después de fijar un hold nacía sin hold.
--
--   * `preservacion_legal_hold`  una fila por acto (fijar). Estado: activo (`levantado_at IS NULL`)
--                                -> levantado (una vez, sin vuelta). Append-only salvo ese paso.
--   * El estado S3 del objeto = «el expediente tiene >= 1 hold activo». Se libera en S3 solo cuando
--     el recuento llega a cero (lo hace el servicio, no la base).
--   * `ux_plh_valoracion_activa`  a lo sumo UN hold de sistema `valoracion_pendiente` activo por
--                                 expediente (idempotencia del cierre de un fondo acumulado vencido).
--
-- Fondo acumulado con calendario vencido (régimen (c) de ADR-026 D4.4): el contenido se escribe con
-- legal hold y SIN retención (nada irreversible) hasta que el Comité valore la TVD. Por eso
-- `retain_until` y `retention_mode` dejan de ser NOT NULL; `retencion_origen` dice por qué:
--   calendario             retain_until NOT NULL (el caso de siempre)
--   valoracion_pendiente   retain_until NULL, exige legal_hold
--   valorada_sin_retencion retain_until NULL, valoración E/S/M aplicada (sin retención que fijar)
-- Transiciones (las hace el servicio): valoracion_pendiente -> calendario (valoración CT: se FIJA la
-- retención por primera vez) y valoracion_pendiente -> valorada_sin_retencion. Nunca desde calendario.
--
-- Sin backfill ni rama de legado: las filas existentes son `calendario` (default) y cumplen el CHECK.
-- Idempotente.

CREATE TABLE IF NOT EXISTS preservacion_legal_hold (
    id                    UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    expediente_id         TEXT         NOT NULL,
    motivo_tipo           TEXT         NOT NULL,
    fundamento            TEXT         NOT NULL,
    acta_ref              TEXT         NOT NULL,
    fijado_por            UUID,
    fijado_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    levantado_por         UUID,
    levantado_at          TIMESTAMPTZ,
    levantado_fundamento  TEXT,
    levantado_acta_ref    TEXT,
    CONSTRAINT ck_plh_motivo CHECK (
        motivo_tipo IN ('judicial', 'disciplinario', 'fiscal', 'administrativo', 'valoracion_pendiente')
    ),
    CONSTRAINT ck_plh_fundamento CHECK (length(btrim(fundamento)) >= 10),
    -- Solo el hold de SISTEMA puede nacer sin persona detrás.
    CONSTRAINT ck_plh_fijado_por CHECK (motivo_tipo = 'valoracion_pendiente' OR fijado_por IS NOT NULL),
    -- Levantamiento todo-o-nada (`levantado_por` puede ser NULL: lo levanta el sistema al aplicar una valoración).
    CONSTRAINT ck_plh_levantamiento CHECK (
        (levantado_at IS NULL AND levantado_fundamento IS NULL AND levantado_acta_ref IS NULL)
        OR (levantado_at IS NOT NULL AND levantado_fundamento IS NOT NULL AND levantado_acta_ref IS NOT NULL)
    )
);

COMMENT ON TABLE preservacion_legal_hold IS
    'Retenciones legales por MOTIVO (C-5/C7). El S3 Object Lock del objeto está en hold mientras exista >= 1 fila activa del expediente.';

CREATE INDEX IF NOT EXISTS ix_plh_expediente_activo
    ON preservacion_legal_hold (expediente_id) WHERE levantado_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_plh_valoracion_activa
    ON preservacion_legal_hold (expediente_id)
    WHERE motivo_tipo = 'valoracion_pendiente' AND levantado_at IS NULL;

-- Inmutabilidad: el único UPDATE admitido pasa un hold de activo a levantado (rellena `levantado_*`
-- desde NULL y no toca nada más). DELETE y TRUNCATE rechazados.
CREATE OR REPLACE FUNCTION reject_plh_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.levantado_at IS NULL
           AND NEW.levantado_at IS NOT NULL
           AND NEW.id = OLD.id
           AND NEW.expediente_id = OLD.expediente_id
           AND NEW.motivo_tipo = OLD.motivo_tipo
           AND NEW.fundamento = OLD.fundamento
           AND NEW.acta_ref = OLD.acta_ref
           AND NEW.fijado_por IS NOT DISTINCT FROM OLD.fijado_por
           AND NEW.fijado_at = OLD.fijado_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'preservacion_legal_hold: el único UPDATE admitido es levantar un hold activo (una vez)';
    END IF;
    RAISE EXCEPTION 'preservacion_legal_hold es append-only (Ley 594/2000): % rechazado', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_plh_no_mutar ON preservacion_legal_hold;
CREATE TRIGGER trg_plh_no_mutar
    BEFORE UPDATE OR DELETE ON preservacion_legal_hold
    FOR EACH ROW EXECUTE FUNCTION reject_plh_mutation();

DROP TRIGGER IF EXISTS trg_plh_no_truncar ON preservacion_legal_hold;
CREATE TRIGGER trg_plh_no_truncar
    BEFORE TRUNCATE ON preservacion_legal_hold
    FOR EACH STATEMENT EXECUTE FUNCTION reject_plh_mutation();

-- ── preservacion_worm_objeto: retención diferida ───────────────────────────────────────────────
ALTER TABLE preservacion_worm_objeto ALTER COLUMN retain_until DROP NOT NULL;
ALTER TABLE preservacion_worm_objeto ALTER COLUMN retention_mode DROP NOT NULL;
ALTER TABLE preservacion_worm_objeto
    ADD COLUMN IF NOT EXISTS retencion_origen TEXT NOT NULL DEFAULT 'calendario';

ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS ck_pwo_retencion_origen;
ALTER TABLE preservacion_worm_objeto ADD CONSTRAINT ck_pwo_retencion_origen
    CHECK (retencion_origen IN ('calendario', 'valoracion_pendiente', 'valorada_sin_retencion'));

-- retain_until y retention_mode viajan juntos, y existen si y solo si el origen es `calendario`.
ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS ck_pwo_retencion_coherente;
ALTER TABLE preservacion_worm_objeto ADD CONSTRAINT ck_pwo_retencion_coherente
    CHECK ((retencion_origen = 'calendario') = (retain_until IS NOT NULL AND retention_mode IS NOT NULL)
           AND ((retain_until IS NULL) = (retention_mode IS NULL)));

-- Una retención diferida sin hold es un objeto desprotegido: no puede existir.
ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS ck_pwo_diferida_exige_hold;
ALTER TABLE preservacion_worm_objeto ADD CONSTRAINT ck_pwo_diferida_exige_hold
    CHECK (retencion_origen <> 'valoracion_pendiente' OR legal_hold);

COMMENT ON COLUMN preservacion_worm_objeto.retencion_origen IS
    'calendario | valoracion_pendiente (retain_until NULL + legal hold) | valorada_sin_retencion. C-5/C7, ADR-043.';

-- Rollback (manual): DROP las tres CHECK ck_pwo_*, DROP COLUMN retencion_origen, restaurar NOT NULL
-- (solo si no hay filas con retain_until NULL), DROP TABLE preservacion_legal_hold, DROP FUNCTION reject_plh_mutation.
