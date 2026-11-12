-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 014 (B5, ronda de corrección, ADR-032). search_path = tenant_{slug}.
--
--   1. `resolver_codigo_dependencia`: con `p_historico`, un MAPEO MANUAL para ese
--      nombre ES la respuesta (la decisión humana gana a la coincidencia
--      textual). Antes el UNION lo mezclaba con las dependencias homónimas y el
--      mapeo no podía desambiguar nunca: el RAISE de la 019 de workflow no tenía
--      salida. Dos mapeos con códigos distintos siguen siendo ambiguos.
--   2. `SET search_path FROM CURRENT` en la función y en los triggers de la 013:
--      la resolución de `dependencias` no depende del search_path del llamador.
--   3. `dependencia_codigo_retirado` (solo inserción): el código de una
--      dependencia ELIMINADA no se reutiliza jamás; un trigger BEFORE INSERT
--      rechaza crear una dependencia con código retirado o con historial de
--      nombres huérfano. Cierra el hueco de «borrar y recrear el código con otra
--      identidad» que dejaba a los trámites viejos apuntando a otra unidad.
--   4. Siembra de `dependencia_nombre_historico` (origen 'renombre') desde los
--      asientos `dependencia.actualizada` de `public.audit_log` anteriores a B5
--      cuyo payload trae antes.nombre <> despues.nombre.
--
-- Idempotente: CREATE ... IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS,
-- siembra con NOT EXISTS. Rollback: DROP TABLE/TRIGGER/FUNCTION de esta migración.

-- 1 y 2. resolución y search_path -----------------------------------------------------
CREATE OR REPLACE FUNCTION resolver_codigo_dependencia(p_nombre text, p_historico boolean)
RETURNS TABLE (codigo integer, candidatos integer)
LANGUAGE sql STABLE
SET search_path FROM CURRENT
AS $$
    WITH mapeo AS (
        SELECT DISTINCT h.codigo AS cod FROM dependencia_nombre_historico h
         WHERE p_historico AND h.origen = 'mapeo_manual' AND h.nombre = p_nombre
    ),
    c AS (
        SELECT m.cod FROM mapeo m
        UNION
        SELECT d.codigo FROM dependencias d
         WHERE NOT EXISTS (SELECT 1 FROM mapeo)
           AND d.nombre = p_nombre AND (p_historico OR d.activo)
        UNION
        SELECT h.codigo FROM dependencia_nombre_historico h
         WHERE p_historico AND NOT EXISTS (SELECT 1 FROM mapeo)
           AND h.origen = 'renombre' AND h.nombre = p_nombre
    )
    SELECT CASE WHEN count(*) = 1 THEN min(c.cod) END, count(*)::int FROM c
$$;

ALTER FUNCTION reject_dependencia_codigo_change() SET search_path FROM CURRENT;
ALTER FUNCTION reject_dependencia_nombre_historico_mutation() SET search_path FROM CURRENT;

-- 3. códigos retirados -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dependencia_codigo_retirado (
    codigo      INTEGER     PRIMARY KEY,
    nombre      TEXT        NOT NULL,
    retirado_por UUID,
    retirado_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_dependencia_codigo_retirado_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_retirado es de solo inserción (% bloqueado)', TG_OP
        USING ERRCODE = 'check_violation';
END
$$;

DROP TRIGGER IF EXISTS trg_dependencia_codigo_retirado_solo_insercion ON dependencia_codigo_retirado;
CREATE TRIGGER trg_dependencia_codigo_retirado_solo_insercion
    BEFORE UPDATE OR DELETE ON dependencia_codigo_retirado
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_retirado_mutation();

-- Un código retirado no vuelve; tampoco uno con historial de nombres y sin
-- dependencia viva (se borró por fuera de la API). Si la dependencia con ese
-- código existe, el INSERT (p. ej. ON CONFLICT DO NOTHING de un seed) lo resuelve
-- la clave única y no es asunto de este trigger.
CREATE OR REPLACE FUNCTION reject_dependencia_codigo_reutilizado() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM dependencia_codigo_retirado r WHERE r.codigo = NEW.codigo)
       OR (EXISTS (SELECT 1 FROM dependencia_nombre_historico h WHERE h.codigo = NEW.codigo)
           AND NOT EXISTS (SELECT 1 FROM dependencias d WHERE d.codigo = NEW.codigo)) THEN
        RAISE EXCEPTION 'dependencia_codigo_retirado: el código % perteneció a una dependencia eliminada y no se reutiliza', NEW.codigo
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_dependencias_codigo_no_reutilizable ON dependencias;
CREATE TRIGGER trg_dependencias_codigo_no_reutilizable
    BEFORE INSERT ON dependencias
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_reutilizado();

-- 4. siembra del historial de nombres desde el audit_log ------------------------------------
-- Solo asientos con antes.nombre <> despues.nombre. El código sale del payload
-- (B5 en adelante) o de la dependencia citada en object_ref; sin ninguno de los
-- dos (dependencia ya eliminada y asiento antiguo) no se inventa nada.
INSERT INTO dependencia_nombre_historico (codigo, nombre, origen, motivo)
SELECT s.codigo, s.nombre, 'renombre',
       format('Sembrado desde audit_log (asiento %s, B5/014)', s.audit_id)
  FROM (
        SELECT DISTINCT ON (x.codigo, x.nombre) x.codigo, x.nombre, x.audit_id
          FROM (
                SELECT COALESCE(NULLIF(a.payload ->> 'codigo', '')::int, d.codigo) AS codigo,
                       a.payload #>> '{antes,nombre}' AS nombre,
                       a.id AS audit_id
                  FROM public.audit_log a
                  LEFT JOIN dependencias d ON d.id::text = a.object_ref
                 WHERE a.action = 'dependencia.actualizada'
                   AND a.tenant_slug = COALESCE(
                         (SELECT t.slug FROM public.tenants t
                           WHERE 'tenant_' || replace(t.slug, '-', '_') = current_schema()),
                         regexp_replace(current_schema(), '^tenant_', ''))
                   AND a.payload #>> '{antes,nombre}' IS NOT NULL
                   AND a.payload #>> '{despues,nombre}' IS NOT NULL
                   AND a.payload #>> '{antes,nombre}' <> a.payload #>> '{despues,nombre}'
               ) x
         WHERE x.codigo IS NOT NULL
         ORDER BY x.codigo, x.nombre, x.audit_id
       ) s
 WHERE NOT EXISTS (SELECT 1 FROM dependencia_nombre_historico h
                    WHERE h.codigo = s.codigo AND h.nombre = s.nombre AND h.origen = 'renombre');
