# ADR-013: Autorización resuelta en la BD por request (Keycloak autentica, la BD autoriza)

**Estado**: Aceptado (decisión R4 de la Fase 1)
**Fecha**: 2026-06-17
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

Keycloak autentica al usuario y emite un JWT (firma RS256, validado contra JWKS por el
api-gateway, [ADR sobre Keycloak]). Pero **la autorización en Orfeo es por tenant y por
dependencia**: los permisos granulares (`PERM_RADI`, `USUA_PERM_EXPEDIENTE`, …) y la
dependencia activa del usuario viven en el schema `tenant_{slug}` (tablas `auth_*`, `urd_*`),
no en Keycloak. El RBAC ya está implementado (E08): `resolve_permissions(user_id)` calcula
`MAX(crud)` por permiso desde la BD del tenant.

Faltaba decidir **cómo llega esa autorización al punto de decisión** (el gate
`require_permission` de cada endpoint). Tres caminos posibles:

1. **Resolver en la BD por request** — el token de Keycloak solo identifica al usuario
   (`sub`) y su tenant; cada servicio resuelve permisos/dependencia desde la BD del tenant
   en cada petición.
2. **Firma propia** — auth-service valida el token de Keycloak y emite un JWT propio con
   `permissions{}`/`dept` embebidos; auth-service pasa a ser emisor de tokens.
3. **Token-exchange de Keycloak** — Keycloak inyecta los permisos vía protocol mappers /
   token-exchange.

## Decisión

**Se adopta la opción 1: la autorización se resuelve en la BD del tenant en cada request.
Keycloak sigue siendo el único emisor de tokens (solo autentica).**

- El api-gateway valida el JWT de Keycloak e inyecta hacia los servicios los headers de
  identidad ya confiables: `X-User-Id` (= `sub` de Keycloak), `X-Username`, `X-Tenant-Slug`,
  `X-User-Roles`.
- Cada servicio que necesite autorizar resuelve, dentro del schema del tenant:
  `keycloak_sub` → `auth_users.id` → `resolve_effective_permissions()` → comprobación de
  `has_permission(name, min_crud)` (ROOT primero).
- El gate vive en `app/core/authz.py` como dependencia `require_permission(name, min_crud)`
  reutilizable; comparte la conexión con `get_tenant_conn` (FastAPI cachea la dependencia
  por request).
- El cambio de dependencia activa (`/auth/context/switch`) **no reemite token**: cambia la
  fila de contexto en la BD; la siguiente petición resuelve sobre ella.

## Justificación

- **Coherencia con el principio ya fijado** ("Keycloak autentica, la BD autoriza", reflejo
  del legado plan-argoik/plan-icetex). La verdad de los permisos es la BD del tenant.
- **Sin tokens obsoletos**: un cambio de permiso o de dependencia surte efecto en la
  siguiente petición, sin esperar al refresh del token. Crítico para revocaciones (RN de
  seguridad) y para el flujo de `context/switch`.
- **Keycloak no modela permisos por tenant/dependencia**: mapearlos vía protocol mappers
  (opción 3) obligaría a duplicar en Keycloak datos que viven en la BD del tenant y a
  configurar token-exchange en el realm. Frágil y con doble fuente de verdad.
- **auth-service no se vuelve emisor de tokens** (opción 2): evita gestionar firma, rotación
  de claves y expiración propias, y la obsolescencia de permisos embebidos.

## Consecuencias

**Positivas**
- Autorización siempre fresca; revocación inmediata.
- Punto de decisión único y testeable (`require_permission`), replicable en todos los servicios.
- El token de Keycloak queda mínimo y estable.

**Negativas / mitigaciones**
- **Una consulta de permisos por request.** Mitigación: la resolución es una sola query
  indexada (`auth_memberships` ⨝ `auth_group_permissions` ⨝ `auth_permissions`); es cacheable
  por (tenant, user) con TTL corto e invalidación en cambios de RBAC si el perfil de carga lo
  exige. No se cachea en v1 (correctitud primero).
- **Lazy-provisioning**: un usuario válido en Keycloak puede no existir aún en `auth_users`
  del tenant. **Decidido y vigente**: `/validate` lo autocrea en el primer acceso
  (`RBACRepository.upsert_user_by_keycloak_sub`); un usuario recién aprovisionado no tiene
  grupos, así que sigue sin permisos → 403 hasta que se le asigne uno. En los demás servicios,
  si la fila no existe, el gate falla cerrado.
- Cada servicio que autorice necesita acceso al schema del tenant (`get_tenant_conn`) y a la
  resolución de permisos. **Generalizado (AREA-01-17)**: `check_permission`,
  `resolve_caller_clearance`, `nivel_efectivo` y `mensaje_denegacion` viven UNA vez en
  `orpycamcp_common.rbac` ([ADR-010](ADR-010-libreria-compartida.md)); cada servicio las importa
  en su `core/clearance.py` (o `authz.py`) y conserva solo `require_permission`, que depende
  de su `get_tenant_conn`. `shared/tests/test_el_gate_se_define_una_vez.py` falla si reaparece
  una definición fuera de la librería. Una caché por (tenant, usuario) sigue fuera mientras no
  haya un perfil de carga que la exija.

## Alternativas descartadas

- **Firma propia (opción 2)**: convierte a auth-service en IdP secundario; permisos embebidos
  → obsolescencia hasta el refresh; `context/switch` obliga a reemitir.
- **Token-exchange Keycloak (opción 3)**: doble fuente de verdad de permisos; configuración de
  realm pesada; no encaja con permisos por dependencia.

## Referencias

- [ADR-008](ADR-008-auditoria-inmutable.md) — auditoría inmutable (toda decisión sensible se audita).
- [ADR-010](ADR-010-libreria-compartida.md) — librería compartida (futura generalización del gate).
- [ADR-012](ADR-012-migraciones-por-tenant.md) — las tablas `auth_*` viven en `tenant_{slug}`.
