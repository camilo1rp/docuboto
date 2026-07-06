-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Increment C2a, RF-PRE-02, RF-FIR-15): habilita un
-- TERCER estado honesto en `preservacion_evento.resultado` — 'no_evaluado' —
-- para el evento VALIDACION_PDFA cuando el validador PDF/A real no está
-- disponible en el despliegue (`StubPdfaValidator` por defecto). Antes el
-- CHECK solo admitía ('ok','fallo'): el resultado más frecuente en la
-- práctica (validación no realizada) NO cabía, obligando a mentir con 'ok'
-- o 'fallo' — ninguno de los dos es honesto cuando simplemente no se evaluó.
--
-- `resultado` se ensancha de VARCHAR(8) a VARCHAR(16): 'no_evaluado' tiene
-- 11 caracteres y no entraba en VARCHAR(8). Ensanchar una columna VARCHAR es
-- retrocompatible con las filas existentes (todas más cortas que el nuevo
-- límite). Sin columnas nuevas: el detalle JSONB + `agente` ya existentes
-- alcanzan para registrar perfil/versión/outcome del validador (ver
-- `app/schemas/preservacion.py::EventoCreate`, `app/services/pdfa`).
--
-- Idempotente: `ALTER COLUMN ... TYPE` no falla al reaplicarse (mismo tipo),
-- y el DROP/ADD CONSTRAINT usa el nombre por defecto de Postgres para un
-- CHECK inline de columna (`{tabla}_{columna}_check`) con `IF EXISTS`.

ALTER TABLE preservacion_evento ALTER COLUMN resultado TYPE VARCHAR(16);

ALTER TABLE preservacion_evento DROP CONSTRAINT IF EXISTS preservacion_evento_resultado_check;
ALTER TABLE preservacion_evento
    ADD CONSTRAINT preservacion_evento_resultado_check
    CHECK (resultado IN ('ok', 'fallo', 'no_evaluado'));
