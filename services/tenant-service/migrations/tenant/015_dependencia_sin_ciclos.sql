-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 015 (C-4 / C8, ADR-040). search_path = tenant_{slug}.
--
-- El organigrama es un ÁRBOL, y nada lo imponía: el router solo rechazaba
-- `parent_id == id`, así que A -> B -> A pasaba, y `/dependencias/tree` hacía
-- desaparecer en silencio los nodos del ciclo (ninguno es raíz). Ahora que el
-- cuadro de clasificación (fondo > sección > subsección) se deriva de él, un
-- ciclo convertiría un dato corrupto en un cuadro incompleto que parece completo.
--
--   1. La migración ABORTA si ya hay un ciclo, listando los códigos (sin backfill:
--      qué enlace romper es una decisión humana).
--   2. Trigger BEFORE INSERT OR UPDATE OF parent_id: toma un candado consultivo por
--      schema (sin él, A.parent=B y B.parent=A concurrentes pasan las dos
--      comprobaciones) y sube desde el nuevo padre; si alcanza la propia fila, rechaza
--      con `dependencia_ciclo` (check_violation, que el router mapea a 409).
--
-- Idempotente: OR REPLACE / DROP TRIGGER IF EXISTS.

-- 1. verificación previa ---------------------------------------------------------
DO $$
DECLARE
    en_ciclo text;
BEGIN
    WITH RECURSIVE sube AS (
        SELECT d.id AS origen, d.parent_id AS actual, 1 AS prof FROM dependencias d
        UNION ALL
        SELECT s.origen, p.parent_id, s.prof + 1
          FROM sube s JOIN dependencias p ON p.id = s.actual
         WHERE s.prof < 1000
    )
    SELECT string_agg(DISTINCT d.codigo::text, ', ')
      INTO en_ciclo
      FROM sube s JOIN dependencias d ON d.id = s.origen
     WHERE s.actual = s.origen;
    IF en_ciclo IS NOT NULL THEN
        RAISE EXCEPTION 'dependencias en ciclo (codigos: %) — rompa el enlace parent_id de alguna antes de migrar (C-4/C8)', en_ciclo;
    END IF;
END
$$;

-- 2. trigger anticiclos -------------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_dependencia_ciclo() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN
            RETURN NEW;
        END IF;
    END IF;

    -- Serializa los cambios de forma del árbol del schema: dos transacciones que
    -- cierran el ciclo cada una por su lado no se ven hasta confirmar.
    PERFORM pg_advisory_xact_lock(hashtext(current_schema() || ':dependencias_arbol'));

    IF NEW.parent_id = NEW.id THEN
        RAISE EXCEPTION 'dependencia_ciclo: una dependencia no puede ser su propio padre'
            USING ERRCODE = 'check_violation';
    END IF;

    IF EXISTS (
        WITH RECURSIVE arriba AS (
            SELECT d.id, d.parent_id FROM dependencias d WHERE d.id = NEW.parent_id
            UNION
            SELECT d.id, d.parent_id FROM dependencias d JOIN arriba a ON d.id = a.parent_id
        )
        SELECT 1 FROM arriba WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'dependencia_ciclo: el nuevo padre es descendiente de la dependencia (cerraria un ciclo en el organigrama)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_dependencias_sin_ciclos ON dependencias;
CREATE TRIGGER trg_dependencias_sin_ciclos
    BEFORE INSERT OR UPDATE OF parent_id ON dependencias
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_ciclo();

-- Rollback: DROP TRIGGER trg_dependencias_sin_ciclos ON dependencias;
--   DROP FUNCTION reject_dependencia_ciclo();
