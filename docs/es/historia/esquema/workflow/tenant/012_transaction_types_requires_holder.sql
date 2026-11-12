-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-028): tenencia de las transacciones de trámite.
--
-- `requires_holder`: la transacción solo la puede ejecutar el POSEEDOR del paso
-- activo (assigned_to = actor, o paso de dependencia sin dueño y el actor
-- pertenece a ella). Cierra el hueco por el que cualquiera con PERM_TRAMITAR
-- podía marcar como leído —y así mover el estado del radicado— un trámite ajeno.
-- Hoy solo `marcar_leido` (el único que mueve el estado a `in_progress`).
--
-- Idempotente. Rollback:
--   ALTER TABLE transaction_types DROP COLUMN IF EXISTS requires_holder;

ALTER TABLE transaction_types
    ADD COLUMN IF NOT EXISTS requires_holder BOOLEAN NOT NULL DEFAULT false;

UPDATE transaction_types
   SET requires_holder = true
 WHERE code = 'marcar_leido'
   AND requires_holder IS DISTINCT FROM true;
