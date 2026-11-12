-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (paridad legado): vistos buenos secuenciales.
--
-- Cadena ordenada de revisores que deben dar su VoBo antes de que un documento
-- pueda salir/firmarse. Cada revisor actúa solo en su turno (orden ascendente);
-- un rechazo detiene la cadena. Gobernanza de aprobación de comunicaciones.

CREATE TABLE IF NOT EXISTS visto_bueno (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    radicado_id UUID NOT NULL,
    orden       INTEGER NOT NULL,
    revisor_id  UUID NOT NULL,
    estado      VARCHAR(12) NOT NULL DEFAULT 'pendiente'
                CHECK (estado IN ('pendiente','aprobado','rechazado')),
    comentario  TEXT,
    decidido_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (radicado_id, orden)
);
CREATE INDEX IF NOT EXISTS ix_vobo_radicado ON visto_bueno (radicado_id, orden);
