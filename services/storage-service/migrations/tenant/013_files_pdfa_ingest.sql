-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Increment C2b follow-up, RF-PRE-02, ISO 19005,
-- Ley 594/2000 + Acuerdo AGN 060/2001): validación PDF/A en el flujo de
-- ingesta/captura de anexos (E07), no solo al empaquetar el AIP (tarde,
-- Increment C2a/C2b).
--
-- Tres columnas nuevas en `files`, el verdicto DURABLE por objeto:
--   `pdfa_status`     -- 'conformant' | 'non_conformant' | 'not_evaluated'
--                         (terminal, mismo vocabulario que `PdfaOutcome`) |
--                         'pendiente' (encolado, aún sin correr el worker
--                         asíncrono best-effort) | NULL (nunca se evaluó:
--                         gate apagado, no es PDF, o el archivo predata este
--                         incremento).
--   `pdfa_profile`    -- perfil PDF/A contra el que se evaluó (p. ej. "2b"),
--                         para que el empaquetado del AIP detecte divergencia
--                         de perfil y decida si el verdicto es reusable.
--   `pdfa_checked_at` -- instante en que quedó FIJADO un estado terminal.
--
-- `AipService.empaquetar_aip` (storage-service, `app/services/aip_service.py`)
-- REUSA este verdicto si es terminal para el MISMO perfil que el AIP exige —
-- evita re-ejecutar el validador (JVM/subprocess) para contenido ya evaluado
-- en la ingesta.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + CHECK con
-- DROP/ADD `IF EXISTS` (mismo patrón que la migración 008).

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS pdfa_status     TEXT,
    ADD COLUMN IF NOT EXISTS pdfa_profile    TEXT,
    ADD COLUMN IF NOT EXISTS pdfa_checked_at TIMESTAMPTZ;

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_pdfa_status_check;
ALTER TABLE files
    ADD CONSTRAINT files_pdfa_status_check
    CHECK (pdfa_status IS NULL OR pdfa_status IN (
        'conformant', 'non_conformant', 'not_evaluated', 'pendiente'
    ));

COMMENT ON COLUMN files.pdfa_status IS
    'Veredicto PDF/A durable del objeto (E10 C2b follow-up, ingesta E07). NULL = nunca evaluado (gate apagado / no-PDF / archivo previo al incremento). "pendiente" = encolado para el worker asíncrono best-effort (modo advisory). Terminal: conformant|non_conformant|not_evaluated -- reusado por AipService.empaquetar_aip si el perfil coincide.';
COMMENT ON COLUMN files.pdfa_profile IS
    'Perfil PDF/A (p. ej. "2b") contra el que se evaluó pdfa_status -- permite al empaquetado del AIP detectar divergencia de perfil y re-validar solo si difiere.';
COMMENT ON COLUMN files.pdfa_checked_at IS
    'Instante en que pdfa_status quedó en un estado TERMINAL. NULL mientras esté en NULL o "pendiente".';

-- Rollback (manual, no automatizado por el runner):
-- ALTER TABLE files DROP CONSTRAINT IF EXISTS files_pdfa_status_check;
-- ALTER TABLE files DROP COLUMN IF EXISTS pdfa_status;
-- ALTER TABLE files DROP COLUMN IF EXISTS pdfa_profile;
-- ALTER TABLE files DROP COLUMN IF EXISTS pdfa_checked_at;
