-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15, F4, ADR-015/Acuerdo AGN 001/2024): índice electrónico.
--
-- Índice electrónico del expediente: versionado APPEND-ONLY (cada cambio del
-- conjunto documental crea una versión nueva; el histórico no se borra ni se
-- actualiza) + snapshot por documento con orden original inmutable y valor huella.
-- `valor_huella` es nullable en este incremento: se puebla con el SHA-256 de
-- storage-service cuando se cable la integración de fixity (E15 T-08).

CREATE TABLE IF NOT EXISTS expediente_indice (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expediente_id     UUID    NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    version           INTEGER NOT NULL,            -- 0 = creación; ++ por cada cambio
    xml               TEXT    NOT NULL,             -- documento XML del índice
    xml_sha256        CHAR(64) NOT NULL,            -- huella del propio índice (C14N)
    content_hash      CHAR(64) NOT NULL,            -- hash del conjunto (idempotencia)
    algoritmo         TEXT    NOT NULL DEFAULT 'SHA-256',
    xsd_version       TEXT    NOT NULL DEFAULT 'v1',
    num_documentos    INTEGER NOT NULL,
    estado            TEXT    NOT NULL DEFAULT 'vigente'
                      CHECK (estado IN ('vigente','firmado')),
    firma_id          UUID,
    minio_key_firmado TEXT,
    generado_por      TEXT,
    generado_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (expediente_id, version)
);

CREATE INDEX IF NOT EXISTS idx_indice_exp
    ON expediente_indice (expediente_id, version DESC);

CREATE TABLE IF NOT EXISTS expediente_indice_item (
    indice_id         UUID NOT NULL REFERENCES expediente_indice(id) ON DELETE CASCADE,
    document_id       UUID NOT NULL,
    orden             INTEGER NOT NULL,            -- principio de orden original
    titulo            TEXT,
    tipologia         TEXT,
    fecha_declaracion DATE,
    fecha_archivado   TIMESTAMPTZ,
    valor_huella      CHAR(64),                    -- SHA-256 (de storage-service); NULL = pendiente
    funcion_resumen   TEXT NOT NULL DEFAULT 'SHA-256',
    formato           TEXT,
    tamano_bytes      BIGINT,
    PRIMARY KEY (indice_id, document_id)
);
