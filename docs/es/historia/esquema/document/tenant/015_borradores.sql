-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (paridad legado): plantillas + borradores de Salida.
--
-- Producción de documentos antes de radicar: un borrador (Salida/Interno) se
-- crea/edita (opcionalmente desde una plantilla), se aprueba y finalmente se
-- RADICA (genera el radicado real). Hasta radicar no tiene número oficial.

CREATE TABLE IF NOT EXISTS plantilla (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre          VARCHAR(150) NOT NULL,
    tipo_documental VARCHAR(100),
    cuerpo          TEXT NOT NULL,
    activo          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS borrador (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_type     VARCHAR(1) NOT NULL CHECK (doc_type IN ('S','I')),
    subject      TEXT NOT NULL,
    dest_dept    VARCHAR(100),
    cuerpo       TEXT,
    plantilla_id UUID REFERENCES plantilla(id) ON DELETE SET NULL,
    estado       VARCHAR(12) NOT NULL DEFAULT 'borrador'
                 CHECK (estado IN ('borrador','aprobado','radicado')),
    radicado_id  UUID,
    creado_por   UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_borrador_estado ON borrador (estado);
