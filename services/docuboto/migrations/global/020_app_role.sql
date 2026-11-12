-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- The application role. The services connect as `orpycamcp_app`, never as the
-- owner: a superuser skips the REVOKEs that keep the audit log append-only.
--
-- The role is born without LOGIN. A password written here would be published
-- with the repository; the operator enables it with a secret of their own:
--   ALTER ROLE orpycamcp_app LOGIN PASSWORD '<secret>';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        CREATE ROLE orpycamcp_app NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO orpycamcp_app', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO orpycamcp_app;

-- Default privileges go without `IN SCHEMA`: they cover whatever the owner
-- creates in any schema, present or future. That is what lets a new tenant, or
-- a table added by the next migration, be born with its grants. Every file after
-- this one relies on it and grants nothing by hand.
DO $$
BEGIN
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orpycamcp_app', current_user);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT USAGE, SELECT ON SEQUENCES TO orpycamcp_app', current_user);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT EXECUTE ON FUNCTIONS TO orpycamcp_app', current_user);
END $$;
