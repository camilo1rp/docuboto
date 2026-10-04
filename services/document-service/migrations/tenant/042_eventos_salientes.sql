-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 042 (AREA-09-05): OUTBOX de eventos de dominio.
--
-- La reclasificación (y el alta y la anulación) publicaba su evento DESPUÉS del
-- commit, best-effort: si Redis caía justo ahí, el UPDATE del nivel quedaba
-- confirmado y el evento se perdía. knowledge-service deriva el nivel de sus
-- fragmentos SOLO del evento, así que un 1→3 perdido dejaba el fragmento en nivel
-- 1 y la búsqueda/RAG lo servía a quien tiene clearance 1 (y lo hacía embebible
-- por un proveedor externo).
--
-- Ahora el evento se inserta AQUÍ, en la MISMA transacción que el UPDATE y el
-- asiento de auditoría: o existen los tres o ninguno. Un relé (camino rápido tras
-- el commit + barrido de respaldo) lo publica y marca `enviado_at`. Entrega
-- at-least-once: el consumidor ya es idempotente por `event_id`/`dedup_key`.
--
-- `event_id` y `occurred_at` se fijan al ENCOLAR, no al publicar: el reintento
-- reenvía el MISMO sobre. Contiene el payload del evento (puede traer asunto del
-- radicado): vive en el schema del tenant, igual que `radicados`, y las filas ya
-- enviadas se retiran pasada la retención.
--
-- Idempotente. Rollback: DROP TABLE IF EXISTS eventos_salientes;

CREATE TABLE IF NOT EXISTS eventos_salientes (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id      TEXT        NOT NULL UNIQUE,
    stream        TEXT        NOT NULL,
    tipo          TEXT        NOT NULL,
    tenant_slug   TEXT        NOT NULL,
    actor         TEXT,
    dedup_key     TEXT,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at   TIMESTAMPTZ NOT NULL,
    creado_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    enviado_at    TIMESTAMPTZ,
    intentos      INTEGER     NOT NULL DEFAULT 0,
    ultimo_error  TEXT
);

-- La cola de pendientes es la fracción pequeña que el relé recorre.
CREATE INDEX IF NOT EXISTS ix_eventos_salientes_pendientes
    ON eventos_salientes (id) WHERE enviado_at IS NULL;

COMMENT ON TABLE eventos_salientes IS
    'Outbox transaccional (AREA-09-05): el evento nace en la misma tx que el cambio.';
