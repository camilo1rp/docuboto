-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- 008 — Ámbito de las causales (a qué acto pertenece cada una).
--
-- La 006 sembró seis causales cuyo acto estaba escrito DOS veces y declarado
-- ninguna: en el prefijo del código (`DEV-`/`ANU-`/`REA-`) y en el prefijo del
-- nombre («Devolución: …»). Una convención no es un contrato: ninguna pantalla
-- podía filtrar por acto sin adivinar, así que devolver y anular acabaron
-- pidiendo el código A MANO —con ejemplos («INCOMPLETO», «DUPLICADO») que ni
-- siquiera existen en el catálogo— y /envios llevando su propia lista fija en el
-- código del frontend. El catálogo se administraba y no lo leía nadie.
--
-- `general` es el valor por defecto a propósito: una causal nueva nace visible en
-- todos los selectores en vez de desaparecer de todos. En un catálogo que
-- administra la entidad, el olvido tiene que verse en pantalla.

DO $$
DECLARE
    nueva boolean;
BEGIN
    -- `current_schema()` y no un literal: esta migración corre una vez por
    -- tenant sobre su propio schema, y el nombre de columna se repite entre
    -- ellos — un guard sin cualificar daría por hecho el trabajo del primero.
    SELECT NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'causales'
          AND column_name = 'ambito'
    ) INTO nueva;

    IF nueva THEN
        ALTER TABLE causales ADD COLUMN ambito VARCHAR(20) NOT NULL DEFAULT 'general';
        -- Backfill SOLO al crear la columna: en una re-ejecución, un `ambito`
        -- que la entidad haya puesto a mano no se pisa con el prefijo.
        UPDATE causales SET ambito = CASE
            WHEN codigo LIKE 'DEV-%' THEN 'devolucion'
            WHEN codigo LIKE 'ANU-%' THEN 'anulacion'
            WHEN codigo LIKE 'REA-%' THEN 'reasignacion'
            ELSE 'general'
        END;
    END IF;
END $$;

ALTER TABLE causales DROP CONSTRAINT IF EXISTS causales_ambito_check;
ALTER TABLE causales ADD CONSTRAINT causales_ambito_check
    CHECK (ambito IN ('devolucion', 'anulacion', 'reasignacion', 'devolucion_postal', 'general'));

-- Causales de devolución POSTAL: vocabulario distinto del de la devolución
-- documental (aquélla habla del trámite, ésta de por qué el operador no pudo
-- entregar el sobre). Vivían escritas a mano en `envios/+page.svelte` bajo un
-- comentario que decía que el catálogo era «roadmap del backend» — existía desde
-- la 006, solo que sin un eje que las separara de las documentales.
INSERT INTO causales (codigo, nombre, ambito) VALUES
    ('POS-DIR', 'Postal: dirección incorrecta',      'devolucion_postal'),
    ('POS-DES', 'Postal: destinatario desconocido',  'devolucion_postal'),
    ('POS-REH', 'Postal: rehusado por el destinatario', 'devolucion_postal'),
    ('POS-NOR', 'Postal: no reclamado',              'devolucion_postal'),
    ('POS-OTR', 'Postal: otra causa',                'devolucion_postal')
ON CONFLICT (codigo) DO NOTHING;
