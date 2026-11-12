-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 021: PPI return-path nonces live here, not in Redis.
--
-- The provider HMAC is independent of identity. A nonce is one-use inside its
-- window. INSERT … ON CONFLICT DO NOTHING is the claim; an expired row can be
-- reused after DELETE.
--
-- Idempotent. Rollback: DROP TABLE IF EXISTS ppi_nonces;

CREATE TABLE IF NOT EXISTS ppi_nonces (
    nonce      TEXT        PRIMARY KEY,
    expira_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_ppi_nonces_expira
    ON ppi_nonces (expira_at);

COMMENT ON TABLE ppi_nonces IS
    'PPI return-path nonces (one-use). Replaces Redis orpycamcp:ppi:nonce:*.';
