-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016, Inc.1): columnas XAdES-B en `firma`.
--
-- Aditiva e idempotente (ADD COLUMN IF NOT EXISTS): las firmas 'nativa' ya
-- persistidas quedan intactas, con estas columnas nuevas en NULL. `tipo_firma`
-- ya existía desde la migración 002 (VARCHAR(30) DEFAULT 'electronica-nativa')
-- — IF NOT EXISTS la deja sin tocar; el Incremento 1 fija explícitamente
-- 'nativa'/'sello_sistema' en cada INSERT nuevo (ver FirmaRepository.insert),
-- así que el DEFAULT histórico de columna deja de ser relevante hacia adelante.
--
-- Rollback:
--   ALTER TABLE firma
--       DROP COLUMN IF EXISTS certificado_pem,
--       DROP COLUMN IF EXISTS minio_key_firmado,
--       DROP COLUMN IF EXISTS nivel_conformidad,
--       DROP COLUMN IF EXISTS xades_level,
--       DROP COLUMN IF EXISTS certificado_fingerprint;
--   ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
--   ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_xades_level_check;

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS certificado_pem         TEXT,
    ADD COLUMN IF NOT EXISTS minio_key_firmado        TEXT,
    ADD COLUMN IF NOT EXISTS nivel_conformidad         TEXT,
    ADD COLUMN IF NOT EXISTS xades_level               TEXT,
    ADD COLUMN IF NOT EXISTS tipo_firma                TEXT DEFAULT 'nativa',
    ADD COLUMN IF NOT EXISTS certificado_fingerprint    TEXT;

-- CHECK laxo (admite NULL — filas históricas y firmas 'nativa' sin estos
-- campos). No hay "ADD CONSTRAINT IF NOT EXISTS" en Postgres para CHECK, así
-- que se envuelve en un DO block idempotente (re-ejecutar el archivo no falla
-- con "constraint already exists").
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'firma_nivel_conformidad_check'
    ) THEN
        ALTER TABLE firma
            ADD CONSTRAINT firma_nivel_conformidad_check
            CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
                'firma_electronica_nativa',
                'xades_b_no_acreditado',
                'xades_b_acreditado'  -- reservado: sin CA acreditada ONAC integrada (Inc.1)
            ));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'firma_xades_level_check'
    ) THEN
        ALTER TABLE firma
            ADD CONSTRAINT firma_xades_level_check
            CHECK (xades_level IS NULL OR xades_level IN (
                'XAdES-B',
                'XAdES-T',    -- reservado: TSA/RFC 3161, no implementado
                'XAdES-LT',   -- reservado: material de validación a largo plazo
                'XAdES-LTA'   -- reservado: archivado a largo plazo, requiere CRL/OCSP
            ));
    END IF;
END $$;

COMMENT ON COLUMN firma.certificado_pem IS
    'Certificado X.509 PÚBLICO (PEM) del sello institucional usado para firmar. '
    'NUNCA la clave privada — esa nunca se persiste ni se loguea (RT-16).';

COMMENT ON COLUMN firma.minio_key_firmado IS
    'object_key en MinIO (storage-service, bucket orpycamcp-{slug}-documents) '
    'del XML COMPLETO ya firmado (XAdES-B). NULL para firmas nativa (el HMAC '
    'no se incrusta en un documento separado).';

COMMENT ON COLUMN firma.nivel_conformidad IS
    'Grado de conformidad declarado del material de firma (RF-FIR-15: nunca '
    'sobre-declarar). Distinto de nivel_seguridad (clearance RF-SEG-08, '
    'migración 004) — NO reusar ese concepto. '
    '''xades_b_acreditado'' queda reservado sin productor en el Incremento 1: '
    'no hay integración con una CA acreditada ONAC.';

COMMENT ON COLUMN firma.xades_level IS
    'Nivel XAdES del formato de firma (''XAdES-B'' en el Incremento 1). NULL '
    'para firmas nativa (HMAC, sin XML-DSig). T/LT/LTA reservados — ver '
    'app.providers.base.SignatureFormat.';

COMMENT ON COLUMN firma.tipo_firma IS
    'Tipo de acto de firma: ''nativa'' (HMAC de servicio) | ''sello_sistema'' '
    '(XAdES-B con el sello institucional del tenant, atribuido a la persona '
    'que actúa vía firmante_id + audit_log, RF-FIR-11). No confundir con '
    'provider (columna existente, identifica la IMPLEMENTACIÓN pluggable).';

COMMENT ON COLUMN firma.certificado_fingerprint IS
    'Huella SHA-256 (hex) del certificado usado para firmar, capturada al '
    'momento de la firma. Fuente de verdad para /verify al cotejar el '
    'certificado embebido en una firma XAdES — no depende de releer el sello '
    'actual del tenant (que pudo rotar desde entonces).';
