-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 045 (B2, ADR-031): un acuse de recibo es de UN solo envío.
-- search_path = tenant_{slug} (ADR-012).
--
-- El servicio ya rechaza (409 acuse_ya_referenciado) un `acuse_file_id` que otro
-- envío, un anexo o un borrador usan; esta es la guarda de MOTOR para la carrera
-- entre dos registros simultáneos que pasan el cotejo a la vez.
--
-- Si ya hay un mismo acuse en dos envíos (datos previos a esta guarda) NO se
-- escoge cuál conservar —eso es juzgar una prueba—: la migración aborta y dice
-- cuáles son. Se corrigen a mano y se vuelve a aplicar.
--
-- Idempotente. Rollback: DROP INDEX postal_shipment_acuse_file_id_uq.
DO $$
DECLARE
    duplicados TEXT;
BEGIN
    SELECT string_agg(acuse_file_id::text || ' (' || n || ' envíos)', ', ')
      INTO duplicados
      FROM (
          SELECT acuse_file_id, count(*) AS n
            FROM postal_shipment
           WHERE acuse_file_id IS NOT NULL
           GROUP BY acuse_file_id
          HAVING count(*) > 1
      ) d;
    IF duplicados IS NOT NULL THEN
        RAISE EXCEPTION
            'No se puede crear el índice único de acuse: el mismo archivo es acuse de varios envíos: %',
            duplicados;
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS postal_shipment_acuse_file_id_uq
    ON postal_shipment (acuse_file_id)
    WHERE acuse_file_id IS NOT NULL;
