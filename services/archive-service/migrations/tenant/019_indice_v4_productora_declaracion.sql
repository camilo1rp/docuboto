-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15 perfil v4 "conforme completo", Acuerdo AGN 001/2024
-- art. 4.3.2.3): dependencia productora por-radicado (R1).
--
-- Decisión del arquitecto (contrato v4): la dependencia productora se APORTA
-- por quien vincula el radicado al expediente (igual que formato/tamano_bytes/
-- folios en 016_radicado_index_metadata.sql, "Opción A") — archive-service NO
-- hace una llamada HTTP a document-service para resolverla (el build del
-- índice/firma debe ser determinista y auto-contenido). Ambos campos son
-- OPCIONALES pero deben aportarse JUNTOS o NINGUNO (CHECK de pareja) — la
-- capa Pydantic (`RadicadoLink`) ya rechaza con 422 el caso parcial antes de
-- llegar aquí; el CHECK es la segunda barrera (ADR-013: la BD autoriza).
--
-- `expediente_indice_item` gana el mismo par como SNAPSHOT inmutable por
-- versión del índice (paridad con formato/tamano_bytes/folios y con
-- nivel_seguridad_snapshot/estado de 018_expediente_radicados_orden_soft_delete.sql)
-- — necesario para que la vista JSON de una versión HISTÓRICA del índice
-- (`GET /indice?version=N`) pueda seguir mostrando la productora tal como
-- estaba al generarse esa versión, sin depender del estado actual de
-- `expediente_radicados`.
--
-- `expediente_indice_item.fecha_declaracion` (existente desde
-- 008_expediente_indice.sql, nunca poblada) queda formalmente DEPRECADA (R2):
-- la fecha de declaración se deriva de `fecha_archivado`/`added_at`
-- (equivalencia declaracion == incorporacion, Orfeo SGD_EXP_FECH vs fech_radi)
-- — una sola fuente de verdad, sin columna redundante que poblar.

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS dependencia_productora_codigo INTEGER;

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS dependencia_productora_nombre TEXT;

-- ADD CONSTRAINT no admite IF NOT EXISTS en PG15 — se localiza por definición
-- antes de crear (mismo patrón que 016/017/018).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'expediente_radicados'
          AND con.contype = 'c'
          AND con.conname = 'expediente_radicados_dependencia_productora_pair_check'
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_dependencia_productora_pair_check
            CHECK (
                (dependencia_productora_codigo IS NULL)
                = (dependencia_productora_nombre IS NULL)
            );
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS dependencia_productora_codigo INTEGER;

ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS dependencia_productora_nombre TEXT;

COMMENT ON COLUMN expediente_radicados.dependencia_productora_codigo IS
    'Código de la dependencia productora del documento (E15 perfil v4, R1), '
    'aportado por quien vincula (Opción A, igual que formato/tamano_bytes/folios). '
    'NULL = no declarado (fila legacy); siempre acompañado de _nombre (CHECK de pareja).';
COMMENT ON COLUMN expediente_radicados.dependencia_productora_nombre IS
    'Nombre de la dependencia productora del documento (E15 perfil v4, R1); '
    'ver dependencia_productora_codigo.';
COMMENT ON COLUMN expediente_indice_item.dependencia_productora_codigo IS
    'Snapshot inmutable de expediente_radicados.dependencia_productora_codigo '
    'en la versión del índice (E15 perfil v4); NULL en versiones anteriores a v4 '
    'o en vínculos sin productora declarada.';
COMMENT ON COLUMN expediente_indice_item.dependencia_productora_nombre IS
    'Snapshot inmutable de expediente_radicados.dependencia_productora_nombre; '
    'ver dependencia_productora_codigo.';
COMMENT ON COLUMN expediente_indice_item.fecha_declaracion IS
    'DEPRECADA desde E15 perfil v4 (R2): nunca poblada. La fecha de declaración '
    'se deriva de fecha_archivado/added_at (equivalencia declaracion == '
    'incorporacion; Orfeo SGD_EXP_FECH vs fech_radi) — una sola fuente de verdad, '
    'ver <fechaDeclaracion> en el XML (index_builder.build_xml). Columna '
    'conservada NULL por compatibilidad histórica de la tabla; no usar.';

-- Rollback (referencia manual, no ejecutado por el runner):
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS dependencia_productora_nombre;
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS dependencia_productora_codigo;
-- ALTER TABLE expediente_radicados DROP CONSTRAINT IF EXISTS expediente_radicados_dependencia_productora_pair_check;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS dependencia_productora_nombre;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS dependencia_productora_codigo;
