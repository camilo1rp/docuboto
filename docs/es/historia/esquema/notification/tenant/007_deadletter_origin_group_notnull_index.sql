-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-021, seguimiento no-bloqueante Fase 5): origin_group
-- NOT NULL + índice compuesto sobre la tabla COMPARTIDA evento_dead_letter.
--
-- SEGURA por diseño: origin_group nunca se inserta NULL (reclaim_stale en
-- orpycamcp_common.events siempre escribe dead_letter_group=GROUP y los 3 workers
-- (signature/workflow/notification) pasan origin_group=failure_meta["dead_letter_group"]).
-- Ver ADR-021 §D.
--
-- Guard defensivo: además, se sanea cualquier fila legada con origin_group NULL antes
-- del SET NOT NULL para no abortar la migración en un entorno ya desplegado (en dev es
-- no-op). El sentinel 'unknown' es invisible a TODAS las queries por-servicio (ningún
-- servicio consulta ese origin_group) → la fila queda fail-closed, sin mis-atribución.
--
-- IDEMPOTENTE y sin pisarse entre los 3 servicios: actúan sobre la MISMA tabla
-- física; SET NOT NULL es no-op si ya es NOT NULL (PG15) y CREATE INDEX IF NOT
-- EXISTS lo crea el primero que corra, los demás no-op. El nombre del índice es
-- ÚNICO y compartido: ix_edl_tenant_group_status (NO usar nombres por-servicio;
-- esta misma migración 007 la aplican también signature-service y
-- workflow-service bajo su propio número de secuencia).
--
-- Rollback:
--   DROP INDEX IF EXISTS ix_edl_tenant_group_status;
--   ALTER TABLE evento_dead_letter ALTER COLUMN origin_group DROP NOT NULL;

UPDATE evento_dead_letter SET origin_group = 'unknown' WHERE origin_group IS NULL;

ALTER TABLE evento_dead_letter
    ALTER COLUMN origin_group SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_edl_tenant_group_status
    ON evento_dead_letter (tenant_slug, origin_group, status);

-- Nota índice: orden (tenant_slug, origin_group, status) cubre el predicado de
-- list_pending/list_scoped_for_clearance (WHERE tenant_slug=$1 AND status=$2 AND
-- origin_group=$3, todas igualdades); el orden de columnas del índice es
-- independiente del orden en el WHERE.
