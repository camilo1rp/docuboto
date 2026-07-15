-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12 H-L — integridad de la creación de transferencias,
-- Ac. AGN 001/2024 Tít. 4.4): a lo sumo UNA transferencia ACTIVA
-- (`preparada`/`enviada`) por expediente. Antes `TransferenciaService.create()`
-- solo exigía el expediente `closed` -- nada impedía preparar N transferencias
-- solapadas sobre el MISMO expediente.
--
-- El `WHERE` cubre deliberadamente solo `preparada`/`enviada`:
--   - `rechazada` NO cuenta como activa -- tras un rechazo debe poder
--     RE-PREPARARSE una nueva transferencia del mismo expediente (spec E12
--     §8 R5); una fila `rechazada` no debe bloquear esa re-preparación.
--   - `recibida` tampoco está en el predicado, pero es inalcanzable como
--     SEGUNDA fila de todas formas: `recibir()` congela el expediente en
--     `transferred` y `create()` exige `closed` -- el gate de estado ya lo
--     impide, el índice solo es coherente con esa realidad.
--
-- Concurrencia: dos `create()` simultáneos sobre el mismo expediente `closed`
-- pasan ambos el gate de estado (leído ANTES del INSERT) -- el índice es
-- quien serializa de verdad; el segundo INSERT viola la unicidad y
-- `TransferenciaService.create()` traduce la `UniqueViolationError` a un 409
-- `transferencia_activa_existente` (fail-closed, mismo patrón que
-- `FisicoService.create_ubicacion`).
--
-- Idempotente: CREATE UNIQUE INDEX IF NOT EXISTS.
--
-- Caveat de despliegue: este índice FALLA al crearse si el schema ya contiene
-- datos que violan el invariante (dos transferencias activas del mismo
-- expediente). En un schema de tenant fresco (o los `tenant_itest_*`
-- desechables de integración) no hay ese riesgo; un tenant existente con el
-- bug latente necesitaría un saneo previo de datos antes de aplicar esta
-- migración.

CREATE UNIQUE INDEX IF NOT EXISTS ux_transferencias_activa_por_expediente
    ON transferencias (expediente_id)
    WHERE estado IN ('preparada', 'enviada');
