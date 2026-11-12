-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 053 (E12, AREA-07-04): el acta de recepción ata la FIRMA del índice.
-- search_path = tenant_{slug} (ADR-012).
--
-- `transferencia_acta.indice_version` (migración 022) pinneaba solo el NÚMERO de versión
-- del índice transferido. Una versión admite una sola firma hoy, pero un número no
-- identifica una firma: sin `indice_firma_id` el acta no dice CUÁL sello ampara el
-- índice que entregó, y un acta firmada que atestigua «versión 3» sin poder señalar
-- el sello es una referencia a medias. Se añaden el id de la firma y la huella del XML
-- que ese sello cubre (`expediente_indice.xml_sha256`).
--
-- Nullable A PROPÓSITO: las actas anteriores no lo tienen, y rellenarlas inventaría un
-- vínculo en un acta ya sellada (inmutable: trigger de la migración 023). Solo las
-- actas nuevas lo llevan; la respuesta de `GET /transferencias/{id}/acta` lo muestra
-- como `null` para las anteriores, sin ocultarlo.
--
-- Aditiva/idempotente: ADD COLUMN IF NOT EXISTS (un ADD COLUMN no dispara el trigger
-- de fila de la 023).

ALTER TABLE transferencia_acta
    ADD COLUMN IF NOT EXISTS indice_firma_id UUID;

ALTER TABLE transferencia_acta
    ADD COLUMN IF NOT EXISTS indice_sha256 TEXT;

COMMENT ON COLUMN transferencia_acta.indice_firma_id IS
    'expediente_indice.firma_id de la versión indice_version, leído en la MISMA tx de recepción que la fixity: el sello XAdES que ampara el índice entregado. NULL en las actas anteriores a la migración 053.';
COMMENT ON COLUMN transferencia_acta.indice_sha256 IS
    'expediente_indice.xml_sha256 de esa versión (huella C14N del XML que la firma cubre). NULL en las actas anteriores a la migración 053.';

-- Rollback manual (no automatizado):
-- ALTER TABLE transferencia_acta DROP COLUMN IF EXISTS indice_sha256;
-- ALTER TABLE transferencia_acta DROP COLUMN IF EXISTS indice_firma_id;
