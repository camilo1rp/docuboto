-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06/E23): el DEFAULT de `firma.tipo_firma` pertenecía a
-- un vocabulario que ya no existe.
--
-- ── Dos migraciones sobre la misma columna, y solo una llegó ────────────────
--
-- La `002` creó la columna:
--
--     ADD COLUMN IF NOT EXISTS tipo_firma VARCHAR(30) NOT NULL
--                              DEFAULT 'electronica-nativa'
--
-- con el vocabulario de entonces (`electronica-nativa | xades | pades`). La
-- `008` quiso redefinirla:
--
--     ADD COLUMN IF NOT EXISTS tipo_firma TEXT DEFAULT 'nativa'
--
-- y ese `IF NOT EXISTS` la convirtió en **no-op**: la columna ya existía. Lo
-- único de la `008` que sí se aplicó fue su `COMMENT` —los comentarios no
-- llevan guarda—, así que la columna quedó **documentando el vocabulario nuevo
-- (`nativa | sello_sistema | personal`) y con el DEFAULT del viejo**. Nadie lo
-- notó porque el DEFAULT no se usa nunca: el repositorio nombra la columna en
-- todos sus INSERT.
--
-- Esta migración alinea el DEFAULT con el vocabulario que el código lee y que
-- el propio COMMENT de la columna declara. No toca filas existentes: un DEFAULT
-- solo afecta a inserciones futuras que omitan la columna.
--
-- No se añade un CHECK. Sería lo natural para un vocabulario cerrado, pero esta
-- base puede tener filas históricas con `electronica-nativa` de antes de la
-- `008`, y una restricción que rechaza el pasado convierte una migración en una
-- avería de despliegue. El vocabulario se sostiene en el código y en el
-- comentario.

ALTER TABLE firma
    ALTER COLUMN tipo_firma SET DEFAULT 'nativa';

COMMENT ON COLUMN firma.tipo_firma IS
    'Tipo de acto de firma: ''nativa'' (HMAC de servicio) | ''sello_sistema'' '
    '(XAdES-B con el sello institucional del tenant, atribuido a la persona '
    'que actúa vía firmante_id + audit_log, RF-FIR-11) | ''personal'' (XAdES '
    'con la credencial PKI por-usuario, E17/F4). No confundir con provider, '
    'que identifica la IMPLEMENTACION pluggable. El DEFAULT se alineo a este '
    'vocabulario en la migracion 019: la 002 lo habia fijado en '
    '''electronica-nativa'' y la 008 no pudo cambiarlo por su IF NOT EXISTS.';
