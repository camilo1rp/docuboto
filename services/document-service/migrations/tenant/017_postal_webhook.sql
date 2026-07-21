-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E20, F5): integración con operador postal como consumidor
-- de webhooks. El operador (4-72/Servientrega) notifica el estado de entrega a un
-- callback ENTRANTE idempotente; se autentica por HMAC-SHA256 con un secreto
-- per-tenant/per-operador (vive en el schema del tenant → un operador de otro
-- tenant no puede firmar callbacks de éste) y se deduplica por (operador,
-- event_id). Cierra el residual D-14 #3 (identidad del callback separada del
-- permiso humano de despacho). Añade `motivo_devolucion` de primera clase
-- (spec invariante 5: devolución trazable).

-- Motivo de devolución como columna propia del envío (antes solo quedaba en el
-- descripcion del evento). Se puebla al pasar a 'devuelto'.
ALTER TABLE postal_shipment
    ADD COLUMN IF NOT EXISTS motivo_devolucion TEXT;

-- Secreto HMAC por operador. FAIL-CLOSED: sin fila para un operador, TODO
-- callback de ese operador se rechaza (401) — no hay modo "abierto" por omisión
-- (mismo principio que internal_service_token / D-02).
CREATE TABLE IF NOT EXISTS postal_operator_credential (
    operador       VARCHAR(20) PRIMARY KEY,
    webhook_secret TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Registro de deduplicación del webhook (idempotencia bajo entrega at-least-once).
-- Una notificación identificada por (operador, event_id) se aplica UNA sola vez;
-- el reintento del operador cae en el UNIQUE y es un no-op (200 duplicate).
CREATE TABLE IF NOT EXISTS postal_webhook_delivery (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operador        VARCHAR(20) NOT NULL,
    event_id        VARCHAR(120) NOT NULL,
    guia            VARCHAR(60) NOT NULL,
    estado_operador VARCHAR(40) NOT NULL,
    estado_interno  VARCHAR(16),                 -- NULL si no aplicó transición
    shipment_id     UUID REFERENCES postal_shipment(id) ON DELETE SET NULL,
    aplicada        BOOLEAN NOT NULL DEFAULT FALSE,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (operador, event_id)
);
CREATE INDEX IF NOT EXISTS ix_postal_delivery_guia ON postal_webhook_delivery (guia);
