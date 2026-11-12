-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, F3, ADR-015): modelo TRD/CCD.
--
-- Jerarquía serie↔subserie (parent_id auto-referencia), retención en DOS fases
-- (archivo de gestión + archivo central) y versionado de la TRD. La disposición
-- conserva la columna `disposition` (ahora con códigos AGN CT/E/S/M; los valores
-- antiguos se mapean en la capa de servicio).

ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES trd_series(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS archivo_gestion_years INTEGER,
    ADD COLUMN IF NOT EXISTS archivo_central_years INTEGER,
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS valid_from DATE NOT NULL DEFAULT CURRENT_DATE;

-- Backfill de la retención en dos fases desde los campos previos.
UPDATE trd_series
   SET archivo_gestion_years = COALESCE(archivo_gestion_years, retention_years),
       archivo_central_years = COALESCE(
           archivo_central_years, GREATEST(total_retention - retention_years, 0)
       )
 WHERE archivo_gestion_years IS NULL OR archivo_central_years IS NULL;

CREATE INDEX IF NOT EXISTS ix_trd_series_parent ON trd_series (parent_id);
