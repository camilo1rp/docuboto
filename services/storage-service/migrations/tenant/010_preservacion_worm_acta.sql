-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Inc.2 wiring — cableado WORM/Object-Lock del ACTA
-- de transferencia firmada, fast-follow declarado en el diseño de Inc.1: "Acta
-- = generalizar tipo CHECK a incluir acta_transferencia... comparte
-- fixity/retención/gate"): generaliza `preservacion_worm_objeto` (migración
-- 006) para admitir `tipo='acta_transferencia'`, SIN tocar el comportamiento
-- ya probado de `tipo='indice'`.
--
-- Unicidad por-expediente para `acta_transferencia` es SEGURA de mirar
-- (mismo criterio que la migración 009 para el índice): el ciclo de vida del
-- expediente (`open -> closed -> transferred`, TERMINAL) y el índice único
-- `ux_transferencias_activa_por_expediente` (migración 024, archive-service)
-- garantizan que un expediente alcanza `estado='recibida'` (y por tanto un
-- acta congelada) A LO SUMO UNA VEZ en toda su vida — `create()` exige
-- `closed`, que un expediente `transferred` nunca vuelve a ser. El acta de
-- transferencia es, en la práctica, tan SINGLETON por expediente como el
-- índice electrónico.
--
-- El nombre del constraint CHECK es el que Postgres asigna por defecto al
-- declararlo inline en la migración 006 (`<tabla>_<columna>_check`) —
-- verificado contra un Postgres real antes de integrar esta migración.
--
-- Aditiva/idempotente: `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` (el
-- CHECK en sí no admite `IF NOT EXISTS`, de ahí el drop-then-add, seguro de
-- re-ejecutar) + `CREATE INDEX IF NOT EXISTS`. Segura de re-ejecutar sobre un
-- schema ya migrado.

ALTER TABLE preservacion_worm_objeto
    DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;

ALTER TABLE preservacion_worm_objeto
    ADD CONSTRAINT preservacion_worm_objeto_tipo_check
    CHECK (tipo IN ('indice', 'aip', 'acta_transferencia'));

-- Fuente de idempotencia de `POST /proteger-artefacto` (tipo='acta_transferencia'),
-- espejo EXACTO de `ux_preservacion_worm_objeto_expediente_indice` (migración 009).
CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_expediente_acta
    ON preservacion_worm_objeto (expediente_id)
    WHERE tipo = 'acta_transferencia';

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ux_preservacion_worm_objeto_expediente_acta;
-- ALTER TABLE preservacion_worm_objeto DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check;
-- ALTER TABLE preservacion_worm_objeto ADD CONSTRAINT preservacion_worm_objeto_tipo_check
--     CHECK (tipo IN ('indice', 'aip'));
