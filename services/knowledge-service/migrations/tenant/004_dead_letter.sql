-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21): tabla durable de dead-letter del worker de ingesta.
--
-- Espejo de la `evento_dead_letter` de workflow-service (ADR-021 Fase 2): almacena
-- los eventos de `orpycamcp.document.events` que agotaron los reintentos del
-- consumer-group `knowledge-ingest` sin poder ingestarse (fallo de embedding/BD).
-- El INSERT usa ON CONFLICT (event_id) DO NOTHING para idempotencia: un reintento
-- del mismo mensaje (XADD ok pero XACK falló, at-least-once) no produce
-- duplicados ni dobles trazas en audit_log.
--
-- NOTA (tabla física compartida, ver dead_letter_repository.py de workflow-service):
-- esta es una tabla NUEVA propia de knowledge-service (misma DDL que la de
-- workflow/signature/notification, pero una instancia distinta — cada servicio la
-- crea con `CREATE TABLE IF NOT EXISTS` sobre el MISMO schema del tenant). Las filas
-- se distinguen igualmente por `origin_group` ("knowledge-ingest") por si en el
-- futuro se comparte inspección/admin entre consumidores del mismo tenant.
--
-- Rollback:
--   DROP TABLE IF EXISTS evento_dead_letter;
--   DROP INDEX IF EXISTS ix_evento_dead_letter_at;

CREATE TABLE IF NOT EXISTS evento_dead_letter (
    event_id          TEXT        PRIMARY KEY,           -- ID único del sobre de evento (idempotencia)
    event_type        TEXT,
    correlation_id    TEXT,
    tenant_slug       TEXT        NOT NULL,
    payload           JSONB       NOT NULL,              -- sobre íntegro (incl. payload de negocio)
    failure           JSONB       NOT NULL,              -- failure_meta: reason, last_error, counts, etc.
    origin_stream     TEXT,
    origin_group      TEXT,
    dead_lettered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE evento_dead_letter IS
    'Copia durable (Postgres) de los eventos aparcados en el dead-letter de Redis '
    'del worker de ingesta (E21). event_id como PK garantiza idempotencia '
    '(ON CONFLICT DO NOTHING). El INSERT y el audit_log se escriben en la misma '
    'transacción: no hay dead-letter sin traza durable.';

COMMENT ON COLUMN evento_dead_letter.payload IS
    'Sobre íntegro del evento (event_id, event_type, tenant_slug, occurred_at, actor, '
    'payload de negocio). Autosuficiente para reconstrucción forense (Ley 594/2000 art. 19).';

COMMENT ON COLUMN evento_dead_letter.failure IS
    'failure_meta del helper reclaim_stale: dead_letter_reason, dead_letter_last_error, '
    'dead_letter_delivery_count, dead_lettered_at, origin stream/group/consumer.';

-- Índice por dead_lettered_at para consultas de inspección y TTL operativo.
CREATE INDEX IF NOT EXISTS ix_evento_dead_letter_at
    ON evento_dead_letter (dead_lettered_at);
