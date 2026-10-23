-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 023 (C-6, ADR-042): el AIP se VERSIONA, no se reescribe.
--
-- Cuando el sello de archivo (XAdES-LTA) del índice se renueva, el AIP que embebe el índice
-- anterior queda desfasado. El AIP anterior está bajo COMPLIANCE y no se toca: se empaqueta una
-- versión nueva (storage la construye DESDE el bag anterior) que lo sustituye.
--
--   * `aip.version`      1 = ingesta; n+1 = reempaquetado.
--   * `aip.sustituye_a`  el AIP al que sustituye (NULL en la versión 1).
--   * `aip.motivo`       'ingesta' | 'renovacion_lta'.
--   * `ux_aip_expediente_version`  una versión por número y expediente.
--   * `ux_aip_sustituye_a`         un AIP no puede tener dos sucesores (sin bifurcación aunque
--                                  dos réplicas corran a la vez).
--   * `ux_pwo_aip_raiz`            UNA raíz WORM de tipo `aip` por expediente: la identidad de
--                                  retención. Sin esto, «la fila WORM del AIP» era ambigua en
--                                  cuanto había dos versiones (H4 del diseño C-6).
--
-- `preservacion_evento.tipo` no tiene CHECK: `RENOVACION` ya se usa y no se migra.
-- Sin backfill ni rama de legado: los AIP previos son corpus de prueba. Si existiesen dos AIP
-- del mismo expediente, el índice único falla A PROPÓSITO (recrear el entorno).
-- Idempotente: IF NOT EXISTS / DROP CONSTRAINT IF EXISTS antes de ADD CONSTRAINT.

ALTER TABLE aip ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
ALTER TABLE aip ADD COLUMN IF NOT EXISTS sustituye_a UUID REFERENCES aip(id);
ALTER TABLE aip ADD COLUMN IF NOT EXISTS motivo VARCHAR(16) NOT NULL DEFAULT 'ingesta'
    CHECK (motivo IN ('ingesta', 'renovacion_lta'));

ALTER TABLE aip DROP CONSTRAINT IF EXISTS aip_version_coherente;
ALTER TABLE aip ADD CONSTRAINT aip_version_coherente
    CHECK ((version = 1) = (sustituye_a IS NULL) AND (motivo = 'ingesta') = (sustituye_a IS NULL));

CREATE UNIQUE INDEX IF NOT EXISTS ux_aip_expediente_version ON aip (expediente_id, version);
CREATE UNIQUE INDEX IF NOT EXISTS ux_aip_sustituye_a ON aip (sustituye_a) WHERE sustituye_a IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_aip_raiz ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'aip' AND renueva_a IS NULL;

-- Rollback (manual): DROP INDEX ux_pwo_aip_raiz, ux_aip_sustituye_a, ux_aip_expediente_version;
-- ALTER TABLE aip DROP CONSTRAINT aip_version_coherente, DROP COLUMN motivo, sustituye_a, version.
