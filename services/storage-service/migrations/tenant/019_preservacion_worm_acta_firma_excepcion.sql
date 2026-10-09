-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Migración de tenant (E10 / E12, AREA-07-10): la CONSTANCIA de «firma inobtenible» del acta
-- de transferencia entra en el almacén WORM.
--
-- Cuando una parte del acta no puede firmar, archive-service deja una constancia (XML canónico
-- que cubre la huella del acta congelada, la parte, el motivo, la huella del fundamento y la
-- referencia documental) sellada con el sello institucional XAdES-T y, si el declarante tiene
-- credencial, firmada también por él. Esa constancia es la PRUEBA de por qué el acta no
-- tiene la firma de una parte: sin Object-Lock cualquiera con acceso al bucket podría
-- borrarla y el acta quedaría con un hueco sin explicación.
--
-- Seis `tipo`: UNO por rol y por artefacto (sello institucional / firma personal del
-- declarante). La protección es SINGLETON por (expediente, tipo) —idempotencia y conflicto de
-- `file_id` descansan en eso— y un expediente alcanza `recibida` a lo sumo una vez, así que
-- cada rol tiene a lo sumo una constancia (UNIQUE (transferencia_id, rol) en archive).
--
-- Instrumentos de control (`app.core.preservacion_tipos`): sobreviven al contenido y no
-- postergan su disposición. El `legal_hold` los cubre igual (incondicional).
--
-- Aditiva/idempotente: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT y CREATE UNIQUE INDEX IF NOT
-- EXISTS. El CHECK de pareja tipo<->propietario (014) no cambia.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    -- En UNA línea a propósito: `tests/test_preservacion_tipos_guard.py` extrae el CHECK vigente
    -- línea a línea.
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion', 'acta_firma_elabora', 'acta_firma_entrega', 'acta_firma_recibe', 'acta_firma_excepcion_elabora', 'acta_firma_excepcion_entrega', 'acta_firma_excepcion_recibe', 'acta_firma_excepcion_personal_elabora', 'acta_firma_excepcion_personal_entrega', 'acta_firma_excepcion_personal_recibe'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_elabora
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_elabora';

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_entrega
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_entrega';

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_recibe
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_recibe';

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_personal_elabora
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_personal_elabora';

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_personal_entrega
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_personal_entrega';

CREATE UNIQUE INDEX IF NOT EXISTS ux_pwo_acta_firma_excepcion_personal_recibe
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_excepcion_personal_recibe';

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_recibe;
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_entrega;
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_elabora;
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_recibe;
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_entrega;
-- DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_elabora;
-- (revertir el CHECK al de la migración 018 exige que no existan filas de estos tipos)
