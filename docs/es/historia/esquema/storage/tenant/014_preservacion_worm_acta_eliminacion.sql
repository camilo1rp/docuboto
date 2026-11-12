-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 + E04/RF-TRD-07, 2026-08-29): el ACTA DE ELIMINACIÓN
-- entra en el almacén WORM.
--
-- Era la deuda declarada al cerrar la fase 4 de RF-TRD-07: el acta de
-- eliminación se quedó con sus tres garantías locales (trigger append-only, SHA
-- anclado en `audit_log` y firma XAdES) pero SIN Object-Lock, porque este
-- almacén asumía que todo artefacto protegido pertenece a un EXPEDIENTE — y un
-- acta de eliminación cubre N expedientes que además van a dejar de existir.
-- Clavarla bajo el id de uno de ellos no era incómodo: era incorrecto.
--
-- ── Qué cambia, y qué NO ────────────────────────────────────────────────────
--
-- `expediente_id` es TEXT **sin clave foránea** desde la migración 006 (apunta a
-- otro servicio), así que ya era de hecho un «id del propietario». Lo que
-- faltaba era decir DE QUÉ es ese id. Se añade `objeto_tipo`.
--
-- **No se renombra `expediente_id`**, y es una decisión con coste: el nombre
-- seguirá siendo impreciso para las filas de tipo `lote`. Renombrarlo tocaría
-- 142 referencias en diez módulos, de las cuales la gran mayoría hablan de
-- expedientes de verdad (constructor de AIP, repositorio de preservación); el
-- riesgo de ese diff supera al de un nombre histórico documentado. `objeto_tipo`
-- desambigua, y las consultas por expediente lo exigen explícitamente.
--
-- El emparejamiento **no es libre**: un acta de eliminación pertenece SIEMPRE a
-- un lote, y todo lo demás SIEMPRE a un expediente. Un CHECK lo impone, para que
-- no exista la fila que diga que un índice electrónico pertenece a un lote.

ALTER TABLE preservacion_worm_objeto
    ADD COLUMN IF NOT EXISTS objeto_tipo TEXT NOT NULL DEFAULT 'expediente';

COMMENT ON COLUMN preservacion_worm_objeto.expediente_id IS
    'ID del PROPIETARIO del artefacto (expediente o lote, según objeto_tipo). Conserva su nombre histórico de la migración 006; ver la cabecera de la 014.';
COMMENT ON COLUMN preservacion_worm_objeto.objeto_tipo IS
    'De qué es `expediente_id`: expediente (todo el contenido y el acta de transferencia) | lote (acta de eliminación, E04/RF-TRD-07).';

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_objeto_tipo_check
    CHECK (objeto_tipo IN ('expediente', 'lote'));

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia', 'acta_eliminacion'));

-- El emparejamiento tipo↔propietario, impuesto en la base y no solo en el
-- código: sin él podría existir una fila que afirmara que un índice electrónico
-- pertenece a un lote, y el gate de purga —que consulta por expediente— dejaría
-- de verla sin que nada avisara.
ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_pareja_check;
ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_pareja_check
    CHECK (
        (tipo = 'acta_eliminacion' AND objeto_tipo = 'lote')
        OR (tipo <> 'acta_eliminacion' AND objeto_tipo = 'expediente')
    );

-- Idempotencia de la protección, espejo exacto de los índices de las
-- migraciones 009/010/012: un reintento no crea una segunda fila.
CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_lote_acta_eliminacion
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_eliminacion';

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_lote_acta_eliminacion;
-- ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_pareja_check;
-- ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_objeto_tipo_check;
-- ALTER TABLE preservacion_worm_objeto DROP COLUMN IF EXISTS objeto_tipo;
