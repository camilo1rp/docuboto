-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B4, ADR-018): constancia de CADA entrega de webhook saliente.
--
-- Hasta aquí `dispatch` era best-effort y mudo: un webhook que entregó, que falló o
-- que se rechazó por destino no dejaba rastro alguno, así que la entidad no podía
-- probar a quién salió qué evento ni cuándo (Ley 594/2000 arts. 4 y 19).
--
-- Dos filas por intento, APPEND-ONLY:
--   * `envio`     — se escribe ANTES del POST; si no puede escribirse, no se envía
--                   (fail-closed: nada sale sin constancia previa);
--   * `resultado` — se escribe DESPUÉS, con el desenlace (`entregado`, `http_error`,
--                   `error_red`, `destino_rechazado`, `secreto_inutilizable`).
-- Una entrega que no llega a enviarse (destino rechazado, secreto inutilizable)
-- deja solo la fila `resultado`.
--
-- Sin FK a `webhook_subscription`: borrar la suscripción NO borra la constancia
-- (por eso se guarda `url_destino` y su huella). NUNCA se guarda el cuerpo: solo su
-- SHA-256, porque el sobre puede llevar datos personales. `url_destino` es solo
-- esquema://host[:puerto] —ni credenciales, ni ruta, ni query— porque Slack/Teams
-- ponen el token en la ruta; `url_sha256` correlaciona sin exponerlo.
--
-- Retención/purga: PENDIENTE y declarada en ADR-018 (la tabla es append-only; una
-- purga exigirá un rol de ops y su propio asiento).
-- Idempotente. Rollback manual: DROP TABLE webhook_entregas;

CREATE TABLE IF NOT EXISTS webhook_entregas (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entrega_id  UUID NOT NULL,
    fase        VARCHAR(9) NOT NULL CHECK (fase IN ('envio', 'resultado')),
    webhook_id  UUID NOT NULL,
    url_destino TEXT NOT NULL,
    url_sha256  CHAR(64) NOT NULL,
    event_id    TEXT,
    event_type  TEXT NOT NULL,
    intento     SMALLINT NOT NULL CHECK (intento >= 1),
    body_sha256 CHAR(64) NOT NULL,
    recortado   BOOLEAN NOT NULL DEFAULT false,
    firmado     BOOLEAN NOT NULL DEFAULT false,
    resultado   VARCHAR(24),
    http_status SMALLINT,
    error       VARCHAR(500),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_webhook_entregas_resultado
        CHECK (resultado IS NULL OR resultado IN
               ('entregado', 'http_error', 'error_red', 'destino_rechazado', 'secreto_inutilizable')),
    -- `envio` no lleva desenlace; `resultado` siempre.
    CONSTRAINT chk_webhook_entregas_fase_resultado
        CHECK ((fase = 'envio' AND resultado IS NULL) OR (fase = 'resultado' AND resultado IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_webhook_entregas_webhook ON webhook_entregas (webhook_id, at DESC);
CREATE INDEX IF NOT EXISTS ix_webhook_entregas_event ON webhook_entregas (event_id);

CREATE OR REPLACE FUNCTION reject_webhook_entregas_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'webhook_entregas is append-only (constancia de entrega inmutable; Ley 594/2000 arts. 4 y 19)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_webhook_entregas_immutable ON webhook_entregas;
CREATE TRIGGER trg_webhook_entregas_immutable
    BEFORE UPDATE OR DELETE ON webhook_entregas
    FOR EACH ROW EXECUTE FUNCTION reject_webhook_entregas_mutation();

-- TRUNCATE no dispara los triggers de fila: sin esta guarda vaciaría la tabla entera.
CREATE OR REPLACE FUNCTION reject_webhook_entregas_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'webhook_entregas is append-only: TRUNCATE denegado';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_webhook_entregas_no_truncate ON webhook_entregas;
CREATE TRIGGER trg_webhook_entregas_no_truncate
    BEFORE TRUNCATE ON webhook_entregas
    FOR EACH STATEMENT EXECUTE FUNCTION reject_webhook_entregas_truncate();

-- Capa de privilegios: el rol de la aplicación no puede ni intentar mutar. Sin
-- `orpycamcp_app` (dev, donde la app corre como propietario) no hay a quién revocar.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE %I.webhook_entregas FROM orpycamcp_app',
                       current_schema());
    END IF;
END;
$$;
