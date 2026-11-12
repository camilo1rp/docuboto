-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 058 (E15, AREA-07-01): marca de «origen completado» de un vínculo.
-- search_path = tenant_{slug} (ADR-012).
--
-- Los vínculos expediente↔radicado anteriores al perfil v4 del índice quedaron SIN dependencia
-- productora, formato ni tamaño (columnas NULL): el índice los declara con
-- `productoraPresente="false"`. Para los expedientes que SIGUEN ABIERTOS todavía se puede
-- completar lo que document-service sabe (los cerrados son probatorios y no se tocan). El job
-- (`ArchiveService.completar_origen_legado`) rellena SOLO columnas NULL y deja esta marca.
--
-- La marca existe porque un NULL puede ser legítimo (un radicado sin anexos no tiene formato
-- ni tamaño; uno sin dependencia de origen no tiene productora): sin ella el job reelegiría
-- esas filas en cada barrida, para siempre. Con la marca cada vínculo se intenta UNA vez y el
-- job es idempotente.
--
-- Aditiva/idempotente.

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS origen_completado_at TIMESTAMPTZ;

COMMENT ON COLUMN expediente_radicados.origen_completado_at IS
    'Instante en que el job de backfill intentó completar productora/formato/tamano_bytes de este vínculo con lo que sabe document-service (AREA-07-01). NULL = aún no intentado. Un vínculo ya completado (o creado con el origen resuelto en el servidor) no se vuelve a elegir.';

-- Fuente de ArchiveRepository.list_vinculos_origen_pendiente.
CREATE INDEX IF NOT EXISTS ix_exp_rad_origen_pendiente
    ON expediente_radicados (expediente_id)
    WHERE origen_completado_at IS NULL AND estado = 'vinculado';

-- Rollback manual (no automatizado):
-- DROP INDEX IF EXISTS ix_exp_rad_origen_pendiente;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS origen_completado_at;
