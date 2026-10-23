-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 021: retira `_b5_jsonb_o_null` (ADR-002, addendum C-7 / SEG #12).
--
-- Era el ayudante del backfill 020 (evidencia completa) y nadie más lo usa. Se había
-- creado con `search_path = tenant_x, public` capturado del runner; en vez de re-fijarlo
-- se retira: una función de apoyo sin llamadores no debe quedar en el schema.

DROP FUNCTION IF EXISTS _b5_jsonb_o_null(text);
