-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016): material XAdES-LT/LTA en `firma`.
--
-- Aditiva e idempotente (ADD COLUMN IF NOT EXISTS): las firmas 'nativa', las
-- XAdES-B y las XAdES-T sin material de CA local (kill-switch
-- signer_lt_enabled=False, o tenant sin ca.crt/crl.der montado) quedan
-- intactas, con estas columnas nuevas en NULL/DEFAULT. El CHECK de
-- `firma.xades_level` ya reservaba 'XAdES-LT' y 'XAdES-LTA' desde la
-- migración 008 (no hace falta migrarlo aquí). El XML firmado en MinIO
-- (`minio_key_firmado`) sigue siendo la fuente de verdad para
-- CertificateValues/RevocationValues/ArchiveTimeStamp — estas columnas son
-- para queryabilidad/auditoría (Pieza 8), nunca autoridad.
--
-- PROHIBIDO: ninguna columna de esta migración persiste material PRIVADO
-- (ni de la CA ni de ninguna clave) — solo certificados/CRL PÚBLICOS,
-- huellas y metadatos de fechas (RT-16, línea roja del incremento).
--
-- Rollback:
--   ALTER TABLE firma
--       DROP COLUMN IF EXISTS lt_material_present,
--       DROP COLUMN IF EXISTS ca_fingerprint,
--       DROP COLUMN IF EXISTS crl_fingerprint,
--       DROP COLUMN IF EXISTS crl_this_update,
--       DROP COLUMN IF EXISTS crl_next_update,
--       DROP COLUMN IF EXISTS archive_timestamp_present,
--       DROP COLUMN IF EXISTS archive_timestamp_at,
--       DROP COLUMN IF EXISTS archive_tsa_cert_fingerprint,
--       DROP COLUMN IF EXISTS revocation_provenance,
--       DROP COLUMN IF EXISTS archive_timestamp_token;
--   -- El CHECK firma_nivel_conformidad_check vuelve a su forma de la
--   -- migración 008 (ver ese archivo) si se hace rollback de esta migración.

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS lt_material_present          BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ca_fingerprint                TEXT,
    ADD COLUMN IF NOT EXISTS crl_fingerprint               TEXT,
    ADD COLUMN IF NOT EXISTS crl_this_update               TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS crl_next_update               TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archive_timestamp_present     BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS archive_timestamp_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archive_tsa_cert_fingerprint  TEXT,
    ADD COLUMN IF NOT EXISTS revocation_provenance         TEXT,
    ADD COLUMN IF NOT EXISTS archive_timestamp_token       TEXT;

-- CHECK aditivo (RF-FIR-15, no sobre-declarar): añade los dos valores nuevos
-- de nivel_conformidad para LT/LTA sin quitar los ya existentes (008). No hay
-- "ADD CONSTRAINT IF NOT EXISTS" para CHECK en Postgres, así que se DROPea
-- (IF EXISTS) y se re-crea siempre — seguro de re-ejecutar: tras el DROP el
-- estado es el mismo, sea la primera corrida o una repetición.
ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
ALTER TABLE firma
    ADD CONSTRAINT firma_nivel_conformidad_check
    CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
        'firma_electronica_nativa',
        'xades_b_no_acreditado',
        'xades_b_acreditado',        -- reservado: sin CA acreditada ONAC integrada
        'xades_lt_no_acreditado',
        'xades_lta_no_acreditado'
    ));

COMMENT ON COLUMN firma.lt_material_present IS
    'True cuando la firma incluye xades:CertificateValues/RevocationValues '
    '(material de una CA local de DESARROLLO, scripts/gen-dev-ca.sh) — '
    'siempre false para firmas nativa/XAdES-B/XAdES-T o cuando '
    'settings.signer_lt_enabled=false / el tenant no tiene ca.crt/crl.der '
    'montado junto a su sello (degradación observable, ver '
    'firma.lt_downgrade_no_ca en audit_log).';

COMMENT ON COLUMN firma.ca_fingerprint IS
    'Huella SHA-256 (hex) del certificado de la CA local embebido en '
    'xades:CertificateValues al momento de firmar — ancla de confianza '
    'persistida para /verify (mismo patrón de pinning que '
    'firma.tsa_cert_fingerprint, migración 012): sin cotejar contra este '
    'valor, cualquiera podría anexar CertificateValues de su propia CA.';

COMMENT ON COLUMN firma.crl_fingerprint IS
    'Huella SHA-256 (hex) de la CRL (crl.der) embebida al momento de firmar '
    '— trazabilidad/auditoría; la autoridad para /verify es la CRL '
    'REALMENTE embebida en el XML firmado (MinIO), no esta columna.';

COMMENT ON COLUMN firma.crl_this_update IS
    'thisUpdate declarado por la CRL de la CA local al momento de firmar.';

COMMENT ON COLUMN firma.crl_next_update IS
    'nextUpdate declarado por la CRL de la CA local al momento de firmar — '
    'las CRL de desarrollo caducan con frecuencia; un vencimiento NO invalida '
    'por sí solo una verificación posterior en este perfil no acreditado '
    '(se expone para diagnóstico, ver VerifyResponse.crl_next_update).';

COMMENT ON COLUMN firma.archive_timestamp_present IS
    'True cuando la firma incluye xadesv141:ArchiveTimeStamp (XAdES-LTA '
    'simplificado, no acreditado) — requiere lt_material_present=true. '
    'False para XAdES-B/-T/-LT o firmas nativa.';

COMMENT ON COLUMN firma.archive_timestamp_at IS
    'genTime declarado por el TimeStampToken RFC 3161 del ArchiveTimeStamp '
    '(misma TSA local que sella XAdES-T, ver firma.tsa_timestamp) — NULL si '
    'archive_timestamp_present=false.';

COMMENT ON COLUMN firma.archive_tsa_cert_fingerprint IS
    'Huella SHA-256 (hex) del certificado de la TSA que emitió el '
    'ArchiveTimeStamp — hoy siempre igual a firma.tsa_cert_fingerprint (la '
    'MISMA TSA local sella tanto SignatureTimeStamp como ArchiveTimeStamp), '
    'columna separada por si un incremento futuro usa TSAs distintas.';

COMMENT ON COLUMN firma.revocation_provenance IS
    'Procedencia del material de revocación embebido: SIEMPRE ''dev'' en '
    'este incremento (CRL de una CA local de desarrollo, jamás de una PKI '
    'acreditada) — RF-FIR-15, no ocultar la procedencia. NULL si '
    'lt_material_present=false.';

COMMENT ON COLUMN firma.archive_timestamp_token IS
    'TimeStampToken RFC 3161 del ArchiveTimeStamp (CMS SignedData, DER en '
    'base64) — material PÚBLICO (igual que firma.tsa_token, migración 012), '
    'persistido por conveniencia de consulta; la autoridad es el XML firmado '
    'en MinIO. NULL si archive_timestamp_present=false.';
