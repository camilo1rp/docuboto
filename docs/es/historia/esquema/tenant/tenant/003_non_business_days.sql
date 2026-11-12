-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 003: días no hábiles (festivos) — E14.
-- Base del cálculo de fechas de vencimiento en días hábiles (RF-RAD-04, E01).
-- search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS non_business_days (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fecha        DATE NOT NULL UNIQUE,
    descripcion  VARCHAR(200) NOT NULL,
    tipo         VARCHAR(20) NOT NULL DEFAULT 'festivo'
                 CHECK (tipo IN ('festivo','puente','especial')),
    is_national  BOOLEAN NOT NULL DEFAULT true,     -- false = local del tenant
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_nbd_fecha ON non_business_days (fecha);
