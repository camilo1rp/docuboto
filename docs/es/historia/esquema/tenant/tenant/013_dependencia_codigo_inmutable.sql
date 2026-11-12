-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 013: la dependencia se correlaciona por CÓDIGO (B5, ADR-032).
-- search_path = tenant_{slug} (ADR-012).
--
--   1. `dependencias.codigo` pasa a ser INMUTABLE (trigger): es la llave con la
--      que 8+ tablas de 5 servicios correlacionan, todas sin FK.
--   2. `nombre` <= 100 caracteres (las instantáneas de flujo son VARCHAR(100)).
--   3. `dependencia_nombre_historico`: nombres anteriores (renombres) y mapeos
--      manuales decididos por un humano. Solo inserción.
--   4. `resolver_codigo_dependencia(nombre, historico)`: UNA definición del
--      criterio «nombre -> código» para los backfills, el alias deprecado de
--      las APIs y el replay de la cola de eventos fallidos.
--
-- Idempotente: CREATE ... IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS.
-- Rollback: DROP FUNCTION/TABLE/TRIGGER/CONSTRAINT; ningún dato se reescribe.

-- 1. codigo inmutable ---------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_dependencia_codigo_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.codigo IS DISTINCT FROM OLD.codigo THEN
        RAISE EXCEPTION 'dependencia_codigo_inmutable: el código de una dependencia no se puede cambiar (% -> %)',
            OLD.codigo, NEW.codigo
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_dependencias_codigo_inmutable ON dependencias;
CREATE TRIGGER trg_dependencias_codigo_inmutable
    BEFORE UPDATE OF codigo ON dependencias
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_change();

-- 2. nombre <= 100 --------------------------------------------------------------
DO $$
DECLARE
    largas text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
         WHERE n.nspname = current_schema() AND c.conname = 'ck_dependencias_nombre_len'
    ) THEN
        SELECT string_agg(format('%s (%s caracteres)', codigo, char_length(nombre)), ', ')
          INTO largas FROM dependencias WHERE char_length(nombre) > 100;
        IF largas IS NOT NULL THEN
            RAISE EXCEPTION 'dependencias con nombre > 100 caracteres: % — acórtelos antes de migrar', largas;
        END IF;
        ALTER TABLE dependencias
            ADD CONSTRAINT ck_dependencias_nombre_len CHECK (char_length(nombre) <= 100);
    END IF;
END
$$;

-- 3. historial de nombres / mapeos manuales (solo inserción) ---------------------
CREATE TABLE IF NOT EXISTS dependencia_nombre_historico (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo        INTEGER      NOT NULL,
    nombre        TEXT         NOT NULL,
    origen        VARCHAR(30)  NOT NULL CHECK (origen IN ('renombre', 'mapeo_manual')),
    motivo        TEXT,
    registrado_por UUID,
    at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dependencia_nombre_historico_nombre
    ON dependencia_nombre_historico (nombre);

CREATE OR REPLACE FUNCTION reject_dependencia_nombre_historico_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_nombre_historico es de solo inserción (% bloqueado)', TG_OP
        USING ERRCODE = 'check_violation';
END
$$;

DROP TRIGGER IF EXISTS trg_dependencia_nombre_historico_solo_insercion ON dependencia_nombre_historico;
CREATE TRIGGER trg_dependencia_nombre_historico_solo_insercion
    BEFORE UPDATE OR DELETE ON dependencia_nombre_historico
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_nombre_historico_mutation();

-- 4. resolución nombre -> código ---------------------------------------------------
-- p_historico = false: solo dependencias ACTIVAS con ese nombre actual (alias de
-- API). p_historico = true: activas, inactivas, nombres anteriores y mapeos
-- manuales (backfills y replay de la cola de fallidos). `candidatos` distingue
-- huérfano (0) de ambiguo (>=2); `codigo` es NULL salvo con exactamente 1.
CREATE OR REPLACE FUNCTION resolver_codigo_dependencia(p_nombre text, p_historico boolean)
RETURNS TABLE (codigo integer, candidatos integer)
LANGUAGE sql STABLE AS $$
    WITH c AS (
        SELECT d.codigo AS cod FROM dependencias d
         WHERE d.nombre = p_nombre AND (p_historico OR d.activo)
        UNION
        SELECT h.codigo AS cod FROM dependencia_nombre_historico h
         WHERE p_historico AND h.nombre = p_nombre
    )
    SELECT CASE WHEN count(*) = 1 THEN min(c.cod) END, count(*)::int FROM c
$$;
