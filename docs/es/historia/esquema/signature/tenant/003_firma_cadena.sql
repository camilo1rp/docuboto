-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016): cadena de firma secuencial.
--
-- Modelo:
--   firma_solicitud  — cabecera de la cadena (un objeto puede tener una sola cadena abierta)
--   firma_cadena     — turnos ordenados; cada turno apunta a una fila en `firma` al resolverse
--
-- Restricción de negocio: solo una cadena abierta por objeto (índice parcial WHERE estado='pendiente').
-- Un rechazo detiene la cadena completa (firma_solicitud.estado='rechazado').
--
-- Rollback:
--   DROP TABLE IF EXISTS firma_cadena;
--   DROP TABLE IF EXISTS firma_solicitud;

CREATE TABLE IF NOT EXISTS firma_solicitud (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    objeto_tipo    VARCHAR(40)  NOT NULL,    -- radicado | indice | anexo | documento
    objeto_id      TEXT         NOT NULL,
    titulo         TEXT,                     -- etiqueta para la bandeja (asunto / nº radicado)
    payload_sha256 CHAR(64),                 -- huella del documento a firmar (vínculo firmante↔documento)
    estado         VARCHAR(20)  NOT NULL DEFAULT 'pendiente'
                   CHECK (estado IN ('pendiente', 'firmado', 'rechazado')),
    creado_por     UUID,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Solo puede existir una cadena abierta por objeto (idempotencia de solicitudes).
CREATE UNIQUE INDEX IF NOT EXISTS ux_firma_solicitud_objeto_abierta
    ON firma_solicitud (objeto_tipo, objeto_id)
    WHERE estado = 'pendiente';

COMMENT ON TABLE firma_solicitud IS
    'Cabecera de una cadena de firma sobre un objeto documental (E06, ADR-016). '
    'Un solo objeto puede tener una cadena abierta simultáneamente.';

COMMENT ON COLUMN firma_solicitud.payload_sha256 IS
    'SHA-256 del documento que se firma. Vincula cada turno al contenido exacto. '
    'Nunca es NULL para borrador/documento desde E23 (RF-PRO-09): sin huella la '
    'firma recaeria sobre el NOMBRE del objeto y sobreviviria al cambio del texto. '
    'De donde sale, que no es lo mismo en los dos casos: para objeto_tipo '
    'documento es OBLIGATORIO en el cuerpo de la peticion, porque no hay '
    'productor a quien preguntarle; para borrador la '
    'resuelve el SERVIDOR contra document-service y la del cuerpo se ignora '
    '(fuente autoritativa, ver CADENA_PAYLOAD_AUTORITATIVO) — una huella que el '
    'llamante elige no vincula a nadie. Los demas tipos aun admiten NULL.';

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS firma_cadena (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    solicitud_id  UUID         NOT NULL REFERENCES firma_solicitud(id) ON DELETE CASCADE,
    orden         INT          NOT NULL,
    firmante_id   UUID         NOT NULL,
    estado        VARCHAR(20)  NOT NULL DEFAULT 'pendiente'
                  CHECK (estado IN ('pendiente', 'firmado', 'rechazado')),
    firma_id      UUID,                     -- FK lógica a firma(id); se rellena al firmar
    comentario    TEXT,                     -- motivo de rechazo / nota del firmante
    decidido_at   TIMESTAMPTZ,
    UNIQUE (solicitud_id, orden)
);

-- Bandeja del firmante: turno activo por firmante.
CREATE INDEX IF NOT EXISTS ix_firma_cadena_pendiente
    ON firma_cadena (firmante_id, estado);

-- Consulta de turnos de una solicitud.
CREATE INDEX IF NOT EXISTS ix_firma_cadena_solicitud
    ON firma_cadena (solicitud_id);

COMMENT ON TABLE firma_cadena IS
    'Turno de firma dentro de una cadena (firma_solicitud). '
    'El turno activo es el de menor orden con estado=''pendiente''.';

COMMENT ON COLUMN firma_cadena.firma_id IS
    'UUID de la fila en firma(id) producida al resolver este turno. '
    'FK lógica (no enforced); queda NULL hasta que el firmante actúa.';
