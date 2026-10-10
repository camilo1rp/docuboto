-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B4, auditoría): matiza el mensaje del trigger append-only de
-- `webhook_entregas`. La tabla es TRAZA TÉCNICA de entregas salientes, no el registro de
-- comunicaciones oficiales (que es el radicado, Ley 594/2000): citar sus arts. 4 y 19
-- aquí sobre-declaraba lo que la tabla es. Solo cambia el texto de la excepción.
-- Nota: `url_sha256` pasa a ser la huella del ORIGEN; las filas ya escritas (append-only)
-- conservan la del URL completo y no pueden reescribirse.
-- Idempotente (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION reject_webhook_entregas_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'webhook_entregas is append-only (traza tecnica de entregas, inmutable)';
END;
$$ LANGUAGE plpgsql;
