-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Migración de tenant (E10 / E12, AREA-07-10): la firma PERSONAL del acta de transferencia
-- entra en el almacén WORM.
--
-- El acta de entrega puede llevar hasta tres firmas personales XAdES —quien la elaboró, quien
-- la entregó y quien la recibió—, cada una un XML firmado en `files`. Solo el SELLO
-- INSTITUCIONAL del acta (`acta_transferencia`) tenía Object-Lock: el XML de cada firma
-- personal vivía en el bucket de documentos, sin retención, borrable por la vía normal de
-- purga. La firma personal es la evidencia de QUIÉN respondió por el traspaso.
--
-- Tres `tipo` y no uno: la protección es SINGLETON por (expediente, tipo) —idempotencia y
-- conflicto de `file_id` descansan en eso— y un acta tiene hasta tres firmas del mismo
-- expediente. Un `tipo` por rol deja intacta toda esa mecánica (índice único parcial por
-- tipo, como las migraciones 009/010/014) en vez de abrir una variante «N por expediente».
-- Un expediente alcanza `recibida` A LO SUMO UNA VEZ en su vida (ver la 010), así que cada
-- rol firma una sola vez por expediente.
--
-- Los tres son INSTRUMENTOS DE CONTROL (`app.core.preservacion_tipos`): sobreviven al
-- contenido y no postergan su disposición, como el acta que firman. El `legal_hold` los
-- cubre igual (es incondicional).
--
-- Aditiva/idempotente: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT, y CREATE UNIQUE INDEX
-- IF NOT EXISTS. El CHECK de pareja tipo↔propietario (014) no cambia: `tipo <>
-- 'acta_eliminacion'` ya exige `objeto_tipo = 'expediente'`.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    -- En UNA línea a propósito: `tests/test_preservacion_tipos_guard.py` extrae el CHECK vigente
    -- línea a línea.
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion', 'acta_firma_elabora', 'acta_firma_entrega', 'acta_firma_recibe'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_acta_firma_elabora
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_elabora';
CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_acta_firma_entrega
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_entrega';
CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_acta_firma_recibe
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_firma_recibe';

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_acta_firma_recibe;
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_acta_firma_entrega;
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_acta_firma_elabora;
-- (revertir el CHECK al de la migración 014 exige que no existan filas de estos tipos)
