-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 038 (AREA-09-14): binarios que una importación fallida subió a
-- storage y que NINGÚN radicado referencia.
--
-- Antes solo se ASENTABAN (document.import_binarios_sin_radicado) y se quedaban en
-- el almacén para siempre. Ahora se PURGAN, pero storage deduplica por SHA-256: el
-- objeto puede sostener anexos de otros radicados, así que el borrado lo decide
-- storage-service comprobando referencias en su propia transacción
-- (POST /internal/purgar-huerfanos). Esta tabla es la cola DURABLE de ese borrado:
-- si storage no responde, la fila queda 'pendiente' y el barrido la reintenta; el
-- asiento de «sin radicado» ya está escrito y no se pierde.
--
-- Estados: pendiente (por intentar/reintentar) | purgado (borrado, o ya no estaba) |
-- conservado (lo referencia un anexo, o es anterior a la importación: se queda) |
-- protegido (bajo WORM) | agotado (se superó el máximo de reintentos: revisar a mano).
-- Idempotente; search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS import_binario_huerfano (
    id            UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    import_uuid   UUID        NOT NULL,
    file_id       UUID        NOT NULL,
    sha256        VARCHAR(64),
    actor         UUID,
    -- Instante a partir del cual un objeto cuenta como CREADO por esta importación;
    -- storage no borra nada anterior (una coincidencia de hash no es suyo).
    subido_desde  TIMESTAMPTZ NOT NULL,
    estado        VARCHAR(20) NOT NULL DEFAULT 'pendiente',
    intentos      INTEGER     NOT NULL DEFAULT 0,
    ultimo_error  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resuelto_at   TIMESTAMPTZ,
    CONSTRAINT ck_import_binario_huerfano_estado
        CHECK (estado IN ('pendiente', 'purgado', 'conservado', 'protegido', 'agotado')),
    CONSTRAINT ux_import_binario_huerfano UNIQUE (import_uuid, file_id)
);

CREATE INDEX IF NOT EXISTS ix_import_binario_huerfano_pendiente
    ON import_binario_huerfano (created_at) WHERE estado = 'pendiente';
