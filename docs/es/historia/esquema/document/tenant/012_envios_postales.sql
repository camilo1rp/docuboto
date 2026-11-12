-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E20, F5/F6, vía E11): envíos postales de radicados.
--
-- Integración con operadores postales (4-72/Servientrega): un radicado de salida
-- se despacha con una guía del operador; el estado de entrega se actualiza (por
-- callback del operador o sondeo) y se conserva el historial append-only. El
-- operador concreto es un proveedor pluggable (stub por defecto).

CREATE TABLE IF NOT EXISTS postal_shipment (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    radicado_id   UUID NOT NULL REFERENCES radicados(id) ON DELETE CASCADE,
    operador      VARCHAR(20) NOT NULL,          -- 4-72 | servientrega | otro
    guia          VARCHAR(60) NOT NULL,
    estado        VARCHAR(16) NOT NULL DEFAULT 'registrado'
                  CHECK (estado IN ('registrado','en_transito','entregado','devuelto','fallido')),
    destinatario  TEXT,
    direccion     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_postal_radicado ON postal_shipment (radicado_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_postal_guia ON postal_shipment (operador, guia);

CREATE TABLE IF NOT EXISTS postal_shipment_event (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id UUID NOT NULL REFERENCES postal_shipment(id) ON DELETE CASCADE,
    estado      VARCHAR(16) NOT NULL,
    descripcion TEXT,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_postal_event_shipment ON postal_shipment_event (shipment_id, ts);
