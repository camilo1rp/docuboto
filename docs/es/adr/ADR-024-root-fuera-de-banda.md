# ADR-024: `USUA_PERM_ROOT` no se concede desde la API — el administrador de entidad no es el techo de su entidad

**Estado**: Aceptado
**Fecha**: 2026-07-31
**Autores**: Auditoría de seguridad + fastapi-developer

---

## Contexto

Una auditoría de seguridad sobre `services/auth-service` encontró un hallazgo **Alto**:
un administrador de entidad (`USUA_PERM_ADMIN`, el rol operativo más alto de un tenant)
podía auto-escalarse a `USUA_PERM_ROOT` (bypass total, ver `has_permission` en
`app/services/rbac_service.py`) por dos vías:

1. `POST /auth/users` con `is_root: true` — `RBACService.create_user` insertaba la fila
   sin comprobar quién hacía la petición.
2. `PUT /auth/groups/{gid}/permissions/{pid}` concediendo `USUA_PERM_ROOT` a un grupo —
   incluyendo un grupo del que el propio actor es miembro. `USUA_PERM_ROOT` es una fila
   normal de `auth_permissions` (seed `migrations/tenant/004_permissions_seed.sql`), y
   `GET /auth/permissions` la devolvía sin filtrar, así que aparecía en el desplegable
   del panel de administración como cualquier otro permiso concedible.

Además, esa segunda vía es una escalada de [RF-SEG-08](ADR-013-autorizacion-resuelta-en-bd.md)
por otro eje: `PUT /auth/groups/{gid}/clearance` no comprobaba que el `max_level` pedido
no excediera el clearance efectivo de quien lo pide — un admin con clearance RESERVADA
podía subirse a sí mismo (vía el grupo del que es miembro) a CLASIFICADA.

Al diseñar el cierre apareció una restricción de arranque que no es opcional: verificado
contra el código, **no existe ninguna vía de crear el primer ROOT de un tenant fuera de
la API**. `scripts/init_tenant.py` (tenant-service) no crea usuario alguno, y
`rbac_repository.upsert_user_by_keycloak_sub` es explícito: *"Never flips is_root — admin
must set it manually"*. Cerrar las dos vías sin más deja un punto muerto: ningún tenant
podría tener jamás su primer ROOT.

## Decisión

**`USUA_PERM_ROOT` deja de ser concedible desde la API bajo cualquier circunstancia,
incluso por otro ROOT vía las dos vías anteriores salvo que el llamante YA sea ROOT.
La única forma de que un tenant obtenga su primer (o siguiente) ROOT es un script de
operación (`app/ops/grant_root.py`) ejecutado por quien tiene acceso directo a la base de
datos — nunca por HTTP, nunca expuesto por el api-gateway.**

Concretamente:

1. `RBACService.create_user`: rechaza `is_root=true` con 403 salvo que el `caller` ya sea
   ROOT.
2. `RBACService.set_group_permission`: rechaza conceder (`crud > 0`) `USUA_PERM_ROOT` a
   un grupo con 403 salvo que el `caller` ya sea ROOT. Revocar (`crud = 0`) sigue permitido
   sin restricción — solo se bloquea la concesión.
3. `GET /auth/permissions` omite `USUA_PERM_ROOT` del catálogo para un llamante no-ROOT
   (no ofrecer en un selector de UI un permiso que el propio backend va a rechazar).
4. `PUT /auth/groups/{gid}/clearance`: no-write-up — rechaza con 403 un `max_level` por
   encima del clearance efectivo del `caller`, salvo que sea ROOT. Mismo patrón que el
   no-write-up de clasificación de `archive_service.create_expediente` /
   `document_service.reclassify` (RF-SEG-08): 403 explícito, chequeo antes de mutar.
5. `app/ops/grant_root.py` (nuevo): concede `is_root=true` a un usuario existente de UN
   tenant explícito (nunca "todos"), directamente contra la BD. Idempotente, audita en
   `public.audit_log` (`rbac.root_otorgado_fuera_de_banda`, actor
   `system:ops-grant-root`), y aborta si la auditoría no está disponible. Sigue el mismo
   patrón que `app/ops/purge_revoked_signing_keys.py` / `app/ops/rewrap_signing_keys.py`
   — el precedente ya establecido de operaciones sensibles fuera de banda.

**El administrador de entidad ya no es el techo de su propia entidad.** Un
`USUA_PERM_ADMIN` sigue pudiendo administrar todo lo demás (usuarios no-ROOT, grupos,
permisos no-ROOT, clearance hasta su propio nivel), pero ni él ni ningún otro
`USUA_PERM_ADMIN` puede crear un par de sí mismo con más privilegio que él, y no puede
elevar el clearance de un grupo por encima del suyo. Solo ROOT concede ROOT; solo un
clearance igual o superior concede ese clearance.

