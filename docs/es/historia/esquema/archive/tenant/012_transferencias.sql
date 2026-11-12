-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12, F5, Acuerdo AGN 001/2024 Art. 4.4.x): transferencias.
--
-- Transferencias del ciclo vital: primaria (gestión→central) y secundaria
-- (central→histórico). Ciclo preparada→enviada→recibida|rechazada. Al recibir, el
-- expediente pasa a `transferred` y queda congelado (no admite documentos). El
-- FUID (E17) y el índice firmado (E15) son los entregables.

CREATE TABLE IF NOT EXISTS transferencias (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo                 VARCHAR(12) NOT NULL CHECK (tipo IN ('primaria','secundaria')),
    estado               VARCHAR(12) NOT NULL DEFAULT 'preparada'
                         CHECK (estado IN ('preparada','enviada','recibida','rechazada')),
    expediente_id        UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    ubicacion_origen_id  UUID REFERENCES ubicacion(id) ON DELETE SET NULL,
    ubicacion_destino_id UUID REFERENCES ubicacion(id) ON DELETE SET NULL,
    creada_por           TEXT,
    observacion          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    enviada_at           TIMESTAMPTZ,
    recibida_at          TIMESTAMPTZ,
    decidida_por         TEXT
);
CREATE INDEX IF NOT EXISTS ix_transferencias_expediente ON transferencias (expediente_id);
CREATE INDEX IF NOT EXISTS ix_transferencias_estado ON transferencias (estado);
