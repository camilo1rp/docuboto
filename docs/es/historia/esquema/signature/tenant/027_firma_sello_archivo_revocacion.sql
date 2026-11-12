-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 027 (C-6, ADR-042): lo que hace falta saber de la cadena de sellos de archivo
-- para vigilarla.
--
--   * tsa_cadena_not_after   fin de vigencia de la CA por encima de la TSA del sello (NULL = TSA
--                            autofirmada o CA no localizada). Una cadena de sellos vale mientras viva
--                            todo el camino hasta su ancla: el margen de renovación mira
--                            LEAST(tsa_cert_not_after, tsa_cadena_not_after), no solo la hoja.
--   * tsa_revocacion_fuente  qué revocación ANUNCIA el certificado de esa TSA, leída de sus
--                            extensiones (DP de CRL / AIA OCSP / autofirmada), sin red. Responde a la
--                            pregunta del administrador: ¿habrá de dónde sacar la revocación cuando
--                            toque renovar?
--   * revocacion_tsa_previa  la que REALMENTE respaldó al sello anterior en el TimeStampValidationData
--                            del eslabón (crl | ocsp | autofirmada). NULL solo en el ordinal 0.
--
-- Las tres las escriben los DOS productores de eslabones (signing_service, ordinal 0; renovacion_lta,
-- ordinal n) y ninguno tiene valor por omisión: un NULL explícito ignora el DEFAULT. Sin backfill: las
-- filas anteriores son datos de prueba y se recrean; por eso el CHECK entre ordinal y
-- revocacion_tsa_previa va NOT VALID (rige para las filas nuevas sin recorrer las viejas).
--
-- Idempotente.

ALTER TABLE firma_sello_archivo ADD COLUMN IF NOT EXISTS tsa_cadena_not_after TIMESTAMPTZ;

ALTER TABLE firma_sello_archivo ADD COLUMN IF NOT EXISTS tsa_revocacion_fuente VARCHAR(12);
ALTER TABLE firma_sello_archivo DROP CONSTRAINT IF EXISTS fsa_tsa_revocacion_fuente_check;
ALTER TABLE firma_sello_archivo ADD CONSTRAINT fsa_tsa_revocacion_fuente_check
    CHECK (tsa_revocacion_fuente IN ('crl', 'ocsp', 'crl_ocsp', 'autofirmada', 'ninguna'));

ALTER TABLE firma_sello_archivo ADD COLUMN IF NOT EXISTS revocacion_tsa_previa VARCHAR(12);
ALTER TABLE firma_sello_archivo DROP CONSTRAINT IF EXISTS fsa_revocacion_tsa_previa_check;
ALTER TABLE firma_sello_archivo ADD CONSTRAINT fsa_revocacion_tsa_previa_check
    CHECK (revocacion_tsa_previa IN ('crl', 'ocsp', 'autofirmada'));

ALTER TABLE firma_sello_archivo DROP CONSTRAINT IF EXISTS fsa_previa_solo_renovados;
ALTER TABLE firma_sello_archivo ADD CONSTRAINT fsa_previa_solo_renovados
    CHECK ((ordinal = 0) = (revocacion_tsa_previa IS NULL)) NOT VALID;

-- Lo primero que caduque de la TSA y su CA: ordena la consulta de administración y el barrido del job.
CREATE INDEX IF NOT EXISTS ix_fsa_vence ON firma_sello_archivo
    (LEAST(tsa_cert_not_after, COALESCE(tsa_cadena_not_after, tsa_cert_not_after)));

-- Rollback (manual): DROP INDEX IF EXISTS ix_fsa_vence;
--   ALTER TABLE firma_sello_archivo DROP COLUMN tsa_cadena_not_after, DROP COLUMN tsa_revocacion_fuente,
--     DROP COLUMN revocacion_tsa_previa;
