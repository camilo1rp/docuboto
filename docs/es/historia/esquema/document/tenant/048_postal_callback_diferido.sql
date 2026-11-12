-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 048 (B10, ADR-035 §7): cola de callbacks postales recibidos con la
-- entidad SUSPENDIDA. search_path = tenant_{slug} (ADR-012).
--
-- El operador postal manda UNA vez cada estado de entrega y reintenta por un tiempo
-- acotado. Contestarle 503 a una firma válida hacía que el evento (p. ej. la devolución
-- que abre un plazo) se perdiera si la suspensión duraba más que sus reintentos. Ahora
-- se guarda el cuerpo tal como llegó (la firma ya se verificó) y se aplica cuando la
-- entidad se reactiva: el barrido (fase 5) solo recorre tenants activos.
--
-- `cuerpo` se conserva en bytes crudos porque es la evidencia firmada; `recibido_at` es
-- el instante de la RECEPCIÓN del aviso del operador y es el que se asienta como `ts` del
-- evento del envío (no puede fecharse en el drenaje). NO es la fecha del hecho postal: si el
-- operador la manda (`ocurrido_at`, dentro del cuerpo firmado), se guarda aparte en
-- `postal_shipment_event.ocurrido_operador_at` y es esa la que sirve de referencia de entrega.
--
-- `intentos` acota el reintento de una fila que no se puede aplicar (error no HTTP que se
-- repite): al llegar al tope del servicio la fila se descarta con asiento y no bloquea la cola.
--
-- Idempotente. Rollback: DROP TABLE postal_callback_diferido;
--   ALTER TABLE postal_shipment_event DROP COLUMN ocurrido_operador_at.
ALTER TABLE postal_shipment_event ADD COLUMN IF NOT EXISTS ocurrido_operador_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS postal_callback_diferido (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operador       VARCHAR(20)  NOT NULL,
    event_id       VARCHAR(120) NOT NULL,
    guia           VARCHAR(60)  NOT NULL,
    cuerpo         BYTEA        NOT NULL CHECK (octet_length(cuerpo) <= 65536),
    cuerpo_sha256  CHAR(64)     NOT NULL,
    firma_hmac     TEXT         NOT NULL,
    estado_interno VARCHAR(16)  NOT NULL,
    recibido_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    estado         VARCHAR(12)  NOT NULL DEFAULT 'pendiente'
                   CHECK (estado IN ('pendiente', 'procesado', 'descartado')),
    intentos       INTEGER      NOT NULL DEFAULT 0,
    ultimo_error   TEXT,
    resultado      VARCHAR(24),
    procesado_at   TIMESTAMPTZ,
    UNIQUE (operador, event_id),
    CONSTRAINT postal_diferido_cierre CHECK ((estado = 'pendiente') = (procesado_at IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_postal_diferido_pendiente
    ON postal_callback_diferido (recibido_at, id) WHERE estado = 'pendiente';
