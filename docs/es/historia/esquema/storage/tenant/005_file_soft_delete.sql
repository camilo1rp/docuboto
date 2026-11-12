-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant: soft-delete de anexos (D-04, constitución inv. 12).
--
-- "Borrar" un anexo solo marca la fila como eliminada lógicamente; la fila
-- y el objeto en MinIO se conservan. El borrado físico (purga) solo ocurre
-- como parte de la disposición final del TRD.
--
-- deleted_at IS NULL → activo
-- deleted_at IS NOT NULL → eliminado lógicamente

ALTER TABLE files ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE files ADD COLUMN IF NOT EXISTS deleted_by UUID;

-- Partial index: la ruta habitual (archivos activos) no paga el coste de las
-- filas eliminadas al hacer SELECT ... WHERE deleted_at IS NULL.
CREATE INDEX IF NOT EXISTS ix_files_active ON files (id) WHERE deleted_at IS NULL;
