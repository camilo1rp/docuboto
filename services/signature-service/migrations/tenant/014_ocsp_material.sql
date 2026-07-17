-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, RF-FIR-15): material OCSP stapled en `firma`.
--
-- Aditiva e idempotente (ADD COLUMN IF NOT EXISTS): las firmas 'nativa', las
-- XAdES-B/-T y las XAdES-LT/LTA sin OCSP (kill-switch
-- signer_ocsp_enabled=False, o tenant sin ocsp.crt/ocsp.key montado) quedan
-- intactas, con estas columnas nuevas en NULL/DEFAULT. NO se toca el CHECK
-- de `firma.nivel_conformidad` (migración 013) ni `firma.xades_level` — OCSP
-- es ADITIVO a XAdES-LT/LTA, nunca un nivel de conformidad nuevo. El XML
-- firmado en MinIO (`minio_key_firmado`) sigue siendo la fuente de verdad
-- para xades:OCSPValues — estas columnas son para queryabilidad/auditoría
-- (mismo patrón que la migración 013), nunca autoridad.
--
-- PROHIBIDO: ninguna columna de esta migración persiste material PRIVADO
-- (ni la clave del responder OCSP ni ninguna otra) — solo la respuesta OCSP
-- PÚBLICA (BasicOCSPResponse DER), huellas y metadatos de fechas (RT-16,
-- línea roja del incremento — misma regla no-fuga que `seal.key`).
--
-- Rollback:
--   ALTER TABLE firma
--       DROP COLUMN IF EXISTS ocsp_present,
--       DROP COLUMN IF EXISTS ocsp_responder_fingerprint,
--       DROP COLUMN IF EXISTS ocsp_cert_status,
--       DROP COLUMN IF EXISTS ocsp_produced_at,
--       DROP COLUMN IF EXISTS ocsp_this_update,
--       DROP COLUMN IF EXISTS ocsp_next_update,
--       DROP COLUMN IF EXISTS ocsp_response_der;

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS ocsp_present               BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ocsp_responder_fingerprint  TEXT,
    ADD COLUMN IF NOT EXISTS ocsp_cert_status            TEXT,
    ADD COLUMN IF NOT EXISTS ocsp_produced_at            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ocsp_this_update            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ocsp_next_update            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ocsp_response_der           TEXT;

COMMENT ON COLUMN firma.ocsp_present IS
    'True cuando la firma incluye xades:RevocationValues/xades:OCSPValues '
    '(OCSP stapled, responder delegado emitido por la CA local de '
    'DESARROLLO, scripts/gen-dev-ocsp-responder.sh) — siempre false para '
    'firmas nativa/XAdES-B/XAdES-T/XAdES-LT/XAdES-LTA sin OCSP o cuando '
    'settings.signer_ocsp_enabled=false / el tenant no tiene '
    'ocsp.crt/ocsp.key montado junto a su sello (degradación observable, '
    'ver firma.ocsp_downgrade_no_responder en audit_log).';

COMMENT ON COLUMN firma.ocsp_responder_fingerprint IS
    'Huella SHA-256 (hex) del certificado del responder OCSP embebido en '
    'xades:OCSPValues al momento de firmar — ancla de confianza persistida '
    'para /verify (mismo patrón de pinning que firma.tsa_cert_fingerprint '
    'migración 012 y firma.ca_fingerprint migración 013): sin cotejar '
    'contra este valor, cualquiera podría anexar una respuesta OCSP firmada '
    'con su propio responder.';

COMMENT ON COLUMN firma.ocsp_cert_status IS
    'certStatus declarado por la respuesta OCSP al momento de firmar '
    '(''good''|''revoked''), derivado de la MISMA CRL embebida en '
    'xades:CRLValues — coherencia CRL<->OCSP, nunca dos fuentes de verdad '
    'de revocación divergentes dentro del mismo acto de firma.';

COMMENT ON COLUMN firma.ocsp_produced_at IS
    'producedAt declarado por la BasicOCSPResponse al momento de firmar.';

COMMENT ON COLUMN firma.ocsp_this_update IS
    'thisUpdate declarado por la respuesta OCSP al momento de firmar.';

COMMENT ON COLUMN firma.ocsp_next_update IS
    'nextUpdate declarado por la respuesta OCSP al momento de firmar '
    '(settings.signer_ocsp_validity_days desde thisUpdate) — un vencimiento '
    'NO invalida por sí solo una verificación posterior en este perfil no '
    'acreditado (se expone para diagnóstico, mismo criterio que '
    'firma.crl_next_update).';

COMMENT ON COLUMN firma.ocsp_response_der IS
    'BasicOCSPResponse (RFC 6960, DER en base64) — material PÚBLICO (igual '
    'que firma.tsa_token/archive_timestamp_token), persistido por '
    'conveniencia de consulta; la autoridad es el XML firmado en MinIO. '
    'NULL si ocsp_present=false. NUNCA la clave privada del responder OCSP '
    '(RT-16, misma regla no-fuga que seal.key).';
