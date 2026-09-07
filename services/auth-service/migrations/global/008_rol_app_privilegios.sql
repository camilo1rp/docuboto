-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Software libre: se distribuye bajo la Licencia Pública General Affero de GNU
-- (AGPL) versión 3 o posterior, conservando los términos de la GPL del Orfeo
-- original del que deriva. SIN GARANTÍA ALGUNA. Véase LICENSE.

-- ===========================================================================
-- Privilegios del rol de aplicación: separar QUIEN OPERA de QUIEN ESTRUCTURA
-- ===========================================================================
--
-- Hasta aquí todos los servicios conectaban con el rol de arranque, que Postgres
-- crea SUPERUSUARIO. Con ese rol, las tres capas que protegen `audit_log` no
-- protegen nada: la capa de privilegios la omite el superusuario por definición,
-- y así lo dice la propia migración 003. Un guardián de solo-anexar que descansa
-- sobre permisos que nadie retira es una declaración, no un control.
--
-- El reparto queda así:
--
--   • Rol PROPIETARIO (el de arranque): aplica migraciones, provisiona entidades
--     y crea particiones. Es quien ESTRUCTURA. Los servicios ya no lo usan; sus
--     runners leen `MIGRATIONS_DATABASE_URL`.
--
--   • Rol de APLICACIÓN (`orpycamcp_app`): lee y escribe filas. Es quien OPERA.
--     No puede crear ni alterar objetos, y sobre `audit_log` solo puede AÑADIR.
--
--   • Excepción declarada: `tenant-service` crea esquemas al dar de alta una
--     entidad, así que sigue conectando como propietario. Es un solo servicio,
--     sellado con token interno y no enrutado por el gateway.
--
-- Se comprobó midiendo, no estimando: el carril de integración de
-- document-service pasa 99 de 103 con este rol; los 4 restantes fallaban por
-- privilegios que necesita el PROPIETARIO (crear esquema, declarar una clave
-- foránea contra un catálogo de `public`), no la aplicación.

DO $$
DECLARE
    propietario TEXT := current_user;
    esquema     TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        RAISE NOTICE 'orpycamcp_app no existe; nada que conceder.';
        RETURN;
    END IF;

    EXECUTE format('GRANT CONNECT ON DATABASE %I TO orpycamcp_app', current_database());

    -- Esquemas que ya existen: `public` y los de cada entidad provisionada.
    FOR esquema IN
        SELECT nspname FROM pg_namespace
         WHERE nspname = 'public' OR nspname LIKE 'tenant\_%'
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO orpycamcp_app', esquema);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO orpycamcp_app',
            esquema);
        EXECUTE format(
            'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO orpycamcp_app', esquema);
        EXECUTE format(
            'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO orpycamcp_app', esquema);
    END LOOP;

    -- ── Lo que aún no existe ────────────────────────────────────────────────
    --
    -- Conceder sobre lo existente NO basta, y es el error que más caro sale:
    -- la entidad que se da de alta mañana, y la tabla que añada la próxima
    -- migración, nacerían sin permisos y el fallo aparecería al primer uso en
    -- producción, no al desplegar. Se comprobó ejecutándolo: con los permisos
    -- concedidos sobre las 106 tablas existentes, todo seguía fallando en cuanto
    -- se creaba un esquema nuevo.
    --
    -- Los privilegios por omisión van SIN `IN SCHEMA`, o sea globales para todo
    -- lo que cree el propietario en cualquier esquema, presente o futuro. Es lo
    -- que hace que dar de alta una entidad no requiera acordarse de nada.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orpycamcp_app', propietario);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT USAGE, SELECT ON SEQUENCES TO orpycamcp_app', propietario);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT EXECUTE ON FUNCTIONS TO orpycamcp_app', propietario);

    RAISE NOTICE 'orpycamcp_app: privilegios de operación concedidos (propietario: %).',
        propietario;
END;
$$;

-- ---------------------------------------------------------------------------
-- La excepción: sobre la auditoría solo se AÑADE
-- ---------------------------------------------------------------------------
--
-- Los privilegios por omisión de arriba conceden UPDATE y DELETE sobre toda
-- tabla nueva, lo cual es correcto para las tablas de dominio y sería la ruina
-- para `audit_log`: cada partición mensual nueva nacería mutable. La excepción
-- se retira aquí para lo que ya existe, y se añade a `harden_audit_partition`
-- para lo que venga — que es donde ya vive el endurecimiento de cada partición,
-- en vez de en un segundo sitio que alguien tendría que recordar.
DO $$
DECLARE
    parte TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        RETURN;
    END IF;
    -- La tabla se comprueba además del rol: `REVOKE` sobre una relación
    -- inexistente aborta la migración entera, y «aún no existe» es un estado
    -- alcanzable en una base donde las migraciones previas no llegaron. Se
    -- detectó probándolo contra Postgres real, no leyendo el fichero.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = 'audit_log'
    ) THEN
        RAISE NOTICE 'public.audit_log no existe todavía; nada que revocar.';
        RETURN;
    END IF;
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log FROM orpycamcp_app;
    FOR parte IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_inherits i ON i.inhrelid = c.oid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'audit_log'
    LOOP
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM orpycamcp_app', parte);
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------
-- Y para las particiones que vengan
-- ---------------------------------------------------------------------------
--
-- `harden_audit_partition` retiraba UPDATE/DELETE/TRUNCATE de PUBLIC. Eso no
-- alcanza a `orpycamcp_app`, que los recibe por concesión DIRECTA vía los
-- privilegios por omisión: revocar de PUBLIC no toca lo concedido a un rol
-- nombrado. Sin esta línea, cada partición mensual nueva nacería mutable para
-- la aplicación y el guardián volvería a ser una declaración.
--
-- Se añade aquí, dentro de la función que el job ya llama, y no como un paso
-- más en el job: un endurecimiento que depende de que alguien recuerde
-- invocarlo es el que se olvida.
CREATE OR REPLACE FUNCTION public.harden_audit_partition(partition_name text)
RETURNS void
LANGUAGE plpgsql
AS $fn$
DECLARE
    _trig_trunc TEXT;
    _trig_mut   TEXT;
BEGIN
    IF partition_name !~ '^audit_log[a-z0-9_]*$' THEN
        RAISE EXCEPTION
            'harden_audit_partition: nombre de partición inválido: %. '
            'Debe empezar por audit_log y contener solo [a-z0-9_].', partition_name;
    END IF;

    EXECUTE format(
        'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM PUBLIC', partition_name);

    -- El rol de aplicación recibe UPDATE/DELETE por los privilegios por omisión
    -- (migración 008). Sobre la auditoría se retiran: solo se AÑADE.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM orpycamcp_app',
            partition_name);
    END IF;

    _trig_trunc := 'trg_' || partition_name || '_no_truncate';
    _trig_mut   := 'trg_' || partition_name || '_no_mutation';

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_trunc, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03()',
        _trig_trunc, partition_name);

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_mut, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03()',
        _trig_mut, partition_name);
END;
$fn$;
