-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Increment B, RF-PRE-05/07, ISO 14721 OAIS):
-- convierte el AIP stub (Increment A: un string BagIt sintético, sin bytes
-- reales, guardado íntegro en la columna `bagit`) en el AIP real: un ZIP
-- BagIt+PREMIS v3 subido al bucket WORM del tenant (misma fundación Object
-- Lock del Increment A, `preservacion_worm_objeto` con tipo='aip', ya
-- admitido desde la migración 006).
--
-- Aditiva y NULLABLE: las filas `aip` insertadas en el Increment A (stub)
-- siguen siendo válidas — sus columnas nuevas quedan NULL. `bagit` deja de
-- ser NOT NULL porque el Bag real ya no se guarda como texto en esta tabla
-- (vive como ZIP en MinIO, referenciado por bucket/object_key); las filas
-- nuevas simplemente no la escriben.

ALTER TABLE aip ADD COLUMN IF NOT EXISTS bucket         TEXT;
ALTER TABLE aip ADD COLUMN IF NOT EXISTS object_key     TEXT;
ALTER TABLE aip ADD COLUMN IF NOT EXISTS aip_bytes      BIGINT;
ALTER TABLE aip ADD COLUMN IF NOT EXISTS formato        TEXT NOT NULL DEFAULT 'application/zip';
ALTER TABLE aip ADD COLUMN IF NOT EXISTS retain_until   TIMESTAMPTZ;
ALTER TABLE aip ADD COLUMN IF NOT EXISTS retention_mode TEXT;
ALTER TABLE aip ADD COLUMN IF NOT EXISTS legal_hold     BOOLEAN NOT NULL DEFAULT false;

-- `bagit` (columna del stub del Increment A) deja de escribirse desde el
-- código de aplicación a partir de este incremento; se afloja la restricción
-- para no romper los INSERT reales (loosening de una restricción es siempre
-- retrocompatible con las filas existentes).
ALTER TABLE aip ALTER COLUMN bagit DROP NOT NULL;
