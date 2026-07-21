-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración D-02: fail-closed en clasificación de chunks de conocimiento.
--
-- Añade la columna `nivel_seguridad` (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) como
-- columna de primera clase: NOT NULL, CHECK y DEFAULT 3 (CLASIFICADA).
--
-- El DEFAULT 3 es la decisión fail-closed: cualquier fila existente que NO tenga
-- clasificación explícita recibe el nivel máximo hasta que se corrija la fuente.
--
-- Backfill: si la fila tenía acl->>'nivel_seguridad' con valor 1, 2 o 3, se usa ese
-- valor; en caso contrario queda en 3 (CLASIFICADA).
--
-- Idempotente: el bloque DO protege contra doble ejecución.
--
-- BUGFIX (testing-engineer, cobertura E21 Inc.1): el check original NO filtraba
-- por `table_schema`, solo por `table_name`. `information_schema.columns` lista
-- las columnas de TODOS los schemas de la base, no solo del schema activo del
-- `search_path` — con multi-tenant (un `knowledge_chunk` por schema `tenant_*`,
-- mismo nombre de tabla en todos), en cuanto el PRIMER tenant tiene la columna,
-- el `IF NOT EXISTS` la encuentra ahí y la migración se SALTA en silencio para
-- CUALQUIER OTRO tenant que se provisione después — el `ALTER TABLE` nunca
-- corre en el schema nuevo, que se queda sin `nivel_seguridad` (columna que
-- sostiene el gate D-02/RF-SEG-08 fail-closed) hasta que algo intente leerla y
-- reviente con `UndefinedColumnError`. Se añade `AND table_schema =
-- current_schema()` para acotar el check al schema efectivamente activo (el
-- primero del `search_path` fijado por el runner/fixture antes de aplicar esta
-- migración).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name   = 'knowledge_chunk'
           AND column_name  = 'nivel_seguridad'
    ) THEN
        -- Paso 1: añadir con DEFAULT para que las filas existentes queden en 3.
        ALTER TABLE knowledge_chunk
            ADD COLUMN nivel_seguridad SMALLINT NOT NULL DEFAULT 3;

        -- Paso 2: backfill desde acl JSONB donde el valor es válido (1, 2 o 3).
        UPDATE knowledge_chunk
           SET nivel_seguridad = (acl->>'nivel_seguridad')::SMALLINT
         WHERE acl ? 'nivel_seguridad'
           AND acl->>'nivel_seguridad' ~ '^[123]$';

        -- Paso 3: restricción de rango; el DEFAULT permanece para filas nuevas.
        ALTER TABLE knowledge_chunk
            ADD CONSTRAINT ck_kc_nivel_seguridad CHECK (nivel_seguridad BETWEEN 1 AND 3);
    END IF;
END
$$;
