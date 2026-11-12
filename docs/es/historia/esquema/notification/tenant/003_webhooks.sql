-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E11, F5, ADR-018): suscripciones de webhooks salientes.
--
-- Cada tenant registra endpoints externos a los que se entregan (POST firmado
-- HMAC-SHA256) los eventos de dominio del bus canónico. event_types vacío = todos.

CREATE TABLE IF NOT EXISTS webhook_subscription (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url         TEXT NOT NULL,
    event_types TEXT[] NOT NULL DEFAULT '{}',   -- vacío = todos los eventos
    secret      TEXT,                            -- clave HMAC (firma de la entrega)
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_webhook_active ON webhook_subscription (active);
