-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Migración de tenant (AREA-11-11 / CC-02 — aceptación humana de sugerencias):
-- quién GENERÓ la sugerencia. Sin este dato, el endpoint de aceptación no puede
-- saber si quien acepta es quien la pidió: cualquiera con el UUID aceptaría la
-- sugerencia de otro y `aceptada_por` dejaría de decir quién decidió algo que se
-- le puso delante. NULL en las filas anteriores a esta migración (no hay forma
-- honesta de reconstruirlo): esas NO son aceptables — fail-closed, porque
-- aceptar exige `creada_por = quien acepta`.
--
-- Idempotente: `ADD COLUMN IF NOT EXISTS`.

ALTER TABLE kb_suggestions
    ADD COLUMN IF NOT EXISTS creada_por UUID;

COMMENT ON COLUMN kb_suggestions.creada_por IS
    'auth_users.id de quien pidió la generación (X-User-Id). Solo esa persona '
    'puede aceptarla (CC-02). NULL = anterior a la migración 012: no aceptable.';

-- ROLLBACK (manual):
-- ALTER TABLE kb_suggestions DROP COLUMN IF EXISTS creada_por;
