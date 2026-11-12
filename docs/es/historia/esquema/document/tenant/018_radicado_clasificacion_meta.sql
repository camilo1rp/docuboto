-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E08): metadata de clasificación VIGENTE del radicado, para
-- el índice de información clasificada y reservada (Ley 1712/2014 art. 20 +
-- Decreto 1081/2015). El `audit_log` sigue siendo el registro LEGAL inmutable del
-- acto de clasificación (hash-chain); estas columnas son la PROYECCIÓN del estado
-- vigente para poder generar el índice con una consulta directa (sin reconstruir
-- el "último evento por objeto" del log). Se pueblan al reclasificar a nivel ≥ 2
-- y se LIMPIAN al desclasificar (nivel = 1).

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS clasificacion_fundamento TEXT,
    ADD COLUMN IF NOT EXISTS clasificacion_causal TEXT,
    ADD COLUMN IF NOT EXISTS clasificacion_plazo_meses INTEGER,
    ADD COLUMN IF NOT EXISTS clasificado_at TIMESTAMPTZ;

-- Índice parcial: el registro solo incluye los radicados clasificados/reservados
-- (nivel ≥ 2), así que el índice cubre exactamente esa población.
CREATE INDEX IF NOT EXISTS ix_radicados_clasificados
    ON radicados (nivel_seguridad, registered_at DESC)
    WHERE nivel_seguridad >= 2;
