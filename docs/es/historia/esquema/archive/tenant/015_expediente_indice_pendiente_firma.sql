-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06 Inc.1, XAdES-B + ADR-016 / RT-16): nuevo estado
-- 'pendiente_firma' del índice electrónico.
--
-- Decisión del usuario: si el sellado XAdES-B del índice falla al cierre del
-- expediente (sello del tenant no aprovisionado, signature-service
-- inalcanzable, etc.), el cierre del expediente NO se bloquea — el índice
-- queda en 'pendiente_firma' (en vez de 'vigente' silencioso, que ocultaba el
-- fallo) y puede reintentarse vía POST /expedientes/{id}/indice/firmar
-- (idempotente). Ensancha el CHECK de `expediente_indice.estado` definido en
-- 008_expediente_indice.sql ('vigente','firmado') sin tocar filas existentes.

DO $$
DECLARE
    old_name text;
BEGIN
    -- Localiza el nombre real del CHECK sobre `estado` (auto-generado por
    -- Postgres en 008 como `expediente_indice_estado_check`, pero se busca por
    -- definición en vez de asumir el nombre para que la migración sea robusta
    -- ante una ejecución previa de sí misma).
    SELECT con.conname INTO old_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'expediente_indice'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%estado%'
    LIMIT 1;

    IF old_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE expediente_indice DROP CONSTRAINT %I', old_name);
    END IF;

    ALTER TABLE expediente_indice
        ADD CONSTRAINT expediente_indice_estado_check
        CHECK (estado IN ('vigente', 'pendiente_firma', 'firmado'));
END $$;
