-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Fusión de terceros duplicados (RF-RAD-05).
--
-- El Orfeo del que deriva este sistema acabó con el mismo señor treinta veces en
-- el catálogo. Aquí la primera defensa es la búsqueda sin tildes al radicar
-- —encontrar lo que ya está—, pero los duplicados que YA existen necesitan una
-- salida, y hasta ahora no había ninguna: ni pantalla de directorio, ni forma de
-- consolidar dos fichas de la misma persona.
--
-- ── La fusión NO reescribe nada ───────────────────────────────────────────
--
-- Fusionar marca al absorbido con `fusionado_en` y **no toca `radicado_actor`.
-- Ni una fila.** El primer diseño repuntaba el `tercero_id` de los vínculos, y
-- el trigger de la migración 026 lo impidió: incluye `tercero_id` entre las
-- columnas inmutables, con el mensaje «los datos de un actor son los USADOS en
-- el envío y no se editan: cambiarlos daría información errónea sobre un acto
-- ya ocurrido». Y tiene razón — a QUÉ ficha del catálogo se atendió a alguien
-- también es parte de lo que pasó: ese día existían dos fichas y se usó una.
--
-- Así que la fusión solo añade lo que se supo DESPUÉS: estas dos son la misma
-- persona. Quien lee resuelve a través de `fusionado_en`; el pasado sigue
-- diciendo la verdad de su momento. Por eso tampoco se borra la fila: el
-- vínculo antiguo la sigue nombrando y tiene que poder resolverse.

ALTER TABLE tercero
    ADD COLUMN IF NOT EXISTS fusionado_en uuid REFERENCES tercero (id),
    ADD COLUMN IF NOT EXISTS fusionado_at timestamptz,
    ADD COLUMN IF NOT EXISTS fusionado_por uuid;

COMMENT ON COLUMN tercero.fusionado_en IS
    'Si no es NULL, esta ficha se fusionó en la que apunta: es un duplicado ya '
    'consolidado. No se borra para no perder el rastro de los radicado_actor '
    'que se repuntaron. Las fichas fusionadas se excluyen de la búsqueda.';

-- Una ficha fusionada no puede a su vez absorber: encadenar fusiones haría que
-- el destino de un vínculo dependiera de recorrer una cadena, y una cadena con
-- un ciclo colgaría al lector. Se fusiona SIEMPRE contra una ficha viva.
ALTER TABLE tercero
    DROP CONSTRAINT IF EXISTS chk_tercero_fusion_no_a_si_mismo;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_fusion_no_a_si_mismo
    CHECK (fusionado_en IS NULL OR fusionado_en <> id);

-- Los tres campos van juntos o no van: una fusión sin fecha ni responsable es
-- un acto sin autor sobre el catálogo de una entidad.
ALTER TABLE tercero
    DROP CONSTRAINT IF EXISTS chk_tercero_fusion_completa;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_fusion_completa
    CHECK (
        (fusionado_en IS NULL AND fusionado_at IS NULL)
        OR (fusionado_en IS NOT NULL AND fusionado_at IS NOT NULL)
    );

-- Índice para el listado de duplicados: agrupa por nombre NORMALIZADO (sin
-- tildes ni mayúsculas), que es como se reconocen «José Pérez» y «Jose Perez».
-- `immutable_unaccent` no existe: `unaccent()` es STABLE, así que no vale para
-- un índice de expresión — se usa la misma configuración de texto que ya emplea
-- la búsqueda (`spanish_unaccent`), que sí es indexable.
CREATE INDEX IF NOT EXISTS ix_tercero_activo_nombre
    ON tercero (lower(nombre))
    WHERE fusionado_en IS NULL;

-- Y para el eje que de verdad identifica: dos fichas con el mismo documento son
-- la misma persona, sin discusión posible.
CREATE INDEX IF NOT EXISTS ix_tercero_documento
    ON tercero (tipo_documento, numero_documento)
    WHERE numero_documento IS NOT NULL AND fusionado_en IS NULL;
