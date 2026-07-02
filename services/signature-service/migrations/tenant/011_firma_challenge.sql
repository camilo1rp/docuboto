-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06 Incremento 2, RF-FIR-13): 2FA por OTP-correo en la
-- firma personal de la cadena de firma secuencial.
--
-- `firma_challenge` es el análogo, para el segundo factor, de lo que
-- `firma_solicitud`/`firma_cadena` son para la cadena misma (003): una fila
-- por cada código de un solo uso emitido para un turno concreto de firma.
--
-- INVARIANTE RECTOR: el OTP en claro NUNCA se persiste — solo `otp_hash`
-- (HMAC-SHA256 con `settings.signature_secret`, el mismo secreto de servicio
-- que ya protege la firma nativa en `signer.py`, D-01).
--
-- `hash_documento` ata el challenge al `payload_sha256` de la solicitud en el
-- momento de emitirlo (RF-FIR-13, Q1): un challenge nunca es válido para un
-- documento distinto de aquel para el que se emitió, aunque comparta
-- `solicitud_id` (protección cross-documento).
--
-- Solo puede existir UN challenge activo (`pendiente`|`verificado`) por
-- combinación (`solicitud_id`, `firmante_id`, `hash_documento`) — el índice
-- único parcial `uq_firma_challenge_activo` es la misma técnica que
-- `ux_firma_solicitud_objeto_abierta` (003): reemplazo idempotente, nunca dos
-- challenges vivos compitiendo por el mismo acto de firma.
--
-- Rollback:
--   DROP TABLE IF EXISTS firma_challenge;

CREATE TABLE IF NOT EXISTS firma_challenge (
    challenge_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    solicitud_id   UUID         NOT NULL,
    firmante_id    UUID         NOT NULL,
    hash_documento CHAR(64)     NOT NULL,
    factor_tipo    TEXT         NOT NULL DEFAULT 'otp_email'
                   CHECK (factor_tipo IN ('otp_email')),
    otp_hash       TEXT         NOT NULL,
    estado         TEXT         NOT NULL DEFAULT 'pendiente'
                   CHECK (estado IN ('pendiente', 'verificado', 'consumido', 'expirado', 'bloqueado')),
    intentos       INT          NOT NULL DEFAULT 0,
    max_intentos   INT          NOT NULL,
    expira_at      TIMESTAMPTZ  NOT NULL,
    verificado_at  TIMESTAMPTZ,
    consumido_at   TIMESTAMPTZ,
    creado_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    canal_masked   TEXT         NOT NULL,
    tenant_slug    TEXT         NOT NULL
);

-- Un solo challenge vivo por (solicitud, firmante, documento) — la emisión de
-- uno nuevo primero expira el anterior (reemplazo idempotente, ver Q1/Q2 del
-- contrato E06 Inc.2) para nunca violar este índice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_firma_challenge_activo
    ON firma_challenge (solicitud_id, firmante_id, hash_documento)
    WHERE estado IN ('pendiente', 'verificado');

-- Barrido/expiración operativa por estado + vencimiento (uso futuro: job de
-- limpieza análogo a app/jobs/deadletter_retention.py; no forma parte de este
-- incremento, pero el índice ya deja el acceso listo).
CREATE INDEX IF NOT EXISTS ix_firma_challenge_expira
    ON firma_challenge (estado, expira_at);

COMMENT ON TABLE firma_challenge IS
    'Segundo factor (OTP por correo, RF-FIR-13) para la firma personal de un '
    'turno de firma_cadena. El OTP en claro NUNCA se persiste — solo su HMAC '
    '(otp_hash). Un challenge se emite atado a un hash_documento concreto y se '
    'consume atómicamente junto con el marcado del turno (anti-replay).';

COMMENT ON COLUMN firma_challenge.solicitud_id IS
    'FK lógica a firma_solicitud.id (no enforced — mismo patrón que firma_cadena.firma_id).';

COMMENT ON COLUMN firma_challenge.firmante_id IS
    'Debe coincidir con firma_cadena.firmante_id del turno activo en el momento '
    'de emitir el challenge (RF-FIR-11) — el emisor solo puede ser el propio firmante.';

COMMENT ON COLUMN firma_challenge.hash_documento IS
    'Snapshot de firma_solicitud.payload_sha256 al emitir el challenge. Ata el '
    'segundo factor al documento exacto (protección cross-documento, RF-FIR-13).';

COMMENT ON COLUMN firma_challenge.otp_hash IS
    'HMAC-SHA256(otp, signature_secret). El OTP en claro NUNCA se persiste, ni '
    'se loguea, ni se registra en audit_log/Redis, ni se devuelve en respuesta alguna.';

COMMENT ON COLUMN firma_challenge.estado IS
    'pendiente: emitido, esperando OTP. verificado: reservado para un futuro '
    'flujo de verificación desacoplada del acto de firma (no usado en Inc.2 — '
    '/sign verifica y consume en un solo paso). consumido: OTP correcto y ya '
    'aplicado a una firma (terminal, anti-replay). expirado: venció expira_at '
    'sin verificarse. bloqueado: agotó max_intentos.';

COMMENT ON COLUMN firma_challenge.intentos IS
    'Contador de verificaciones fallidas. Se incrementa y persiste de forma '
    'durable aunque el intento de firma se aborte (RF-FIR-13) — nunca se '
    'pierde con un rollback del acto de firma que lo originó.';

COMMENT ON COLUMN firma_challenge.max_intentos IS
    'Snapshot de settings.otp_max_intentos al emitir el challenge (cambios de '
    'configuración posteriores no alteran challenges ya emitidos).';

COMMENT ON COLUMN firma_challenge.canal_masked IS
    'Canal de entrega enmascarado para exhibición segura (p.ej. j***@dominio.gov.co). '
    'NUNCA el email completo en claro en esta columna.';

COMMENT ON COLUMN firma_challenge.tenant_slug IS
    'Slug del tenant al momento de emitir el challenge — trazabilidad explícita '
    'en la fila (redundante con el search_path del schema, igual que otras '
    'tablas de auditoría del proyecto).';
