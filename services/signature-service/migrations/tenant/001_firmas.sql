-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016): firmas electrónicas (signature-service).
--
-- Registro de firma por tenant. El proveedor de firma es PLUGGABLE: el stub nativo
-- por defecto produce una firma electrónica (hash SHA-256 + identidad + sello), no
-- una firma digital PKI. Un proveedor XAdES/PAdES/TSA real se conecta sin cambiar
-- el contrato ni este esquema (se añade firma_blob/refrendo del proveedor).

CREATE TABLE IF NOT EXISTS firma (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    objeto_tipo        VARCHAR(40) NOT NULL,        -- indice | radicado | anexo | ...
    objeto_id          TEXT,
    formato_solicitado VARCHAR(20),                 -- XAdES | PAdES | hash-256
    firmante_id        UUID,
    payload_sha256     CHAR(64) NOT NULL,
    firma_blob         TEXT NOT NULL,
    provider           VARCHAR(30) NOT NULL,        -- nativa | xades | ...
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_firma_objeto ON firma (objeto_tipo, objeto_id);
