-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15 finalización RT-15, perfil v3 "conforme extendido",
-- Acuerdo AGN 001/2024 art. 4.3.2.3): orden_documental ESTABLE + soft-delete
-- del vínculo expediente-radicado.
--
-- Decisiones del usuario:
-- - `orden_documental` se asigna UNA vez al vincular (MAX+1 por expediente,
--   calculado de forma atómica en el repositorio) y es INMUTABLE: nunca se
--   renumera ni se reutiliza, ni siquiera cuando el radicado se excluye.
-- - La exclusión (antes DELETE físico en `unlink_radicado`) pasa a ser
--   SOFT-DELETE: la fila permanece con estado='excluido', para que el índice
--   electrónico pueda mostrar el "hueco" trazable en el orden documental en
--   vez de renumerar/perder evidencia de que un documento estuvo vinculado
--   (paridad con el Orfeo legado: estado=2, sin borrado físico).

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS orden_documental INTEGER;

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS estado TEXT NOT NULL DEFAULT 'vinculado';

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS excluido_at TIMESTAMPTZ;

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS excluido_causal TEXT;

-- ADD CONSTRAINT no admite IF NOT EXISTS en PG15 — DROP+ADD es idempotente
-- (mismo patrón que 013_expediente_security_level.sql).
ALTER TABLE expediente_radicados
    DROP CONSTRAINT IF EXISTS ck_expediente_radicados_estado;
ALTER TABLE expediente_radicados
    ADD CONSTRAINT ck_expediente_radicados_estado
    CHECK (estado IN ('vinculado', 'excluido'));

-- Backfill determinista: a las filas existentes (todas 'vinculado' — el
-- unlink previo era DELETE físico, no hay excluidos que reconstruir) se les
-- asigna orden_documental por ORDER BY added_at dentro de cada expediente.
-- Idempotente: solo toca filas con orden_documental IS NULL, así que
-- reejecutar la migración (o correrla tras una recarga parcial) no reasigna
-- el orden ya fijado.
WITH ranked AS (
    SELECT expediente_id,
           radicado_id,
           row_number() OVER (
               PARTITION BY expediente_id ORDER BY added_at ASC, radicado_id ASC
           ) AS rn
    FROM expediente_radicados
    WHERE orden_documental IS NULL
)
UPDATE expediente_radicados er
   SET orden_documental = ranked.rn
  FROM ranked
 WHERE er.expediente_id = ranked.expediente_id
   AND er.radicado_id = ranked.radicado_id;

-- Índice de apoyo a ORDER BY orden_documental (índice electrónico, foliado).
CREATE INDEX IF NOT EXISTS ix_exp_rad_orden
    ON expediente_radicados (expediente_id, orden_documental);

-- Unicidad parcial: ningún expediente puede tener dos filas (vinculadas o
-- excluidas) compartiendo el mismo ordinal — detecta colisiones de asignación
-- (el ordinal de una fila excluida también debe permanecer único: es el
-- "hueco" trazable, no se reutiliza).
CREATE UNIQUE INDEX IF NOT EXISTS ux_exp_rad_orden
    ON expediente_radicados (expediente_id, orden_documental)
    WHERE orden_documental IS NOT NULL;

COMMENT ON COLUMN expediente_radicados.orden_documental IS
    'Orden documental ESTABLE (RT-15 art. 4.3.2.3): asignado UNA sola vez al '
    'vincular (MAX+1 por expediente, atómico), inmutable — nunca se renumera '
    'ni se reutiliza tras una exclusión (soft-delete). NULL solo de forma '
    'transitoria antes del backfill.';
COMMENT ON COLUMN expediente_radicados.estado IS
    'vinculado = vigente en el expediente; excluido = soft-delete (unlink): la '
    'fila permanece para que el índice electrónico muestre el hueco trazable '
    '(Acuerdo AGN 001/2024 art. 4.3.2.3). Paridad con el Orfeo legado '
    '(estado=2, sin DELETE físico).';
COMMENT ON COLUMN expediente_radicados.excluido_at IS
    'Marca de tiempo de la exclusión (soft-delete); NULL mientras estado=vinculado.';
COMMENT ON COLUMN expediente_radicados.excluido_causal IS
    'Causal opcional de la exclusión, aportado por quien excluye (unlink).';

-- expediente_indice_item: snapshot inmutable por versión del índice — añade
-- el ordinal estable, el nivel de seguridad heredado del expediente
-- (listaControlAcceso) y el estado del vínculo (vinculado/excluido), TAL
-- COMO estaban en el momento en que se generó esa versión (E15 perfil v3
-- "conforme extendido"). NULL en versiones anteriores a v3 (v1/v2 no
-- capturaban estos campos).
ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS orden_documental INTEGER;

ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS nivel_seguridad_snapshot SMALLINT;

ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS estado TEXT;

ALTER TABLE expediente_indice_item
    DROP CONSTRAINT IF EXISTS ck_expediente_indice_item_estado;
ALTER TABLE expediente_indice_item
    ADD CONSTRAINT ck_expediente_indice_item_estado
    CHECK (estado IS NULL OR estado IN ('vinculado', 'excluido'));

COMMENT ON COLUMN expediente_indice_item.orden_documental IS
    'Snapshot del orden_documental ESTABLE del vínculo al generar esta versión '
    'del índice (E15 perfil v3); NULL en versiones anteriores a v3.';
COMMENT ON COLUMN expediente_indice_item.nivel_seguridad_snapshot IS
    'Snapshot de expedientes.nivel_seguridad al generar esta versión '
    '(listaControlAcceso, E15 perfil v3); NULL en versiones anteriores a v3.';
COMMENT ON COLUMN expediente_indice_item.estado IS
    'Snapshot de expediente_radicados.estado (vinculado/excluido) al generar '
    'esta versión, para reconstruir el hueco trazable en versiones históricas; '
    'NULL en versiones anteriores a v3.';

-- Rollback (referencia manual, no ejecutado por el runner):
-- ALTER TABLE expediente_indice_item DROP CONSTRAINT IF EXISTS ck_expediente_indice_item_estado;
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS estado;
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS nivel_seguridad_snapshot;
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS orden_documental;
-- DROP INDEX IF EXISTS ux_exp_rad_orden;
-- DROP INDEX IF EXISTS ix_exp_rad_orden;
-- ALTER TABLE expediente_radicados DROP CONSTRAINT IF EXISTS ck_expediente_radicados_estado;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS excluido_causal;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS excluido_at;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS estado;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS orden_documental;
