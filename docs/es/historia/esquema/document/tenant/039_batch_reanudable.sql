-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 039 (AREA-09-15): lotes de radicados reanudables y reprocesables.
--
-- Los lotes se procesaban con asyncio.create_task: si el servicio se reiniciaba, el
-- lote quedaba en pending/processing para siempre, y un ítem fallido no se podía
-- reintentar. Ahora el barrido reanuda los interrumpidos y el lote termina en
-- 'completado_con_errores' con el detalle por ítem.
--
-- `batch_jobs`/`batch_job_items` son COMPARTIDAS por nombre con archive-service
-- (job_type 'documents' | 'expedientes'): solo se AÑADEN columnas, sin CHECK sobre
-- status, para no tocar los estados del otro servicio. Idempotente.

ALTER TABLE batch_job_items ADD COLUMN IF NOT EXISTS intentos   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE batch_job_items ADD COLUMN IF NOT EXISTS error_tipo VARCHAR(100);

ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS reanudaciones   INTEGER NOT NULL DEFAULT 0;
-- Quien pidió el último reproceso: los radicados que cree ese reproceso son suyos
-- (registered_by y asiento), no de quien encoló el lote original.
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS reprocesado_por UUID;
ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS reprocesado_at  TIMESTAMPTZ;

COMMENT ON COLUMN batch_job_items.intentos IS
    'Veces que se intentó procesar el ítem (el primer intento cuenta 1).';
COMMENT ON COLUMN batch_job_items.error_tipo IS
    'Clase de la excepción del último fallo (el motivo legible va en error_message).';

-- El estado nuevo `completado_con_errores` (22 caracteres) no cabe en VARCHAR(20).
-- Ensanchar es seguro y compatible con archive-service (que sigue usando los suyos).
ALTER TABLE batch_jobs ALTER COLUMN status TYPE VARCHAR(40);
