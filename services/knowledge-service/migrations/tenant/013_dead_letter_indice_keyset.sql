-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (AREA-10-08, ADR-021): índice del listado paginado de la DLQ.
-- El listado ordena por (dead_lettered_at DESC, event_id DESC) filtrando por
-- tenant, grupo y estado, y ahora pagina en SQL (sin tope de candidatas): el índice
-- permite recorrerlo sin ordenar. IDÉNTICO en los cuatro servicios y con
-- IF NOT EXISTS: el primero que migre lo crea y los demás no hacen nada.
-- Rollback: DROP INDEX IF EXISTS ix_edl_keyset;
CREATE INDEX IF NOT EXISTS ix_edl_keyset
    ON evento_dead_letter (tenant_slug, origin_group, status, dead_lettered_at DESC, event_id DESC);
