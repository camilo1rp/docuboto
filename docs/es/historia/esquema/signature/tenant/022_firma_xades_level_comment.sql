-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 022 (AREA-14-11): corrige el COMMENT de `firma.xades_level` (migración 008).
-- search_path = tenant_{slug} (ADR-012).
--
-- La 008 decía «T/LT/LTA reservados» y, en su CHECK, «XAdES-T reservado: TSA/RFC 3161, no
-- implementado». Hoy XAdES-T (sello RFC 3161, TSA local o cliente remoto real), XAdES-LT y
-- XAdES-LTA se producen. La 008 ya está aplicada y no se edita: el catálogo se corrige aquí.
--
-- Idempotente: COMMENT ON es declarativo.

COMMENT ON COLUMN firma.xades_level IS
    'Nivel XAdES realmente alcanzado: XAdES-B | XAdES-T (sello RFC 3161) | XAdES-LT | XAdES-LTA. '
    'NULL para firmas nativa (HMAC, sin XML-DSig). Todos tienen productor; ver '
    'app.providers.base.SignatureFormat.';
