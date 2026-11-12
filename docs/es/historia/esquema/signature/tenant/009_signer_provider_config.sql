-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016, Inc.1 — recomendada, no crítica):
-- bookkeeping del sello institucional activo por tenant.
--
-- NO guarda clave ni certificado privado/completo — solo metadatos de
-- observabilidad (qué huella de certificado está en uso, si se reporta como
-- acreditado). Se actualiza (upsert) cada vez que /sign-xml firma con
-- xades_local, a título informativo: NO es la fuente de verdad de qué
-- certificado firmó una firma concreta (eso vive en firma.certificado_fingerprint,
-- migración 008, inmutable por fila) — esta tabla solo refleja el sello
-- VIGENTE al momento de la última firma, útil para un panel de administración
-- futuro ("¿qué sello está activo en este tenant ahora mismo?").
--
-- Rollback:
--   DROP TABLE IF EXISTS signer_provider_config;

CREATE TABLE IF NOT EXISTS signer_provider_config (
    tenant_id       TEXT PRIMARY KEY,
    provider        TEXT NOT NULL DEFAULT 'xades_local',
    cert_fingerprint TEXT,
    acreditado      BOOLEAN NOT NULL DEFAULT false,
    actualizado_ts  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE signer_provider_config IS
    'Bookkeeping informativo del sello institucional activo por tenant (Inc.1, '
    'recomendado). NO custodia clave ni certificado — solo su huella. Se '
    'actualiza en cada firma xades_local exitosa; no es la fuente de verdad '
    'histórica (esa es firma.certificado_fingerprint por fila, migración 008).';

COMMENT ON COLUMN signer_provider_config.tenant_id IS
    'Slug del tenant (mismo valor que X-Tenant-Slug). Fila única por tenant: '
    'aunque el schema ya aísla por tenant, se guarda explícito para que el '
    'valor sea auto-descriptivo si la fila se exporta o audita fuera de contexto.';
