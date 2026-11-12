-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- 051 · La tipología documental viaja con el vínculo (E15 perfil v4, RT-15
-- art. 4.3.2.3; Acuerdo AGN 001/2024).
--
-- `expediente_indice_item.tipologia` existe desde el perfil v2 y NADIE la
-- poblaba: `IndexService._items_from` construye orden, título, fecha, huella,
-- formato, tamaño, folios, nivel, estado y dependencia productora — y omite
-- `tipologia`, que se queda en su `None` por defecto. El resultado es un
-- índice donde las 5 entradas de un expediente declaran tipología VACÍA
-- mientras el `doc_class` de cada radicado existe y es correcto.
--
-- Lo que lo hace serio en vez de cosmético: el XSD declara `<tipologia>` con
-- `minOccurs` implícito 1 y tipo `xs:string`, que ADMITE cadena vacía. El
-- índice se emite, valida contra el perfil v4 y se firma — diciendo nada sobre
-- la clase documental de lo que ampara. Un esquema no puede cazar un elemento
-- obligatorio que se cumple con vacío: solo puede cazarlo quien mire el dato.
--
-- Por qué en el VÍNCULO y no resuelto al generar: archive-service no lee la
-- tabla `radicados` — cero consultas, frontera intacta— y meter una llamada de
-- red dentro de la generación de un artefacto que se firma añadiría un modo de
-- fallo a la prueba misma. Es la «Opción A» que ya usan `formato`,
-- `tamano_bytes`, `folios` y la pareja de dependencia productora: lo aporta
-- quien vincula, que sí tiene el dato delante.
--
-- NULL sigue siendo válido: significa «no declarado», igual que sus cuatro
-- hermanas. Los vínculos anteriores a esta migración lo conservan, y el índice
-- que se regenere sobre ellos seguirá emitiendo la tipología vacía — este
-- cambio no reescribe el pasado, abre el camino para que deje de ocurrir.

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS tipologia text;

COMMENT ON COLUMN expediente_radicados.tipologia IS
    'Tipología documental (doc_class del radicado) aportada al vincular '
    '(Opción A, E15 perfil v4 / RT-15 art. 4.3.2.3). Se materializa como '
    '<tipologia> del índice electrónico. NULL = no declarado.';
