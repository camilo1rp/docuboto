-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, F3, ADR-016): firmas electrónicas.
--
-- Registro verificable de firma: identidad del firmante + hash SHA-256 del
-- contenido firmado + sello de tiempo + motivo. La firma verifica si su
-- `content_hash` coincide con el hash actual del contenido (detección de
-- alteración). Firma electrónica (Decreto 2364/2012), no firma digital PKI.

CREATE TABLE IF NOT EXISTS signatures (
    id            UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id   UUID         NOT NULL,
    anexo_id      UUID,                       -- anexo concreto firmado (opcional)
    signer_id     UUID,                       -- X-User-Id (Keycloak)
    signer_name   VARCHAR(255),
    content_hash  VARCHAR(64)  NOT NULL,      -- SHA-256 hex del contenido firmado
    reason        TEXT,
    signed_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_signatures_radicado ON signatures (radicado_id);
