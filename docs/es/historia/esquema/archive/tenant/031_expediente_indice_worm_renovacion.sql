-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt — renovación de retención WORM para series
-- de disposición Conservación Total, CT — ver
-- app/services/index_service.py::renovar_worm_indice y
-- app/jobs/indice_reconciliation.py::run_once_worm_renovacion): espejo
-- LOCAL de `preservacion_worm_objeto.retain_until` (storage-service,
-- migración 011) + estado/backoff de la 5ª fase de reconciliación SOBRE
-- `expediente_indice`.
--
-- Por qué un espejo (Opción A, mismo patrón que `worm_protegido_at`/
-- `worm_object_id` de la migración 029): archive-service es el DUEÑO de la
-- TRD/disposición y necesita barrer "¿qué índices CT están por vencer?" sin
-- una llamada HTTP síncrona a storage-service por cada barrida — el espejo
-- se puebla al proteger (`IndexService.proteger_worm_indice` ->
-- `mark_worm_protegido`, con el `retain_until` REAL devuelto por storage) y
-- se actualiza al renovar (`renovar_worm_indice` -> `mark_worm_renovado`).
-- storage-service sigue siendo la fuente de verdad LEGAL (el Object-Lock
-- vive ahí); este espejo es solo el índice de barrida.
--
-- ORTOGONAL a `worm_protegido_at`/`worm_reconcile_*` (migración 029, eje de
-- LA PRIMERA protección) y a `estado`/`reconcile_*` (sellado XAdES-B) — un
-- eje de backoff INDEPENDIENTE, tercero sobre esta tabla.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT
-- EXISTS`. Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_retain_until TIMESTAMPTZ;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_renovacion_intentos INTEGER NOT NULL DEFAULT 0;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_renovacion_proximo_at TIMESTAMPTZ;

-- Aditivo, deliberadamente NO pedido en el contrato original de esta tarea:
-- se agrega por CONSISTENCIA con el resto de ejes de backoff de este mismo
-- servicio (worm_reconcile_ultimo_error/reconcile_ultimo_error, migraciones
-- 025/029) — todos los demás guardan el motivo textual del último fallo para
-- diagnóstico operacional; un eje de renovación silencioso (solo
-- intentos/agotado, sin motivo) sería el único sin esa visibilidad, y este
-- eje en particular protege un artefacto de conservación PERMANENTE (mayor
-- criticidad, no menor).
ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_renovacion_ultimo_error TEXT;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_renovacion_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN expediente_indice.worm_retain_until IS
    'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service) -- poblado al proteger (mark_worm_protegido) y actualizado al renovar (mark_worm_renovado). NULL mientras worm_protegido_at sea NULL.';
COMMENT ON COLUMN expediente_indice.worm_renovacion_intentos IS
    'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender retain_until antes de su vencimiento, para series CT.';
COMMENT ON COLUMN expediente_indice.worm_renovacion_proximo_at IS
    'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN expediente_indice.worm_renovacion_ultimo_error IS
    'Motivo del último fallo al renovar la retención WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';
COMMENT ON COLUMN expediente_indice.worm_renovacion_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos de renovación: el índice de una serie CT queda sin extender, requiere intervención manual URGENTE (riesgo de vencimiento del lock COMPLIANCE sobre un registro de conservación permanente).';

-- Fuente de IndexRepository.list_worm_renovacion_pending: solo índices YA
-- protegidos, aún no agotados de renovación. El filtro por disposición CT
-- (join a expedientes/trd_series) y por vencimiento próximo
-- (worm_retain_until < now() + ventana) vive en la CONSULTA, no en este
-- índice -- la mayoría de las filas protegidas NO son CT, así que el índice
-- parcial solo acota el universo candidato (barato: devuelve vacío casi
-- siempre en un tenant sin series CT vencidas).
CREATE INDEX IF NOT EXISTS ix_expediente_indice_worm_renovacion_pendiente
    ON expediente_indice (worm_renovacion_proximo_at)
    WHERE worm_protegido_at IS NOT NULL AND worm_renovacion_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_expediente_indice_worm_renovacion_pendiente;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_renovacion_agotado;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_renovacion_ultimo_error;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_renovacion_proximo_at;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_renovacion_intentos;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_retain_until;
