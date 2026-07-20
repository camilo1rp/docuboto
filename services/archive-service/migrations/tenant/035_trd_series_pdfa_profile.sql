-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt "perfil PDF/A por serie" — follow-up de
-- Increment C2b, diseño ratificado por orfeo-architect: ver
-- `.claude/agent-memory/orfeo-architect/project_pdfa-profile-por-serie-e10-c2b.md`).
--
-- Sustituye el global `PDFA_PROFILE` (settings.pdfa_profile, hoy fijo en
-- "2b") por una política POR SERIE: distintas series documentales requieren
-- distintos flavours (2b ofimática genérica, 2u texto buscable, 3a/3b con
-- adjuntos embebidos, PDF/A-1 legado). El perfil es política archivística de
-- la serie -- vive en `trd_series`, NO en storage-service (que no modela TRD,
-- frontera de confianza cruzada por `AipCreate.pdfa_profile`).
--
-- `NULL` (default) = "sin perfil configurado para esta serie" -- el
-- expediente cae al default global de storage-service (`settings.
-- pdfa_profile`, retrocompat total: ninguna serie configurada hoy, ningún
-- cambio de comportamiento).
--
-- El CHECK es la MISMA whitelist que `_VALID_FLAVOURS`
-- (`storage-service/app/services/pdfa/subprocess_verapdf.py`) -- duplicada a
-- propósito (archive-service no importa de storage-service, servicios
-- independientes) y también reforzada en la capa Pydantic
-- (`TrdSerieCreate`/`TrdSerieUpdate`) para dar un 422 legible antes de tocar
-- la BD; el CHECK es la última línea de defensa fail-closed.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'trd_series' AND column_name = 'pdfa_profile'
    ) THEN
        ALTER TABLE trd_series ADD COLUMN pdfa_profile TEXT;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'trd_series'
          AND con.contype = 'c'
          AND con.conname = 'trd_series_pdfa_profile_check'
    ) THEN
        ALTER TABLE trd_series
            ADD CONSTRAINT trd_series_pdfa_profile_check
            CHECK (pdfa_profile IS NULL OR pdfa_profile IN
                   ('1a', '1b', '2a', '2b', '2u', '3a', '3b', '3u'));
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Rollback (referencia manual, no ejecutado por el runner):
-- ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_pdfa_profile_check;
-- ALTER TABLE trd_series DROP COLUMN IF EXISTS pdfa_profile;
