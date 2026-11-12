-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-021 Fase 3, idempotencia): tabla de deduplicación de eventos.
--
-- Cada evento del bus canónico lleva un event_id único (UUID v4). Antes de ejecutar
-- cualquier efecto externo (envío de email, dispatch de webhooks), el worker realiza
-- un INSERT ON CONFLICT DO NOTHING en esta tabla. Si el INSERT devuelve fila, el
-- evento es nuevo y se procesa; si devuelve NULL (conflict), ya fue procesado y se
-- omite (dedup intencional — no es pérdida de datos).
--
-- Patrón insert-then-send: la fila se inserta ANTES de enviar el email. Si el worker
-- muere entre el INSERT y el envío, ese correo se pierde en el periodo de fallo.
-- Trade-off aceptado: el correo no es el registro legal; audit_log y el radicado
-- en document-service son la fuente autoritativa (ADR-021 Fase 3, Ley 594/2000).
--
-- NOTA: Esta tabla crecerá indefinidamente sin un job de retención. Un proceso de
-- limpieza periódica (DELETE ... WHERE processed_at < now() - interval '7 days')
-- DEBE programarse como tarea separada (cron / scheduled job). NO se implementa
-- aquí deliberadamente para no mezclar lógica de retención con migraciones DDL.
--
-- Rollback:
--   DROP INDEX IF EXISTS ix_notification_processed_events_at;
--   DROP TABLE IF EXISTS notification_processed_events;

CREATE TABLE IF NOT EXISTS notification_processed_events (
    event_id     TEXT        PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE notification_processed_events IS
    'Registro de event_id de eventos ya procesados por notification-service. '
    'Patrón insert-then-send (ADR-021 Fase 3): el INSERT precede a cualquier efecto '
    'externo (email, webhook). ON CONFLICT DO NOTHING garantiza idempotencia. '
    'Requiere job de retención externo para evitar crecimiento ilimitado '
    '(sugerencia: DELETE WHERE processed_at < now() - interval ''7 days'').';

-- Índice por processed_at para facilitar el job de retención futuro.
CREATE INDEX IF NOT EXISTS ix_notification_processed_events_at
    ON notification_processed_events (processed_at);
