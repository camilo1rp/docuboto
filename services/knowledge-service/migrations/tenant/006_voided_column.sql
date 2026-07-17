-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21): cierra el residual "purga-en-anulación" (hallazgo
-- archival-compliance-auditor) — hoy un radicado ANULADO sigue siendo consultable
-- en el índice semántico porque el único mecanismo de exclusión existente es el
-- tombstone POR NIVEL (`tombstone_by_source`, embedding = NULL), que es reversible
-- a propósito: una desclasificación legítima (RESERVADA→PUBLICA) debe poder
-- reembeberlo. La anulación de un radicado es TERMINAL (Ley 594/2000 — el radicado
-- no se des-anula), así que necesita un marcador PROPIO que ningún `created`
-- reprocesado (redelivery at-least-once, incluso tras la anulación) pueda limpiar.
--
-- `voided` es ese marcador: distinto de "tombstone por nivel" (embedding NULL,
-- reversible) — `voided = true` es permanente, `ingest_radicado_created` lo
-- respeta incondicionalmente (SKIP total, ver `app/services/ingestion.py`) y
-- ninguna ruta de creación/reclasificación lo vuelve a poner en `false`.
--
-- `ADD COLUMN IF NOT EXISTS` es naturalmente idempotente (sin necesidad de un
-- bloque DO) — re-ejecución segura, mismo criterio que la migración 005.

ALTER TABLE knowledge_chunk
    ADD COLUMN IF NOT EXISTS voided BOOLEAN NOT NULL DEFAULT false;
