-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración 005: creación automática de particiones mensuales de audit_log
-- (ADR-008, punto 11.4 del plan de producción).
--
-- ── El problema ──────────────────────────────────────────────────────────────
-- `002_audit_log.sql` declara la tabla `PARTITION BY RANGE (ts)` y crea
-- `audit_log_default` «hasta que un job cree particiones mensuales». Ese job
-- nunca se escribió: TODA la auditoría del sistema —de todos los tenants, de los
-- once servicios, desde el primer día— vive en una sola partición por defecto.
--
-- No es solo una cuestión de rendimiento. Es una trampa que se cierra con el
-- tiempo: PostgreSQL **rechaza** crear una partición cuyo rango solape con filas
-- que ya están en la partición por defecto. Cuanto más se tarde en poner el job,
-- más histórico hay que mover antes de poder particionar — y mover filas de un
-- registro append-only con cadena de hash no es una operación que apetezca hacer
-- con prisa, de noche, porque las consultas ya no responden.
--
-- ── La corrección ────────────────────────────────────────────────────────────
-- `ensure_audit_partitions(n)` crea las particiones mensuales que falten hasta
-- `n` meses por delante y las endurece con `harden_audit_partition` (004), que
-- es lo que las hace append-only de verdad. Idempotente: se puede llamar en cada
-- despliegue y desde un cron mensual.
--
-- **No mueve el histórico** que ya está en el default, a propósito. Reescribir
-- filas de `audit_log` —aunque sea a otra partición— es tocar un registro con
-- valor probatorio cuya inmutabilidad defiende todo lo demás en este archivo, y
-- el beneficio (ordenar lo viejo) no compensa el riesgo. En su lugar, la función
-- arranca en el primer mes que NO solape con lo que ya hay guardado: el default
-- se congela con lo histórico y deja de crecer, que es el objetivo.

CREATE OR REPLACE FUNCTION public.ensure_audit_partitions(meses_adelante integer DEFAULT 3)
RETURNS TABLE (particion text, creada boolean)
LANGUAGE plpgsql AS $$
DECLARE
    _max_default timestamptz;
    _inicio      date;
    _mes         date;
    _fin         date;
    _nombre      text;
BEGIN
    IF meses_adelante < 0 THEN
        RAISE EXCEPTION 'ensure_audit_partitions: meses_adelante no puede ser negativo';
    END IF;

    -- Primer mes seguro: el siguiente al último asiento que quedó en el default.
    -- Sin esto, el CREATE TABLE ... FOR VALUES fallaría con «updated partition
    -- constraint for default partition would be violated by some row», que es un
    -- mensaje correcto y completamente opaco a las 3 de la mañana.
    SELECT max(ts) INTO _max_default FROM public.audit_log_default;

    _inicio := date_trunc('month', now())::date;
    IF _max_default IS NOT NULL THEN
        _inicio := greatest(_inicio, (date_trunc('month', _max_default) + interval '1 month')::date);
    END IF;

    _mes := _inicio;
    WHILE _mes <= (date_trunc('month', now()) + (meses_adelante || ' months')::interval)::date LOOP
        _fin := (_mes + interval '1 month')::date;
        _nombre := 'audit_log_' || to_char(_mes, 'YYYY_MM');

        IF EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = _nombre
        ) THEN
            particion := _nombre; creada := false; RETURN NEXT;
        ELSE
            EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.audit_log '
                'FOR VALUES FROM (%L) TO (%L)', _nombre, _mes, _fin
            );
            -- Sin esto la partición nueva sería la única parte de `audit_log`
            -- que SÍ admite UPDATE/DELETE/TRUNCATE: los guards de 003/004 se
            -- aplican por tabla, y una partición recién creada no los hereda.
            -- Crear particiones sin endurecerlas abriría, mes a mes, un agujero
            -- en la inmutabilidad del registro.
            PERFORM public.harden_audit_partition(_nombre);
            particion := _nombre; creada := true; RETURN NEXT;
        END IF;

        _mes := _fin;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION public.ensure_audit_partitions(integer) IS
    'Crea y endurece las particiones mensuales de public.audit_log que falten '
    'hasta N meses por delante (ADR-008). Idempotente. No mueve el histórico '
    'de audit_log_default: arranca en el primer mes que no solape con él. '
    'Invocación operativa: scripts/audit_partitions.py (cron mensual).';

-- Primera ejecución, aquí mismo: la migración no debe limitarse a dejar la
-- herramienta instalada. Si se instalara sin ejecutarse, el estado tras aplicar
-- esta migración seguiría siendo «todo al default» y nadie lo notaría hasta el
-- día en que alguien se acordase de llamarla.
SELECT public.ensure_audit_partitions(3);
