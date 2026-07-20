-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt -- extiende la renovación de retención WORM
-- para series de disposición Conservación Total (CT) al AIP OAIS, ver
-- app/services/aip_wiring_service.py::renovar_worm_aip y
-- app/jobs/indice_reconciliation.py::run_once_worm_renovacion, 5ª fase):
-- espejo LOCAL de `preservacion_worm_objeto.retain_until` (storage-service)
-- + estado/backoff de renovación SOBRE `expediente_aip` (migración 033).
--
-- Mismo patrón EXACTO que la migración 031 (`expediente_indice`) / 032
-- (`transferencia_acta_firma`) -- cuando esas dos se implementaron, el AIP
-- quedó deliberadamente fuera de alcance porque `expediente_aip` (el espejo
-- del AIP) todavía no existía (creada recién en la migración 033, cableado
-- del AIP al cierre). Ahora que el espejo existe, se generaliza la 5ª fase de
-- reconciliación para cubrir también el AIP.
--
-- ORTOGONAL a `estado`/`intentos`/`proximo_intento_at`/`ultimo_error`
-- (migración 033, eje de backoff del EMPAQUETADO) -- un eje de backoff
-- INDEPENDIENTE para la RENOVACIÓN. El AIP permanece `estado='empaquetado'`
-- (TERMINAL) mientras este eje de renovación cicla en sus propias columnas,
-- exactamente igual que `expediente_indice.estado='firmado'` permanece fijo
-- mientras `worm_renovacion_*` cicla aparte (migración 031).
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT
-- EXISTS`. Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE expediente_aip
    ADD COLUMN IF NOT EXISTS worm_retain_until TIMESTAMPTZ;

ALTER TABLE expediente_aip
    ADD COLUMN IF NOT EXISTS worm_renovacion_intentos INTEGER NOT NULL DEFAULT 0;

ALTER TABLE expediente_aip
    ADD COLUMN IF NOT EXISTS worm_renovacion_proximo_at TIMESTAMPTZ;

ALTER TABLE expediente_aip
    ADD COLUMN IF NOT EXISTS worm_renovacion_ultimo_error TEXT;

ALTER TABLE expediente_aip
    ADD COLUMN IF NOT EXISTS worm_renovacion_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN expediente_aip.worm_retain_until IS
    'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service, tipo=aip) -- poblado al empaquetar (mark_empaquetado) y actualizado al renovar (mark_worm_renovado). NULL mientras estado <> ''empaquetado''.';
COMMENT ON COLUMN expediente_aip.worm_renovacion_intentos IS
    'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender worm_retain_until del AIP antes de su vencimiento, para series CT.';
COMMENT ON COLUMN expediente_aip.worm_renovacion_proximo_at IS
    'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN expediente_aip.worm_renovacion_ultimo_error IS
    'Motivo del último fallo al renovar la retención WORM del AIP (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';
COMMENT ON COLUMN expediente_aip.worm_renovacion_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos de renovación: el AIP de una serie CT queda sin extender, requiere intervención manual URGENTE (riesgo de vencimiento del lock COMPLIANCE sobre un registro de conservación permanente).';

-- Fuente de AipRepository.list_worm_renovacion_pending: solo AIP YA
-- empaquetados, aún no agotados de renovación. El filtro por disposición CT
-- (join a expedientes/trd_series) y por vencimiento próximo
-- (worm_retain_until < now() + ventana) vive en la CONSULTA, no en este
-- índice -- la mayoría de las filas empaquetadas NO son CT, así que el
-- índice parcial solo acota el universo candidato.
CREATE INDEX IF NOT EXISTS ix_expediente_aip_worm_renovacion_pendiente
    ON expediente_aip (worm_renovacion_proximo_at)
    WHERE estado = 'empaquetado' AND worm_renovacion_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_expediente_aip_worm_renovacion_pendiente;
-- ALTER TABLE expediente_aip DROP COLUMN IF EXISTS worm_renovacion_agotado;
-- ALTER TABLE expediente_aip DROP COLUMN IF EXISTS worm_renovacion_ultimo_error;
-- ALTER TABLE expediente_aip DROP COLUMN IF EXISTS worm_renovacion_proximo_at;
-- ALTER TABLE expediente_aip DROP COLUMN IF EXISTS worm_renovacion_intentos;
-- ALTER TABLE expediente_aip DROP COLUMN IF EXISTS worm_retain_until;
