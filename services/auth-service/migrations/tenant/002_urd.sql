-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 002: URD multi-dependencia + historial de usuario (E08).
-- search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS urd_usuaroldep (           -- sgd_urd_usuaroldep (orfeo7)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    depe_id       INTEGER NOT NULL,                   -- código de dependencia (tenant-service)
    rol_id        UUID    NOT NULL REFERENCES auth_groups(id),
    es_principal  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, depe_id)                         -- RN-4: una URD por (usuario, depe)
);

-- Solo una dependencia principal por usuario.
CREATE UNIQUE INDEX IF NOT EXISTS ux_urd_principal
    ON urd_usuaroldep (user_id) WHERE es_principal = TRUE;

CREATE TABLE IF NOT EXISTS usua_historico (           -- usua_historico (Argoik/orfeo7)
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    executor_id   UUID,                               -- quién ejecuta (admin o el propio usuario)
    target_id     UUID,                               -- usuario afectado
    accion        VARCHAR(40) NOT NULL,               -- LOGIN, LOGOUT, BLOQUEO, CONTEXT_SWITCH, GROUP_ADD...
    ip            INET,
    navegador     TEXT,
    permisos_json JSONB,                              -- snapshot de permisos al evento
    ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_usua_historico_target ON usua_historico (target_id, ts DESC);
