-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 017: marca de reutilización por deduplicación (AREA-09-14, carrera
-- dedup↔purga).
--
-- `upload` deduplica por SHA-256 y devuelve el `file_id` de un objeto que YA existe, sin
-- tocar nada. Mientras tanto la purga de huérfanos de una importación fallida solo ve
-- referencias COMMITEADAS: un objeto que otra importación, una subida o un correo acaba
-- de recibir por dedup, y que aún no tiene su anexo commiteado, parecía huérfano y se
-- borraba, dejando a esa operación con un `file_id` que ya no existe.
--
-- `dedup_hit_at` es el instante de la ÚLTIMA vez que un `upload` devolvió esta fila por
-- deduplicación. La rama de dedup lo escribe con un UPDATE (toma el bloqueo de fila, así
-- que se serializa con el `FOR UPDATE` de la purga) y la purga conserva SIEMPRE lo que
-- tenga la marca: un objeto compartido por dedup no es de nadie en exclusiva.
--
-- Idempotente; search_path = tenant_{slug} (ADR-012). Sin default ni backfill: NULL es
-- «nunca reutilizado por dedup», que es lo cierto para todo lo ya almacenado (y la
-- purga lo protege por sus referencias commiteadas, como hasta ahora).

ALTER TABLE files ADD COLUMN IF NOT EXISTS dedup_hit_at TIMESTAMPTZ;

COMMENT ON COLUMN files.dedup_hit_at IS
    'Última vez que un upload devolvió este objeto por deduplicación SHA-256. La purga de huérfanos nunca borra un objeto con la marca.';
