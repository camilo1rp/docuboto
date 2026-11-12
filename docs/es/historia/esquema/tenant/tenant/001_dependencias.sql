-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 001: dependencias (organigrama) — E14.
-- search_path = tenant_{slug} (ADR-012); sin columna tenant_slug (el schema aísla).

CREATE TABLE IF NOT EXISTS dependencias (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id    UUID REFERENCES dependencias(id) ON DELETE RESTRICT,
    codigo       INT  NOT NULL UNIQUE,              -- depe_codi (3-4 dígitos)
    sigla        VARCHAR(20)  NOT NULL,
    nombre       TEXT NOT NULL,
    depe_rad_tp2 BOOLEAN NOT NULL DEFAULT false,    -- habilita radicación web
    activo       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dependencias_parent ON dependencias (parent_id);
CREATE INDEX IF NOT EXISTS ix_dependencias_activo ON dependencias (activo);
