-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016, Inc.4): columnas del sello de tiempo
-- RFC 3161 (XAdES-T) en `firma`.
--
-- Aditiva e idempotente (ADD COLUMN IF NOT EXISTS): las firmas 'nativa' y las
-- XAdES-B sin sello de tiempo (kill-switch signer_timestamp_enabled=False o
-- firmadas antes de este incremento) quedan intactas, con estas columnas
-- nuevas en NULL/DEFAULT. El CHECK de `firma.xades_level` ya reservaba
-- 'XAdES-T' desde la migración 008 (no hace falta migrarlo aquí).
--
-- Rollback:
--   ALTER TABLE firma
--       DROP COLUMN IF EXISTS tsa_token,
--       DROP COLUMN IF EXISTS tsa_timestamp,
--       DROP COLUMN IF EXISTS tsa_cert_fingerprint,
--       DROP COLUMN IF EXISTS tsa_acreditado;

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS tsa_token             TEXT,
    ADD COLUMN IF NOT EXISTS tsa_timestamp          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tsa_cert_fingerprint    TEXT,
    ADD COLUMN IF NOT EXISTS tsa_acreditado          BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN firma.tsa_token IS
    'TimeStampToken RFC 3161 (CMS SignedData, DER en base64) que respalda el '
    'xades:SignatureTimeStamp incrustado en el XML firmado. Material PÚBLICO '
    '(certificado + firma sobre un messageImprint), no un secreto — persistirlo '
    'es correcto, a diferencia de una clave privada (RT-16). NULL para firmas '
    'nativa o xades_local sin sello de tiempo.';

COMMENT ON COLUMN firma.tsa_timestamp IS
    'genTime declarado por la TSA (local o remota) dentro del TimeStampToken — '
    'fecha del acto de firma con trazabilidad temporal VERIFICABLE '
    '(criptográficamente atada al ds:SignatureValue), NO acreditada / NO '
    'cualificada (TSA local sin acreditación ONAC — ver firma.tsa_acreditado '
    'y app.core.tsa, RF-FIR-15): no equivale a la fecha cierta oponible en '
    'sentido legal fuerte del régimen de estampado cronológico (DUR 1074/2015 '
    'Cap.47/48), reservada a entidades de certificación acreditadas. Distinto '
    'de firma.sello_tiempo (reloj del propio signature-service al momento del '
    'acto, migración 002): este campo es el reloj de la TSA, verificable '
    'criptográficamente vía tsa_token. NULL si no hay sello de tiempo.';

COMMENT ON COLUMN firma.tsa_cert_fingerprint IS
    'Huella SHA-256 (hex) del certificado de la TSA que emitió tsa_token, '
    'capturada al momento de sellar — mismo patrón que '
    'firma.certificado_fingerprint (migración 008) pero para la TSA en vez '
    'del sello institucional del tenant. NULL si no hay sello de tiempo.';

COMMENT ON COLUMN firma.tsa_acreditado IS
    'RF-FIR-15: nunca sobre-declarar conformidad. La TSA local del Incremento 4 '
    'es un par autofirmado sin acreditación ONAC — SIEMPRE false. Reservado '
    'para cuando (si alguna vez) se integre una TSA acreditada real.';
