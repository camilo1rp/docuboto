-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21, ADR-006): base de conocimiento derivada por tenant.
--
-- Capa de conocimiento ADVISORIA y DERIVADA (opt-in por tenant): fragmentos de
-- texto con embedding (pgvector) para recuperación semántica con PRE-FILTRADO ACL.
-- El embedding lo produce un proveedor pluggable (stub local por defecto, dim 64).
-- No es fuente de verdad; se reconstruye desde los eventos/API de dominio.

CREATE TABLE IF NOT EXISTS knowledge_chunk (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(40) NOT NULL,        -- radicado | expediente | ...
    source_ref  TEXT NOT NULL,               -- id/tracking del objeto de dominio
    texto       TEXT NOT NULL,
    embedding   public.vector(64) NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    acl         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- pre-filtrado de acceso
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_knowledge_source
    ON knowledge_chunk (source_type, source_ref);
-- Índice ANN (ivfflat, distancia coseno); requiere ANALYZE con datos para rendir.
CREATE INDEX IF NOT EXISTS ix_knowledge_embedding
    ON knowledge_chunk USING ivfflat (embedding public.vector_cosine_ops) WITH (lists = 100);
