-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 054 (E12, AREA-07-08): capa de PRIVILEGIOS sobre el acta de entrega.
-- search_path = tenant_{slug} (ADR-012).
--
-- La inmutabilidad de `transferencia_acta` (migración 023) descansaba SOLO en un trigger
-- (UPDATE/DELETE/TRUNCATE). Desde que el rol de la aplicación dejó de ser superusuario y
-- dueño (auth-service 008_rol_app_privilegios) no puede desactivar ese trigger, pero la
-- 008 le concede UPDATE y DELETE sobre TODAS las tablas del tenant, el acta incluida: la
-- única barrera era el trigger. Defensa en profundidad, la misma que ya tiene `audit_log`:
-- el rol de la app NO recibe UPDATE ni DELETE sobre el acta ni sobre las firmas
-- personales del acta (append-only, migración 027), de modo que la mutación falla por
-- privilegios ANTES de llegar al trigger.
--
-- Mismo guard de existencia de rol que la 008: sin `orpycamcp_app` (entornos donde la
-- aplicación se conecta como propietario, como la pila de desarrollo) no hay a quién
-- revocar y la migración no hace nada. Los privilegios por omisión de la 008 conceden
-- sobre las tablas NUEVAS; esto retira lo concedido sobre estas dos YA existentes.
--
-- Idempotente: REVOKE sobre un privilegio ausente no falla.

DO $$
DECLARE
    esquema TEXT := current_schema();
    tabla   TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        RAISE NOTICE 'orpycamcp_app no existe; nada que revocar sobre el acta.';
        RETURN;
    END IF;

    FOREACH tabla IN ARRAY ARRAY['transferencia_acta', 'transferencia_acta_firma_personal']
    LOOP
        IF to_regclass(format('%I.%I', esquema, tabla)) IS NOT NULL THEN
            EXECUTE format('REVOKE UPDATE, DELETE ON TABLE %I.%I FROM orpycamcp_app', esquema, tabla);
        END IF;
    END LOOP;
END;
$$;

-- Rollback manual (no automatizado):
-- GRANT UPDATE, DELETE ON TABLE transferencia_acta, transferencia_acta_firma_personal TO orpycamcp_app;
