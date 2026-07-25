-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21 Inc.3 — RAG generativo con citas, Patrón B): tabla de
-- auditoría CC-02/CC-03 de cada generación. `proveedor` y `modelo` van en columnas
-- SEPARADAS (no dentro de `sugerencia` JSONB) porque CC-03 es una pregunta de
-- soberanía ("¿a dónde salió el contenido?") que debe poder consultarse/agregarse
-- sin parsear JSON — es la misma razón por la que `document-service`/`archive-
-- service` no meten campos de auditoría estructural dentro de un JSONB genérico.
--
-- `aceptada_por`/`aceptada_en` NULLables desde el inicio: la aceptación humana de
-- una sugerencia es un acto POSTERIOR y opcional (CC-02 — la capa sugiere, no
-- decide); ningún endpoint de este incremento las escribe todavía
-- (`KbSuggestionRepository.mark_accepted` queda listo para un incremento futuro).
--
-- `canal` reusa el mismo dominio cerrado que `public.audit_log.canal`
-- (auth-service/migrations/global/003_audit_log_hardening.sql): api | ui | mcp |
-- system — consistencia entre las dos auditorías del mismo acto (esta tabla +
-- el asiento en `audit_log` que agrega `app/routers/knowledge.py::rag`).
--
-- Idempotente: `CREATE TABLE IF NOT EXISTS` + guard `IF NOT EXISTS` sobre el
-- CHECK (mismo patrón que 002_nivel_seguridad_failclosed.sql) + `CREATE INDEX IF
-- NOT EXISTS`.

CREATE TABLE IF NOT EXISTS kb_suggestions (
    id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tipo         TEXT        NOT NULL,
    objeto_ref   TEXT,
    sugerencia   JSONB       NOT NULL,
    proveedor    TEXT        NOT NULL,
    modelo       TEXT        NOT NULL,
    confianza    REAL        NOT NULL DEFAULT 0,
    canal        TEXT        NOT NULL DEFAULT 'system',
    aceptada_por UUID,
    aceptada_en  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- El guard DEBE acotarse al schema activo (`n.nspname = current_schema()`): en el
-- modelo multi-tenant-por-schema, `pg_constraint.conname` se REPITE entre tenants
-- (cada `tenant_<slug>.kb_suggestions` tiene su propia `chk_kb_suggestions_canal`).
-- Un guard por `conname` global vería la constraint de OTRO tenant y omitiría la de
-- éste ⇒ los tenants #2+ quedaban sin el CHECK. Mismo patrón correcto que
-- 003_knowledge_upsert.sql (que documenta exactamente esta clase de bug).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'chk_kb_suggestions_canal'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE kb_suggestions
            ADD CONSTRAINT chk_kb_suggestions_canal
            CHECK (canal IN ('api', 'ui', 'mcp', 'system'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_kb_suggestions_created_at
    ON kb_suggestions (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_kb_suggestions_objeto_ref
    ON kb_suggestions (objeto_ref)
    WHERE objeto_ref IS NOT NULL;
