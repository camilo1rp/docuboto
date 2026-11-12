-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17/F4 Incremento 1 -- firma PERSONAL PKI por-usuario
-- del acta de entrega de transferencia): registra QUIÉN firmó personalmente
-- el acta (`rol` = 'recibe' | 'entrega') -- ADITIVA sobre el sello
-- institucional (`transferencia_acta_firma`, migración 026), NUNCA lo
-- reemplaza. A diferencia de esa tabla (1:1 por transferencia, mutable con
-- backoff de reconciliación), esta es 1:N por transferencia (una fila por
-- ROL firmante) y estrictamente append-only -- no hay reconciliación
-- desatendida posible: la firma personal exige un TOTP interactivo del
-- propio usuario, así que nunca puede reintentarse en background (a
-- diferencia del sello, que sí tiene job de reconciliación).
--
-- Alcance de este incremento (Inc.1): solo `rol='recibe'` (el archivista
-- receptor, paso EXPLÍCITO posterior a `recibir()`). El CHECK ya admite
-- 'entrega' (co-firma del remitente, Inc.2 -- deuda, no implementada aquí)
-- para que el esquema no necesite otra migración cuando se complete.
--
-- Sin columnas reconcile_*/estado pendiente (a diferencia de
-- transferencia_acta_firma): no hay un estado "pendiente_firma" que
-- reconciliar en background para un acto que requiere TOTP interactivo --
-- la fila solo existe una vez que la firma YA se produjo.
--
-- Idempotente: `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`.
-- Segura de re-ejecutar sobre un schema ya migrado.

CREATE TABLE IF NOT EXISTS transferencia_acta_firma_personal (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transferencia_id   UUID NOT NULL REFERENCES transferencias(id) ON DELETE CASCADE,
    rol                TEXT NOT NULL CHECK (rol IN ('recibe', 'entrega')),
    firmante_id        UUID NOT NULL,
    firma_id           UUID NOT NULL,
    minio_key_firmado  TEXT,
    xades_level        TEXT,
    ca_fingerprint     TEXT,
    acreditado         BOOLEAN NOT NULL DEFAULT false,
    creada_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (transferencia_id, rol)
);

COMMENT ON TABLE transferencia_acta_firma_personal IS
    'Firma PERSONAL PKI por-usuario del acta de entrega (E17/F4 Inc.1) -- ADITIVA sobre el sello institucional (transferencia_acta_firma, migración 026); 1:N por transferencia (una fila por rol), append-only.';
COMMENT ON COLUMN transferencia_acta_firma_personal.rol IS
    'recibe (único rol implementado en Inc.1, el archivista receptor) | entrega (co-firma del remitente, Inc.2 -- deuda, admitida por el CHECK pero sin escritor todavía).';
COMMENT ON COLUMN transferencia_acta_firma_personal.firmante_id IS
    'auth_users.id del firmante -- SIEMPRE derivado del actor autenticado (JWT/X-User-Id), nunca de un campo de cliente sin verificar.';
COMMENT ON COLUMN transferencia_acta_firma_personal.firma_id IS
    'firma.id de signature-service (provider=xades_personal) -- INSERT idempotente sobre este valor (UNIQUE (transferencia_id, rol)) protege contra una carrera manual-vs-reintento que ya firmó pero perdió la escritura local (misma clase de riesgo S1 que el sello institucional).';
COMMENT ON COLUMN transferencia_acta_firma_personal.acreditado IS
    'RF-FIR-15: SIEMPRE false en este incremento -- art. 7 Ley 527/1999 (no acreditada ONAC), nunca art. 28 (firma digital cualificada).';

CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_personal_transferencia
    ON transferencia_acta_firma_personal (transferencia_id);

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_personal_transferencia;
-- DROP TABLE IF EXISTS transferencia_acta_firma_personal;
