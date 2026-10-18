-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 026 (B10 §8): cadena de sellos de archivo de una firma XAdES-LTA.
--
-- Una firma LTA lleva un `ArchiveTimeStamp` que vale mientras viva el certificado de su TSA. Para
-- seguir siendo prueba hay que RENOVARLO antes de que caduque: el XML renovado es un objeto NUEVO
-- que contiene todos los sellos anteriores. Esta tabla es el registro de esa cadena; `firma` (y su
-- `minio_key_firmado`) sigue siendo el registro del acto original y no se toca.
--
--   * ordinal 0  = el ATS de la firma original (se inserta en la misma tx que la fila `firma`).
--   * ordinal n  = el n-ésimo ATS renovado.
--   * estado `pendiente_worm`: el XML renovado ya está en storage pero storage aún no registró la
--     herencia de la política WORM; el job reintenta SOLO ese paso.
--   * estado `caducado`: el certificado de la TSA de este (último) sello caducó SIN renovarse. Ya no
--     prueba nada y no es candidato a renovar; sigue siendo el artefacto vigente (se sirve, y
--     `/verify` lo degrada a XAdES-LT). Asiento `firma.lta_cadena_caducada`.
--   * `ultimo_aviso`: último motivo por el que el job no renovó este eslabón (rechazada, TSA sin
--     más plazo, sin revocación de la TSA...). Sirve para asentar UNA vez por motivo y no a diario.
--
-- Las firmas LTA anteriores a esta migración no tienen fila y NO son renovables: son datos de
-- prueba y se recrean (sin backfill).
--
-- Idempotente.

CREATE TABLE IF NOT EXISTS firma_sello_archivo (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    firma_id              UUID NOT NULL REFERENCES firma(id),
    ordinal               SMALLINT NOT NULL CHECK (ordinal >= 0),
    minio_key             TEXT NOT NULL,
    xml_sha256            CHAR(64) NOT NULL,
    gen_time              TIMESTAMPTZ NOT NULL,
    tsa_cert_fingerprint  CHAR(64) NOT NULL,
    tsa_cert_not_after    TIMESTAMPTZ NOT NULL,
    estado                VARCHAR(16) NOT NULL DEFAULT 'vigente'
                          CHECK (estado IN ('pendiente_worm', 'vigente', 'caducado')),
    ultimo_aviso          VARCHAR(80),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (firma_id, ordinal)
);

-- Re-aplicable sobre un tenant que ya tenía la tabla de una versión anterior de esta migración.
ALTER TABLE firma_sello_archivo ADD COLUMN IF NOT EXISTS ultimo_aviso VARCHAR(80);
ALTER TABLE firma_sello_archivo DROP CONSTRAINT IF EXISTS firma_sello_archivo_estado_check;
ALTER TABLE firma_sello_archivo ADD CONSTRAINT firma_sello_archivo_estado_check
    CHECK (estado IN ('pendiente_worm', 'vigente', 'caducado'));

CREATE INDEX IF NOT EXISTS ix_fsa_vencimiento ON firma_sello_archivo (tsa_cert_not_after);

-- El artefacto que representa HOY a cada firma: el XML de la última fila `vigente` (o `caducado`) de la cadena,
-- o el original si no hay cadena. Lo leen `/verify` y la descarga del XML firmado.
CREATE OR REPLACE VIEW firma_artefacto_vigente AS
SELECT DISTINCT ON (f.id)
       f.id                                          AS firma_id,
       COALESCE(s.minio_key, f.minio_key_firmado)    AS minio_key,
       COALESCE(s.xml_sha256, f.signed_xml_sha256)   AS xml_sha256,
       COALESCE(s.ordinal, 0)                        AS ordinal
  FROM firma f
  LEFT JOIN firma_sello_archivo s ON s.firma_id = f.id AND s.estado IN ('vigente', 'caducado')
 ORDER BY f.id, s.ordinal DESC NULLS LAST;

-- Rollback (manual): DROP VIEW IF EXISTS firma_artefacto_vigente; DROP TABLE IF EXISTS firma_sello_archivo;
