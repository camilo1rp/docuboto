-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 021 (FIRMA-2, ronda 3, ADR-030): SOBRE DE REVELACIÓN del fundamento.
--
-- Por cada constancia de «firma inobtenible» y por cada revocación, archive-service sella con el
-- sello institucional un XML `{id, acto, texto, sal_hex, compromiso, algoritmo}`: el texto del
-- fundamento y la sal que dan sentido al compromiso del XML principal. Sin él, el texto y la sal
-- vivirían SOLO en la base. Se protege con Object-Lock como el resto de la cadena.
--
-- Cambios:
--   * 6 `tipo` nuevos: `acta_firma_excepcion_sobre_<rol>` y `acta_firma_revocacion_sobre_<rol>`.
--     Instrumentos de control (`app.core.preservacion_tipos`): prueba del acto, no contenido.
--   * Entran en MULTI_ARTEFACTO_TIPOS: identidad (expediente_id, tipo, file_id), como los demás
--     eslabones de la cadena. Se recrea `ux_pwo_multi_artefacto_file` con los 18 tipos.
--
-- Idempotente: DROP CONSTRAINT/INDEX IF EXISTS + ADD CONSTRAINT + CREATE UNIQUE INDEX IF NOT EXISTS.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    -- En UNA línea a propósito: `tests/test_preservacion_tipos_guard.py` extrae el CHECK vigente
    -- línea a línea.
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion', 'acta_firma_elabora', 'acta_firma_entrega', 'acta_firma_recibe', 'acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe', 'acta_firma_revocacion_elabora', 'acta_firma_revocacion_entrega', 'acta_firma_revocacion_recibe', 'acta_firma_revocacion_personal_elabora', 'acta_firma_revocacion_personal_entrega', 'acta_firma_revocacion_personal_recibe', 'acta_firma_excepcion_sobre_elabora', 'acta_firma_excepcion_sobre_entrega', 'acta_firma_excepcion_sobre_recibe', 'acta_firma_revocacion_sobre_elabora', 'acta_firma_revocacion_sobre_entrega', 'acta_firma_revocacion_sobre_recibe'));

DROP INDEX IF EXISTS ux_pwo_multi_artefacto_file;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_multi_artefacto_file
    ON preservacion_worm_objeto (expediente_id, tipo, file_id)
    WHERE file_id IS NOT NULL
      AND tipo = ANY (ARRAY['acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe', 'acta_firma_revocacion_elabora', 'acta_firma_revocacion_entrega', 'acta_firma_revocacion_recibe', 'acta_firma_revocacion_personal_elabora', 'acta_firma_revocacion_personal_entrega', 'acta_firma_revocacion_personal_recibe', 'acta_firma_excepcion_sobre_elabora', 'acta_firma_excepcion_sobre_entrega', 'acta_firma_excepcion_sobre_recibe', 'acta_firma_revocacion_sobre_elabora', 'acta_firma_revocacion_sobre_entrega', 'acta_firma_revocacion_sobre_recibe']::text[]);

-- Rollback (manual): exige que no existan filas de los tipos `*_sobre_*`.
-- DROP INDEX IF EXISTS ux_pwo_multi_artefacto_file; (recrear con los 12 tipos de la 020 y revertir el CHECK)
