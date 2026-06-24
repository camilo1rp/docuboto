-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (paridad legado): anulación de radicado en DOS pasos.
--
-- La ley prohíbe borrar radicados: se anulan con aprobación supervisora. Un usuario
-- SOLICITA la anulación con causal; un supervisor la APRUEBA o RECHAZA. Al aprobar,
-- el radicado pasa a estado `anulado` (el número se conserva, nunca se borra). Queda
-- traza de quién solicitó/decidió y cuándo.

CREATE TABLE IF NOT EXISTS radicado_anulacion (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    radicado_id    UUID NOT NULL REFERENCES radicados(id) ON DELETE CASCADE,
    estado         VARCHAR(20) NOT NULL DEFAULT 'solicitada'
                   CHECK (estado IN ('solicitada','aprobada','rechazada')),
    causal         VARCHAR(15) NOT NULL,
    motivo         TEXT,
    solicitado_por UUID,
    solicitado_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decidido_por   UUID,
    decidido_at    TIMESTAMPTZ,
    observacion    TEXT
);
CREATE INDEX IF NOT EXISTS ix_anulacion_radicado ON radicado_anulacion (radicado_id);
-- A lo sumo UNA solicitud activa (sin decidir) por radicado.
CREATE UNIQUE INDEX IF NOT EXISTS ux_anulacion_activa
    ON radicado_anulacion (radicado_id) WHERE estado = 'solicitada';
