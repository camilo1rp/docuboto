-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 022 (B10 §8): renovación del ArchiveTimeStamp de una firma XAdES-LTA.
--
-- Cada renovación produce un XML NUEVO (nuevo file_id) que CONTIENE todos los ATS anteriores. Ese
-- objeto hereda la política WORM del original (mismo retain_until, modo y legal hold) y queda
-- apuntando a él: es la cadena de custodia del sello de archivo.
--
-- Cambios:
--   * `tipo` nuevo: `firma_lta_renovada`. CONTENIDO (co-expira con el índice del que hereda la
--     retención y, mientras viva, bloquea su disposición: dirección segura).
--   * `renueva_a`: fila WORM del objeto al que sustituye (NULL en el resto de los tipos).
--   * Un solo objeto WORM por (tipo, file_id) para este tipo: la renovación es idempotente.
--
-- Idempotente: DROP CONSTRAINT/INDEX IF EXISTS + ADD CONSTRAINT + CREATE UNIQUE INDEX IF NOT EXISTS.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    -- En UNA línea a propósito: `tests/test_preservacion_tipos_guard.py` extrae el CHECK vigente
    -- línea a línea.
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion', 'acta_firma_elabora', 'acta_firma_entrega', 'acta_firma_recibe', 'acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe', 'acta_firma_revocacion_elabora', 'acta_firma_revocacion_entrega', 'acta_firma_revocacion_recibe', 'acta_firma_revocacion_personal_elabora', 'acta_firma_revocacion_personal_entrega', 'acta_firma_revocacion_personal_recibe', 'acta_firma_excepcion_sobre_elabora', 'acta_firma_excepcion_sobre_entrega', 'acta_firma_excepcion_sobre_recibe', 'acta_firma_revocacion_sobre_elabora', 'acta_firma_revocacion_sobre_entrega', 'acta_firma_revocacion_sobre_recibe', 'firma_lta_renovada'));

ALTER TABLE preservacion_worm_objeto
    ADD COLUMN IF NOT EXISTS renueva_a UUID REFERENCES preservacion_worm_objeto(id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_firma_lta_renovada_file
    ON preservacion_worm_objeto (tipo, file_id)
    WHERE tipo = 'firma_lta_renovada';

-- Rollback (manual): exige que no existan filas de tipo `firma_lta_renovada`.
-- DROP INDEX IF EXISTS ux_pwo_firma_lta_renovada_file;
-- ALTER TABLE preservacion_worm_objeto DROP COLUMN IF EXISTS renueva_a;
-- (revertir el CHECK al de la 021)
