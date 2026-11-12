-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E02 §9, RF-EXP-01/08/09/10): hoja de ruta del expediente
-- APPEND-ONLY (Canal B / hoja de ruta operativa, E08 §9).
--
-- Traza lógica del expediente exigida por RF-EXP-01 ("historial de eventos" entre los
-- metadatos mínimos), RF-EXP-08 (reapertura justificada en historial) y RF-EXP-09 ("toda
-- operación queda en el historial append-only"). Distinta de `movimiento_fisico` (E17,
-- movimientos físicos de unidades) y de `public.audit_log` (E08, auditoría forense). Se
-- escribe en la misma transacción que cada operación del expediente y NUNCA acepta
-- UPDATE/DELETE (trigger). Visible a quien pueda LEER el expediente (acotado por clearance).

CREATE TABLE IF NOT EXISTS expediente_eventos (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    expediente_id      UUID         NOT NULL,
    numero_expediente  TEXT,
    accion             TEXT         NOT NULL,   -- abierto | radicado_vinculado | radicado_excluido
                                                -- | responsable_cambiado | reabierto | cerrado
                                                -- | transferido | reclasificado | disposicion_aplicada
    actor              UUID,                    -- quién ejecuta la operación
    comentario         TEXT,
    detalles           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ts                 TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_expediente_eventos_exp ON expediente_eventos (expediente_id, ts);

-- Inmutabilidad: rechaza UPDATE y DELETE (append-only, Ley 594/2000).
CREATE OR REPLACE FUNCTION reject_expediente_eventos_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'expediente_eventos is append-only (Ley 594/2000)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expediente_eventos_immutable ON expediente_eventos;
CREATE TRIGGER trg_expediente_eventos_immutable
    BEFORE UPDATE OR DELETE ON expediente_eventos
    FOR EACH ROW EXECUTE FUNCTION reject_expediente_eventos_mutation();
