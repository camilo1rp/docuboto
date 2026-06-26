-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 004: audit_log mutation guard (D-03 defense-in-depth) + harden_audit_partition helper
--
-- Cierra las dos brechas identificadas por los auditores en D-03 (intento 2):
--
--   FIX 1 — Trigger BEFORE UPDATE OR DELETE (FOR EACH STATEMENT) sobre la tabla padre
--   y la partición por defecto como capa adicional de defensa en profundidad.
--
--   Defensa en capas para UPDATE/DELETE tras esta migración:
--     • Capa 1 (privilegios): REVOKE UPDATE,DELETE de PUBLIC (003) — superusuarios la omiten.
--     • Capa 2 (fila, 002):   BEFORE UPDATE OR DELETE FOR EACH ROW en la tabla padre;
--                              PostgreSQL propaga automáticamente este trigger de fila a
--                              todas las particiones presentes y futuras (PG >= 11).
--     • Capa 3 (sentencia, 004, esta migración):
--                              BEFORE UPDATE OR DELETE FOR EACH STATEMENT en la tabla padre
--                              y en cada partición individualmente.
--                              Los triggers de sentencia NO se propagan automáticamente en
--                              tablas particionadas: el trigger del padre se dispara cuando
--                              el DML apunta a la raíz; el trigger de una partición se
--                              dispara cuando el DML apunta directamente a esa partición.
--                              Por eso se instalan por separado.
--
--   FIX 2 — Función helper harden_audit_partition(partition_name text) que aplica el
--   conjunto completo de protecciones (REVOKE + anti-TRUNCATE + anti-mutation) sobre
--   una partición mensual nueva, de modo idempotente.
--
--   OPERATIVO: el job de creación de particiones mensuales (ADR-008) DEBE llamar a
--   harden_audit_partition('audit_log_YYYY_MM') para cada nueva partición antes de
--   que entre en servicio.
--
-- Idempotente: CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS + CREATE TRIGGER.

-- ---------------------------------------------------------------------------
-- FIX 1A: función de rechazo de sentencia (sin acceso a NEW/OLD; sólo RAISE)
--         Gemelo de reject_audit_log_truncate() de 003, citando D-03 explícitamente.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.reject_audit_log_mutation_d03()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'UPDATE/DELETE on public.audit_log is prohibited '
        '(ADR-008, Ley 594/2000, D-03): table is append-only';
END;
$$;

-- ---------------------------------------------------------------------------
-- FIX 1B: trigger BEFORE UPDATE OR DELETE (FOR EACH STATEMENT) en la tabla padre
--
-- Cuando el DML apunta a public.audit_log (la raíz particionada), este trigger
-- se dispara UNA VEZ por sentencia ANTES de que PostgreSQL enrute la operación
-- a las particiones. La excepción cancela toda la sentencia.
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_audit_log_no_mutation ON public.audit_log;
CREATE TRIGGER trg_audit_log_no_mutation
    BEFORE UPDATE OR DELETE ON public.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();

-- ---------------------------------------------------------------------------
-- FIX 1C: trigger BEFORE UPDATE OR DELETE (FOR EACH STATEMENT) en la partición
--         por defecto.
--
-- Necesario porque los triggers de sentencia NO se propagan desde la raíz a las
-- particiones individuales (a diferencia de los triggers de fila de 002).
-- Protege el acceso directo: UPDATE public.audit_log_default SET ...
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_audit_log_default_no_mutation ON public.audit_log_default;
CREATE TRIGGER trg_audit_log_default_no_mutation
    BEFORE UPDATE OR DELETE ON public.audit_log_default
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();

-- ---------------------------------------------------------------------------
-- FIX 2: helper harden_audit_partition(partition_name text)
--
-- Aplica el conjunto completo de protecciones D-03 sobre una partición mensual:
--   1. REVOKE UPDATE, DELETE, TRUNCATE FROM PUBLIC
--   2. Trigger anti-TRUNCATE  (reutiliza reject_audit_log_truncate() de 003)
--   3. Trigger anti-UPDATE/DELETE (reutiliza reject_audit_log_mutation_d03())
--
-- Nombre esperado de las particiones mensuales: audit_log_YYYY_MM
-- Ejemplo de uso (en el job de particionado, ADR-008):
--
--   CREATE TABLE public.audit_log_2025_01
--       PARTITION OF public.audit_log
--       FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
--
--   SELECT public.harden_audit_partition('audit_log_2025_01');
--
-- Idempotente: puede llamarse varias veces sobre la misma partición sin error.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.harden_audit_partition(partition_name text)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    _trig_trunc text;
    _trig_mut   text;
BEGIN
    -- Valida el nombre: sólo particiones mensuales de audit_log son aceptables.
    -- Esto previene inyección SQL mediante el argumento y restringe el uso accidental.
    IF partition_name !~ '^audit_log_[0-9]{4}_[0-9]{2}$' THEN
        RAISE EXCEPTION
            'harden_audit_partition: nombre de partición inválido: %. '
            'Se esperaba: audit_log_YYYY_MM (D-03)',
            partition_name;
    END IF;

    _trig_trunc := 'trg_' || partition_name || '_no_truncate';
    _trig_mut   := 'trg_' || partition_name || '_no_mutation';

    -- 1. Revoca operaciones destructivas para todos los roles no-superusuario.
    EXECUTE format(
        'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM PUBLIC',
        partition_name
    );

    -- 2. Trigger anti-TRUNCATE (no se hereda desde la raíz; debe instalarse por partición).
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_trunc, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I '
        'BEFORE TRUNCATE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate()',
        _trig_trunc, partition_name
    );

    -- 3. Trigger anti-UPDATE/DELETE (sentencia).
    --    El trigger de fila de 002 ya protege las filas individuales (es heredado).
    --    Esta capa de sentencia bloquea la operación antes del enrutamiento de filas,
    --    alineada con el patrón D-03 de esta migración (FIX 1).
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_mut, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I '
        'BEFORE UPDATE OR DELETE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03()',
        _trig_mut, partition_name
    );

    RAISE NOTICE
        'Partición % endurecida (D-03): REVOKE + anti-TRUNCATE + anti-mutation triggers instalados.',
        partition_name;
END;
$$;
