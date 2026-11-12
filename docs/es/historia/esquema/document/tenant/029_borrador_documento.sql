-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E23, RF-PRO-03): el borrador produce un DOCUMENTO.
--
-- Hasta aquí el cuerpo del borrador acababa en una columna de texto del
-- radicado (`observations`). Un documento de archivo no es un campo de texto:
-- tiene formato de conservación, huella y existencia propia como fichero.

ALTER TABLE borrador
    -- El fichero generado, cuando ya se produjo. NULL mientras el borrador es
    -- solo texto. NO se borra al radicar: es la prueba de qué se generó.
    ADD COLUMN IF NOT EXISTS documento_file_id UUID,
    ADD COLUMN IF NOT EXISTS documento_sha256  CHAR(64),
    ADD COLUMN IF NOT EXISTS documento_formato TEXT,

    -- ── EL CONTEXTO CONGELADO, y esto es lo que impide falsificar el pasado ──
    --
    -- Los valores con que se combinó, tal como eran en el momento de producir.
    -- Sin esta copia, cambiar mañana la ficha de un tercero o el nombre de una
    -- dependencia haría que la explicación de un documento ya radicado dijera
    -- otra cosa: información errónea sobre un acto hecho con datos que eran
    -- verdad entonces.
    --
    -- Es exactamente el mismo razonamiento que la dirección de un envío
    -- (`radicado_actor`): el dato usado pertenece AL ACTO, no al sujeto. Y por
    -- el mismo motivo va SIN clave foránea a ningún catálogo — una FK lo ataría
    -- al valor vigente, que es justo lo que no debe pasar.
    ADD COLUMN IF NOT EXISTS combinacion       JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN borrador.documento_file_id IS
    'UUID en storage-service del PDF/A producido (E23, RF-PRO-03). FK lógica: no '
    'hay integridad referencial entre servicios.';

COMMENT ON COLUMN borrador.documento_sha256 IS
    'Huella del documento producido. Es lo que un visto bueno afirma haber leído '
    'y lo que una firma compromete (E23, RF-PRO-09).';

COMMENT ON COLUMN borrador.combinacion IS
    'Contexto CONGELADO con el que se combinó la plantilla. Cambiar la plantilla '
    'o la ficha de un tercero después NO reescribe lo ya producido: el dato usado '
    'pertenece al acto, no al sujeto (mismo criterio que radicado_actor).';
