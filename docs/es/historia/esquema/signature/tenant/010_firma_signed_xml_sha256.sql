-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016, Inc.1 — hallazgo de auditoría Media):
-- ata `POST /signature/verify` a la firma_id concreta que se está verificando.
--
-- Antes de esta columna, /verify solo cotejaba la huella del certificado
-- embebido contra `certificado_fingerprint` (columna 008): un `signed_xml` de
-- OTRO índice firmado con el MISMO sello institucional del tenant devolvía
-- `valida=True` para cualquier `firma_id` de ese tenant (el endpoint es
-- público y no ata el documento verificado al acto de firma concreto).
--
-- `signed_xml_sha256` persiste la huella SHA-256 del XML YA FIRMADO
-- (`SignedDoc.signed_xml`, tal como se sube a MinIO) en el momento de
-- `/sign-xml`; `/verify` la recalcula sobre el `payload_xml` recibido ANTES
-- de parsear/verificar criptográficamente — un mismatch se rechaza sin
-- siquiera intentar la verificación XAdES (fail-closed, y de paso evita
-- gastar CPU parseando un documento que no corresponde a esta firma_id).
--
-- Aditiva e idempotente: NULL para filas 'nativa' (no aplica, el HMAC no
-- envuelve un XML firmado separado) y para firmas 'xades_local' anteriores a
-- esta migración (compatibilidad retro — /verify degrada a solo huella de
-- certificado para esas filas, igual que antes).
--
-- Rollback:
--   ALTER TABLE firma DROP COLUMN IF EXISTS signed_xml_sha256;

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS signed_xml_sha256 TEXT;

COMMENT ON COLUMN firma.signed_xml_sha256 IS
    'SHA-256 (hex) del XML YA FIRMADO (SignedDoc.signed_xml) persistido al '
    'momento de /sign-xml para provider=''xades_local''. /verify lo recalcula '
    'sobre el payload_xml recibido para atar la verificación a ESTA firma_id '
    'concreta (evita que el signed_xml de otro índice, firmado con el mismo '
    'sello del tenant, se reporte como válido para un firma_id ajeno). NULL '
    'para firmas ''nativa'' o filas anteriores a esta migración.';
