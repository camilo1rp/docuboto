-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (R-4, ADR-021 Fase 3): tabla durable de dead-letter de eventos.
--
-- Almacena los eventos que alcanzaron el umbral de reintentos sin poder procesarse.
-- El INSERT usa ON CONFLICT (event_id) DO NOTHING para garantizar idempotencia: un
-- reintento del mismo mensaje (XADD ok pero XACK falló, at-least-once) no produce
-- duplicados ni dobles trazas en audit_log.
--
-- La columna origin_stream distingue entre eventos procedentes del stream de workflow
-- (orpycamcp.workflow.events) y del de document (orpycamcp.document.events), ya que
-- notification-service consume ambos y escribe al mismo dead-letter stream
-- (orpycamcp.notification.deadletter, ADR-021 Fase 3).
--
-- El stream Redis orpycamcp.notification.deadletter conserva su rol de buffer operativo
-- para inspección y replay; esta tabla es la copia autoritativa y durable (Postgres)
-- que satisface Ley 594/2000 art. 4 y art. 19 (trazabilidad, integridad, durabilidad).
--
-- Rollback:
--   DROP INDEX IF EXISTS ix_evento_dead_letter_at;
--   DROP TABLE IF EXISTS evento_dead_letter;

CREATE TABLE IF NOT EXISTS evento_dead_letter (
    event_id          TEXT        PRIMARY KEY,           -- ID único del sobre de evento (idempotencia)
    event_type        TEXT,
    correlation_id    TEXT,
    tenant_slug       TEXT        NOT NULL,
    payload           JSONB       NOT NULL,              -- sobre íntegro (incl. payload de negocio)
    failure           JSONB       NOT NULL,              -- failure_meta: reason, last_error, counts, etc.
    origin_stream     TEXT,                              -- workflow o document stream de origen
    origin_group      TEXT,
    dead_lettered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE evento_dead_letter IS
    'Copia durable (Postgres) de los eventos aparcados en el dead-letter de Redis. '
    'event_id como PK garantiza idempotencia (ON CONFLICT DO NOTHING). '
    'El INSERT y el audit_log se escriben en la misma transacción (R-4, ADR-021): '
    'no hay dead-letter sin traza durable. origin_stream distingue el stream de '
    'procedencia (workflow vs document) dentro del dead-letter único del servicio.';

COMMENT ON COLUMN evento_dead_letter.payload IS
    'Sobre íntegro del evento (event_id, event_type, tenant_slug, occurred_at, actor, '
    'payload de negocio). Autosuficiente para reconstrucción forense (Ley 594/2000 art. 19).';

COMMENT ON COLUMN evento_dead_letter.failure IS
    'failure_meta del helper reclaim_stale: dead_letter_reason, dead_letter_last_error, '
    'dead_letter_delivery_count, dead_lettered_at, origin stream/group/consumer.';

-- Índice por dead_lettered_at para consultas de inspección y TTL operativo.
CREATE INDEX IF NOT EXISTS ix_evento_dead_letter_at
    ON evento_dead_letter (dead_lettered_at);
