-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 001: RBAC de 4 tablas (E08).
-- Se aplica con search_path = tenant_{slug} (ADR-012); las tablas NO cualifican
-- schema. No se guarda tenant_slug: el schema ya identifica al tenant (ADR-002).

CREATE TABLE IF NOT EXISTS auth_users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username          VARCHAR(80)  NOT NULL UNIQUE,   -- USUA_LOGIN (UPPER)
    ldap_username     VARCHAR(80),                    -- USUA_LOGIN_LDAP (ICETEX separa)
    email             VARCHAR(255),
    keycloak_sub      VARCHAR(255) UNIQUE,            -- enlace Keycloak (lazy provisioning)
    is_root           BOOLEAN NOT NULL DEFAULT FALSE, -- USUA_PERM_ROOT: bypass total
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    failed_logins     SMALLINT NOT NULL DEFAULT 0,
    locked_until      TIMESTAMPTZ,
    password_migrated BOOLEAN NOT NULL DEFAULT TRUE,  -- RF-SEG-02 (flag preparado)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_groups (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(120) NOT NULL UNIQUE,        -- autg_grupos
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_permissions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre       VARCHAR(80) NOT NULL UNIQUE,         -- autp_permisos.nombre (PERM_RADI...)
    descripcion  TEXT
);

CREATE TABLE IF NOT EXISTS auth_memberships (         -- autm_membresias (M:N)
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   UUID NOT NULL REFERENCES auth_users(id)  ON DELETE CASCADE,
    group_id  UUID NOT NULL REFERENCES auth_groups(id) ON DELETE CASCADE,
    UNIQUE (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS auth_group_permissions (   -- autr_restric_grupo (M:N + CRUD)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id      UUID NOT NULL REFERENCES auth_groups(id)      ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES auth_permissions(id) ON DELETE CASCADE,
    crud          SMALLINT NOT NULL DEFAULT 0 CHECK (crud BETWEEN 0 AND 5),
    -- 0=Ninguno 1=Leer 2=Editar 3=Crear/Borrar 4=Nivel_1 5=Nivel_2
    UNIQUE (group_id, permission_id)
);

CREATE INDEX IF NOT EXISTS ix_auth_memberships_user ON auth_memberships (user_id);
CREATE INDEX IF NOT EXISTS ix_auth_group_permissions_group ON auth_group_permissions (group_id);
