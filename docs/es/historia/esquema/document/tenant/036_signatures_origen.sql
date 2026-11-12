-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- AREA-05-03-S: las firmas declarativas anteriores llevan una huella que ELIGIÓ
-- EL CLIENTE; `verify` no puede seguir dándolas por válidas.
--
-- Las filas que ya existen quedan marcadas `cliente_no_verificado` (el DEFAULT
-- con que se añade la columna las cubre todas) y `verify` las excluye. Después
-- el DEFAULT pasa a `servidor`: las nuevas las inserta el servicio con la huella
-- resuelta en storage. Idempotente: repetir la migración no toca lo ya marcado.

ALTER TABLE signatures
    ADD COLUMN IF NOT EXISTS origen VARCHAR(30) NOT NULL DEFAULT 'cliente_no_verificado';

ALTER TABLE signatures ALTER COLUMN origen SET DEFAULT 'servidor';

ALTER TABLE signatures DROP CONSTRAINT IF EXISTS ck_signatures_origen;
ALTER TABLE signatures
    ADD CONSTRAINT ck_signatures_origen CHECK (origen IN ('servidor', 'cliente_no_verificado'));

COMMENT ON COLUMN signatures.origen IS
    'servidor = huella resuelta por el servidor desde storage; '
    'cliente_no_verificado = huella declarada por el cliente (heredada), '
    'excluida de verify.';
