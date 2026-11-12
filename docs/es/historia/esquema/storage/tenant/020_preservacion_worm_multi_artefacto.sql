-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 020 (FIRMA-2, ADR-030 addendum): la constancia de «firma inobtenible» se
-- puede RE-DECLARAR tras revocarla, y la REVOCACIÓN entra en el almacén WORM como prueba.
--
-- Cambios:
--   * 6 `tipo` nuevos: `acta_firma_revocacion_<rol>` (sello institucional de la revocación) y
--     `acta_firma_revocacion_personal_<rol>` (firma personal de quien revoca). Instrumentos de
--     control, igual que las constancias (`app.core.preservacion_tipos`).
--   * Los 12 tipos de constancia/revocación pasan de SINGLETON por (expediente, tipo) a
--     multi-artefacto: un rol acumula una cadena constancia -> revocación -> constancia ...
--     La identidad es (expediente_id, tipo, file_id). Se retiran los 6 índices únicos
--     parciales por (expediente_id) de la 019 y se crea UNO por (expediente_id, tipo, file_id).
--   * `get_by_expediente_and_tipo(..., file_id)` pasa a exigir `file_id` para estos tipos
--     (ver `PreservacionWormRepository`): buscar solo por tipo y tomar el más reciente dejaría
--     la 2.ª constancia sin Object-Lock.
--
-- (El predicado del índice usa `= ANY (ARRAY[...])` y no `IN (...)`: el guard de taxonomía lee el
-- ÚLTIMO `tipo IN (...)` de las migraciones como el CHECK vigente.)
--
-- Idempotente: DROP CONSTRAINT/INDEX IF EXISTS + ADD CONSTRAINT + CREATE UNIQUE INDEX IF NOT EXISTS.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    -- En UNA línea a propósito: `tests/test_preservacion_tipos_guard.py` extrae el CHECK vigente
    -- línea a línea.
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion', 'acta_firma_elabora', 'acta_firma_entrega', 'acta_firma_recibe', 'acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe', 'acta_firma_revocacion_elabora', 'acta_firma_revocacion_entrega', 'acta_firma_revocacion_recibe', 'acta_firma_revocacion_personal_elabora', 'acta_firma_revocacion_personal_entrega', 'acta_firma_revocacion_personal_recibe'));

DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_elabora;
DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_entrega;
DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_recibe;
DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_elabora;
DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_entrega;
DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_recibe;

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_multi_artefacto_file
    ON preservacion_worm_objeto (expediente_id, tipo, file_id)
    WHERE file_id IS NOT NULL
      AND tipo = ANY (ARRAY['acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe', 'acta_firma_revocacion_elabora', 'acta_firma_revocacion_entrega', 'acta_firma_revocacion_recibe', 'acta_firma_revocacion_personal_elabora', 'acta_firma_revocacion_personal_entrega', 'acta_firma_revocacion_personal_recibe']::text[]);

-- Rollback (manual, no automatizado por el runner): exige que no existan filas de los tipos de
-- revocación ni más de una fila por (expediente, tipo) de constancia.
-- DROP INDEX IF EXISTS ux_pwo_multi_artefacto_file;
-- (recrear los 6 ux_pwo_acta_firma_excepcion_* de la 019 y revertir el CHECK al de la 019)
