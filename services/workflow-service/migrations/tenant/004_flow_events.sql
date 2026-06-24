-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E05, RF-FLU-02): historial de trámite APPEND-ONLY.
--
-- Traza inalterable exigida por Ley 594/2000 / Acuerdo AGN 060/2001. A diferencia
-- de `flow_steps` (proyección mutable del paso activo), `flow_events` registra
-- cada transacción de trámite y NUNCA acepta UPDATE/DELETE (trigger). Se escribe
-- en la MISMA transacción que el paso (no hay paso sin evento).

CREATE TABLE IF NOT EXISTS flow_events (
    id              UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id     UUID         NOT NULL,
    tracking_number VARCHAR(50)  NOT NULL,
    tipo_tx         VARCHAR(40)  NOT NULL,   -- assign | transfer | return | close | complete | ...
    from_dept       TEXT,
    to_dept         TEXT,
    actor           UUID,                    -- quién ejecuta la transacción
    comentario      TEXT,
    detalles        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_flow_events_radicado ON flow_events (radicado_id, ts);

-- Inmutabilidad: rechaza UPDATE y DELETE (append-only).
CREATE OR REPLACE FUNCTION reject_flow_events_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'flow_events is append-only (Ley 594/2000)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_flow_events_immutable ON flow_events;
CREATE TRIGGER trg_flow_events_immutable
    BEFORE UPDATE OR DELETE ON flow_events
    FOR EACH ROW EXECUTE FUNCTION reject_flow_events_mutation();
