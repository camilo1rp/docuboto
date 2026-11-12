-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior. Véase LICENSE y <https://www.gnu.org/licenses/>.

-- ============================================================================
-- E19 Increment 1 — Autorización OAuth2 en curso.
--
-- Una fila por consentimiento iniciado y todavía sin canjear. Existe por dos
-- razones de seguridad, no por comodidad:
--
-- 1. **`state` de un solo uso** (RFC 6749 §10.12). Sin él, un tercero puede
--    inducir al administrador a completar un flujo que el atacante inició, y la
--    cuenta de la entidad acabaría autorizada contra un buzón ajeno —o, peor, el
--    refresh token del atacante quedaría guardado como si fuera de la entidad—.
--    El `state` se marca usado al canjear: reproducir la redirección no vuelve a
--    funcionar.
--
-- 2. **PKCE** (RFC 7636). El `code_verifier` no puede viajar en la redirección,
--    así que se guarda aquí y se envía solo en el canje servidor-a-servidor. Con
--    él, un código de autorización interceptado en el navegador no sirve de nada
--    sin el verificador, que nunca salió del servidor.
--
-- Las filas caducan: un consentimiento que nadie completó no debe quedar
-- utilizable indefinidamente.
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_oauth_state (
    state          VARCHAR(128) PRIMARY KEY,
    cuenta_id      UUID NOT NULL REFERENCES email_cuenta (id) ON DELETE CASCADE,

    -- Quién inició el flujo. El canje exige que sea la MISMA persona: si no,
    -- bastaría con que un administrador iniciara la autorización para que
    -- cualquier otro pudiera completarla con un buzón suyo.
    iniciado_por   UUID NOT NULL,

    -- PKCE. Nunca sale del servidor.
    code_verifier  VARCHAR(128) NOT NULL,

    -- Se guarda el `redirect_uri` usado: el proveedor exige que el del canje sea
    -- IDÉNTICO al de la autorización, y si la configuración de la cuenta cambia
    -- entre medias, el canje fallaría con un error que no explica nada.
    redirect_uri   VARCHAR(500) NOT NULL,

    creado_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_at      TIMESTAMPTZ NOT NULL,
    usado_at       TIMESTAMPTZ,

    CONSTRAINT chk_email_oauth_state_vigencia CHECK (expira_at > creado_at)
);

CREATE INDEX IF NOT EXISTS ix_email_oauth_state_cuenta
    ON email_oauth_state (cuenta_id);

-- Para la limpieza de los caducados sin recorrer la tabla entera.
CREATE INDEX IF NOT EXISTS ix_email_oauth_state_expira
    ON email_oauth_state (expira_at)
    WHERE usado_at IS NULL;
