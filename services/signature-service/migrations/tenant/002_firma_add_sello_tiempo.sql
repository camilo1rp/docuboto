-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, ADR-016, RF-FIR-12, D-01): agrega sello_tiempo y tipo_firma.
--
-- sello_tiempo: timestamp del servidor utilizado en el HMAC de la firma_blob.
--   Necesario para verificar firmas creadas con el proveedor nativa-v2
--   (HMAC-SHA256 con secret del servicio). Las filas anteriores reciben
--   DEFAULT now() en migración — sus firma_blob (SHA-256 sin secret) ya no
--   verifican con el nuevo algoritmo, lo cual es aceptable dado que eran
--   reproducibles por cualquier tercero (D-01, insecuras).
--
-- tipo_firma: clasifica la firma ('electronica-nativa' por defecto).

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS sello_tiempo TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS tipo_firma   VARCHAR(30)  NOT NULL DEFAULT 'electronica-nativa';

COMMENT ON COLUMN firma.sello_tiempo IS
    'Timestamp del servidor incorporado en el HMAC (firma_blob). '
    'Requerido para re-verificar la firma. No debe ser provisto por el cliente.';

COMMENT ON COLUMN firma.tipo_firma IS
    'Tipo de firma: electronica-nativa | xades | pades. '
    'electronica-nativa = HMAC-SHA256 con secreto de servicio (RF-FIR-12).';
