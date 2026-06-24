-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E07, RF-DIG-01): versionado inmutable de anexos.
--
-- Cada fila de `files` es una VERSIÓN inmutable. "Reemplazar" crea una nueva
-- versión con su propio SHA-256; las anteriores se conservan (cadena auditable).
--   root_id     = identidad lógica del anexo (la id de la versión 1; estable).
--   version     = número de versión incremental (>=1).
--   is_current  = marca la última versión vigente (una por root_id).
--   replaces_id = versión inmediatamente anterior (NULL en la v1).

ALTER TABLE files ADD COLUMN IF NOT EXISTS version     INTEGER NOT NULL DEFAULT 1;
ALTER TABLE files ADD COLUMN IF NOT EXISTS is_current  BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE files ADD COLUMN IF NOT EXISTS root_id     UUID;
ALTER TABLE files ADD COLUMN IF NOT EXISTS replaces_id UUID;

-- Las filas existentes se vuelven raíz de su propia cadena (v1, vigente).
UPDATE files SET root_id = id WHERE root_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_files_root_version ON files (root_id, version);
CREATE INDEX IF NOT EXISTS ix_files_current ON files (root_id) WHERE is_current;
