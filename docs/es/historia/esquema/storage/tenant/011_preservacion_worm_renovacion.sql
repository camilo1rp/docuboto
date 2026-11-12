-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt: renovación de retención WORM para series de
-- disposición Conservación Total, CT): registra CUÁNTAS veces y CUÁNDO se
-- extendió (nunca acortó) el `retain_until` COMPLIANCE de un objeto ya
-- protegido (`preservacion_worm_objeto`, migración 006).
--
-- Objetos bajo Object-Lock COMPLIANCE llevan `retain_until = fecha_inicio +
-- retención TOTAL de la TRD` (Increment A/Inc.1). Para series CT el
-- documento debe conservarse PARA SIEMPRE (Ley 594/2000 + Acuerdo AGN
-- 001/2024), pero el lock COMPLIANCE VENCE en esa fecha -- sin renovación
-- periódica el objeto redevendría borrable tras vencer. `POST
-- /preservacion/renovar-retencion[-internal]` extiende `retain_until`
-- (ventana rodante `now() + total_TRD`, nunca una fecha lejana/9999) antes
-- del vencimiento; estas dos columnas son la traza de esa extensión.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS`.

ALTER TABLE preservacion_worm_objeto
    ADD COLUMN IF NOT EXISTS renovado_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS renovaciones  INT NOT NULL DEFAULT 0;

COMMENT ON COLUMN preservacion_worm_objeto.renovado_at IS
    'Instante de la última extensión CONFIRMADA de retain_until (E10 debt, renovación CT). NULL si el objeto nunca fue renovado.';
COMMENT ON COLUMN preservacion_worm_objeto.renovaciones IS
    'Número de extensiones CONFIRMADAS de retain_until (no cuenta llamadas no-op, ver PreservationWormService.renovar_retencion).';

-- Rollback (manual, no automatizado por el runner):
-- ALTER TABLE preservacion_worm_objeto DROP COLUMN IF EXISTS renovado_at;
-- ALTER TABLE preservacion_worm_objeto DROP COLUMN IF EXISTS renovaciones;