## Justificación

- **Mínimo privilegio real, no nominal**: `USUA_PERM_ADMIN` documentaba ser el rol
  administrativo del tenant, pero al poder auto-concederse `USUA_PERM_ROOT` (bypass total
  de TODO gate RBAC del tenant, incluyendo los de otros servicios que confían en
  `X-User-Id`/permisos resueltos aquí) era, de facto, indistinguible de ROOT. El hallazgo
  no es solo "un admin puede hacer cosas de más" — es que la distinción ROOT/ADMIN no
  existía en la práctica.
- **Consistencia con RF-SEG-08**: el proyecto ya aplica no-write-up en clasificación de
  radicados/expedientes (nadie origina contenido por encima de su propia habilitación).
  Permitir que un admin subiera el clearance de un grupo por encima del suyo era la misma
  vulnerabilidad en el eje de permisos administrativos en vez del eje documental.
- **Separación de canales**: un privilegio que, si se abusa, compromete TODO el tenant
  (ROOT) no debe ser alcanzable por el mismo canal (HTTP, tras el gateway) que un admin ya
  controla. Requerir acceso directo a la base de datos para conceder ROOT impone una
  barrera operacional distinta — el mismo principio que ya rige la purga/rotación de
  material de firma (`app/ops/*`).
- **Fail-closed sobre fail-open en el catálogo de permisos**: excluir `USUA_PERM_ROOT` de
  `GET /auth/permissions` para no-ROOT no es solo estética — ofrecer en un desplegable una
  opción que el backend va a rechazar es prometer una operación que falla, y una API que
  promete cosas que no cumple invita a que el cliente (o un atacante) intente rodear el
  gate por otra vía no auditada.

## Consecuencias

**Positivas**
- Cierra el hallazgo Alto: auto-escalada a ROOT y a clearance superior al propio ya no son
  posibles desde ningún endpoint HTTP.
- El primer ROOT de un tenant queda con una vía de arranque clara, auditada e idempotente.
- El patrón de "operación sensible = script de ops fuera de banda, nunca endpoint" queda
  reforzado con un tercer precedente (junto a purga y rotación de claves de firma).

**Negativas / mitigaciones**
- **Fricción operativa**: aprovisionar el primer ROOT de un tenant nuevo exige acceso a la
  base de datos (o a quien lo tenga), no autoservicio desde el panel de administración.
  Aceptado deliberadamente — es la contrapartida directa de cerrar la auto-escalada; un
  ROOT autoservicio y una auto-escalada cerrada son mutuamente excluyentes.
- **El frontend** ofrece hoy una casilla "Otorgar ROOT" en el formulario de creación de
  usuario que, para un admin no-ROOT, ahora siempre falla con 403. Pendiente: el frontend
  debe ocultar esa casilla (y la opción `USUA_PERM_ROOT` del selector de permisos de grupo)
  cuando el usuario en sesión no sea ROOT — `GET /auth/permissions` ya no la lista para
  ese caso, así que el selector de permisos se resuelve solo; la casilla de `is_root` en
  el formulario de usuario necesita un chequeo explícito de `is_root` del propio caller
  (expuesto en `GET /auth/me` o `EffectivePermissions`).
- `app/ops/grant_root.py` tiene test de integración contra Postgres real
  (`tests/integration/test_grant_root_integration.py`, AREA-13-07): concesión con asiento
  único, idempotencia, dry-run sin escritura, rollback conjunto si el asiento falla y
  validación del slug. Igual que sus scripts hermanos (purga y rotación de KEK), que
  también lo tienen.

## Alternativas descartadas

- **Permitir que ROOT se auto-conceda vía la API pero bloquear solo a no-ROOT** (en vez de
  bloquear la concesión de ROOT incondicionalmente salvo llamante-ROOT): descartado por
  simplicidad de razonamiento — la regla "solo ROOT concede ROOT" es más fácil de auditar
  que una excepción condicional, y de todas formas un ROOT existente puede usar el script
  de ops si prefiere no dejar rastro en la sesión HTTP.
- **Voltear `is_root` con una migración `.sql` de datos** para el primer ROOT: descartado
  porque una migración numerada es un artefacto versionado y compartido — no debe llevar
  datos específicos de un tenant/entorno (usuario, contraseña, tenant real). El script de
  ops mantiene esa separación: código versionado, dato de entorno como argumento.

## Referencias

- [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) — autorización resuelta en la BD del
  tenant; ROOT hace bypass total, de ahí la sensibilidad de concederlo.
- [ADR-008](ADR-008-auditoria-inmutable.md) — auditoría inmutable; el script de ops audita
  en la misma cadena que la API.
- [ADR-010](ADR-010-libreria-compartida.md) — `orpycamcp_common.audit`, usada tanto por
  los endpoints como por `app/ops/grant_root.py`.
