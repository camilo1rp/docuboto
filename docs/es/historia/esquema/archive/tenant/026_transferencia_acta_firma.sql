-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (H-H, E06/F4 — firma XAdES del acta de transferencia):
-- tabla HERMANA de `transferencia_acta` (migración 022) que rastrea el ESTADO
-- MUTABLE del sellado del acta -- a diferencia de `transferencia_acta`
-- (append-only por trigger, migración 023), esta tabla SÍ admite UPDATE: el
-- sellado ocurre DESPUÉS de que el acta ya quedó congelada/anclada en la tx
-- de `TransferenciaService.recibir`, así que necesita su propia fila mutable
-- de bookkeeping (mismo criterio que separó `expediente_indice` -- versionado
-- append-only -- de sus columnas `reconcile_*` mutables, migración 025).
--
-- 1:1 con `transferencia_acta` (PK = FK, no una tabla de versiones -- el acta
-- es única por transferencia, a diferencia del índice electrónico que SÍ
-- versiona). `estado` acotado a 'pendiente_firma'/'firmado' (sin 'agotado'
-- propio: `reconcile_agotado` ya lo expresa como columna independiente, igual
-- que `expediente_indice`).
--
-- Columnas de backoff (`reconcile_intentos`/`reconcile_proximo_at`/
-- `reconcile_agotado`) con el MISMO nombre que `expediente_indice` (migración
-- 025) para que `app/jobs/indice_reconciliation.py::_next_backoff_seconds`
-- (cálculo puro, sin acoplarse al nombre de columna) y el patrón de
-- reconciliación se compartan sin reinventar la fórmula del backoff
-- exponencial. `last_error` (en vez de `reconcile_ultimo_error`): mismo dato
-- (motivo del último fallo), nombre propio de esta tabla.
--
-- Aditiva/idempotente: `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`.
-- Segura de re-ejecutar sobre un schema ya migrado.

CREATE TABLE IF NOT EXISTS transferencia_acta_firma (
    transferencia_id    UUID PRIMARY KEY
                         REFERENCES transferencia_acta(transferencia_id) ON DELETE CASCADE,
    estado               TEXT NOT NULL DEFAULT 'pendiente_firma'
                         CHECK (estado IN ('pendiente_firma', 'firmado')),
    firma_id             UUID,
    minio_key_firmado    TEXT,
    xades_level          TEXT,
    acreditado           BOOLEAN NOT NULL DEFAULT false,
    last_error           TEXT,
    reconcile_intentos   INT NOT NULL DEFAULT 0,
    reconcile_proximo_at TIMESTAMPTZ,
    reconcile_agotado    BOOLEAN NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    firmado_at           TIMESTAMPTZ
);

COMMENT ON TABLE transferencia_acta_firma IS
    'Estado MUTABLE del sellado XAdES del acta de entrega (H-H, E06/F4) -- 1:1 con transferencia_acta (append-only), NUNCA la misma tabla.';
COMMENT ON COLUMN transferencia_acta_firma.estado IS
    'pendiente_firma (default, insertada atómicamente al congelar el acta en recibir()) | firmado (inmutable en la práctica, mark_acta_firmada exige estado <> firmado).';
COMMENT ON COLUMN transferencia_acta_firma.firma_id IS
    'firma.id de signature-service una vez sellado -- NULL mientras pendiente_firma.';
COMMENT ON COLUMN transferencia_acta_firma.last_error IS
    'Motivo del último intento fallido de sellado (sello_ausente | sellado_xades_no_obtenido) -- visible al reintento manual y a la reconciliación.';
COMMENT ON COLUMN transferencia_acta_firma.reconcile_intentos IS
    'Reintentos consumidos por el job de reconciliación de actas (gemelo de expediente_indice.reconcile_intentos, migración 025).';
COMMENT ON COLUMN transferencia_acta_firma.reconcile_proximo_at IS
    'Próximo intento elegible del job (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN transferencia_acta_firma.reconcile_agotado IS
    'TRUE cuando se agotó indice_reconciliation_max_intentos: permanece pendiente_firma, requiere reintento manual (POST /transferencias/{id}/acta/firmar).';

-- Fuente de `TransferenciaRepository.list_acta_firmas_pendientes`: solo actas
-- 'pendiente_firma' no agotadas, ordenadas por elegibilidad de backoff --
-- mismo patrón que `ix_expediente_indice_reconcile_pendiente` (migración 025).
CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_pendiente
    ON transferencia_acta_firma (reconcile_proximo_at)
    WHERE estado = 'pendiente_firma' AND reconcile_agotado = false;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_pendiente;
-- DROP TABLE IF EXISTS transferencia_acta_firma;
