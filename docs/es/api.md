# Referencia de API

> Base URL en desarrollo (desde el host, vía el puerto publicado del gateway): `http://localhost:19080/api/v1`
> Todos los endpoints (excepto los públicos) requieren `Authorization: Bearer {token}`.
>
> Los puertos "puerto NNNN" indicados en cada sección de abajo son los puertos **internos**
> del contenedor dentro de la red Docker `orpycamcp-net` — nunca se acceden directo desde
> el host, siempre a través del `api-gateway` (puerto publicado 19080). Ver el mapeo
> completo interno↔publicado en [Arquitectura](architecture.md#servicios-y-puertos).

## Convenciones

| Convención    | Valor                                                               |
| ------------- | ------------------------------------------------------------------- |
| Autenticación | `Authorization: Bearer {JWT}`                                       |
| Paginación    | `?page=1&size=20` — respuesta incluye `X-Total-Count`               |
| Fechas        | ISO 8601 UTC — `2024-01-15T10:30:00Z`                               |
| IDs           | UUID v4                                                             |
| Multi-tenancy | Header `X-Tenant-Slug` inyectado por el gateway tras validar el JWT |

### Formato de error estándar

```json
{
  "error": "document_not_found",
  "detail": "Radicado 2024-ICETEX-E-000001 no encontrado",
  "status": 404
}
```

---

## auth-service — puerto 8001

### `POST /api/v1/auth/token` — público

Autenticación con credenciales. Retorna par de tokens JWT.

```json
// Request
{ "username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo" }

// Response 200
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 300
}
```

### `POST /api/v1/auth/refresh` — público

Renueva el access token con un refresh token válido.

```json
// Request
{ "refresh_token": "eyJ..." }
// Response 200 — mismo formato que /token
```

### `POST /api/v1/auth/logout`

Invalida el refresh token en Keycloak.

```json
// Request
{ "refresh_token": "eyJ..." }
// Response 204
```

### `GET /api/v1/auth/validate`

Valida un Bearer token. Usado internamente por el api-gateway.

```json
// Response 200
{
  "valid": true,
  "user_id": "uuid",
  "username": "operador",
  "tenant_slug": "demo",
  "roles": ["operator"]
}
```

### `GET /api/v1/auth/me`

Información del usuario autenticado extraída del token, incluyendo sus permisos efectivos resueltos desde la BD del tenant (D-07).

Requiere `Authorization: Bearer <token>`. Si se proporciona `X-Tenant-Slug`, se resuelven los permisos desde la BD del tenant. Sin esa cabecera (superadmin / llamada directa), `permissions` devuelve `[]` y `permission_levels` `{}` (fail-closed).

```json
// Response 200
{
  "user_id": "uuid-keycloak-sub",
  "username": "operador",
  "email": "operador@demo.orpycamcp.local",
  "tenant_slug": "demo",
  "roles": ["operator"],
  "permissions": ["PERM_RADI", "USUA_PERM_CONSULTA"],
  "permission_levels": { "PERM_RADI": 3, "USUA_PERM_CONSULTA": 1 }
}
```

- `permissions`: lista de nombres de permisos con `crud > 0` asignados al usuario a través de sus grupos. Los usuarios `is_root=true` siempre incluyen `USUA_PERM_ROOT`, coherente con `has_permission()` y `GET /users/{id}/permissions`.
- `permission_levels`: **mismas claves que `permissions`**, con el **nivel CRUD** de cada una — `0` ninguno · `1` leer · `2` editar · `3` crear/borrar · `4` nivel_1 · `5` nivel_2 (`auth_group_permissions.crud`, ver `migrations/tenant/001_rbac.sql`). ROOT resuelve `USUA_PERM_ROOT: 5`. Es el campo que un espejo RBAC de interfaz **debe** consultar: los gates del backend usan `min_crud=3` (RF-SEG-03), así que decidir solo por presencia del permiso ofrece acciones de escritura a quien lo tiene en modo lectura, que el backend niega con `403`. `permissions` se conserva por compatibilidad con los consumidores existentes; ambos campos son **aditivos entre sí**, no alternativos.
- Si el usuario no está aprovisionado en el tenant, `permissions` es `[]` y `permission_levels` es `{}` (fail-closed). Ninguno de los dos se omite ni se devuelve `null`: un consumidor nunca debe poder leer su ausencia como "sin restricción".

### 2FA TOTP — credencial del usuario (E06 Inc.6, RF-FIR-13)

El secreto TOTP (RFC 6238) es una **credencial de identidad durable** y vive en auth-service (tabla `auth_user_totp` por tenant, cifrada con **AESGCM**; AAD `tenant:{slug}:user:{id}:totp`). El secreto en claro se revela **una sola vez** en el `otpauth://` de `enroll` (`Cache-Control: no-store`); nunca vuelve a salir. Todos son auto-servicio (bearer del propio usuario).

- `POST /api/v1/auth/me/totp/enroll` — genera el secreto (20 bytes, base32) en estado `pendiente_activacion` y devuelve `{otpauth_uri, estado}` (para QR en un autenticador). Re-enrolar sobre un TOTP **activo** exige step-up (un código actual válido): `409 totp_ya_activo` / `401 step_up_invalido`.
- `POST /api/v1/auth/me/totp/activate` `{code}` — activa la credencial con un primer código válido → `{estado:"activo", activated_at}`. `409 totp_no_pendiente`; `401 codigo_invalido` (suma a `failed_attempts`); `423 totp_bloqueado`.
- `GET /api/v1/auth/me/totp` — `{estado, activated_at, locked_until}`. **Nunca** devuelve el secreto.
- `DELETE /api/v1/auth/me/totp` `{step_up_totp_code | step_up_recovery_code}` — revoca la credencial **activa** exigiendo step-up → `{estado:"revocado"}`. `401 step_up_invalido`; `404 no_totp`.
- `POST /api/v1/auth/me/totp/recovery-codes` `{step_up_totp_code | step_up_recovery_code}` — **regenera** el lote de códigos de recuperación, invalidando el anterior por completo. `200 {recovery_codes:[…]}` con `Cache-Control: no-store`. `409 totp_no_activo`; `401 step_up_invalido`; `423 totp_bloqueado`.
- `POST /api/v1/auth/users/{user_id}/totp/revoke` `{motivo}` — **restablecimiento administrativo** (`USUA_PERM_ADMIN`): retira el segundo factor de **otra** persona para que pueda volver a registrarlo ella misma desde `/perfil`, y anula sus códigos de recuperación en la misma transacción. `200 {estado:"revocado", usuario_id, estado_previo, codigos_recuperacion_eliminados}`; `404 no_totp`; `409 restablecimiento_propio_no_permitido`; `422` si el `motivo` no llega a 8 caracteres. **Nunca** devuelve un secreto ni enrola en nombre de nadie — quien pudiera enrolar por otro tendría de hecho su firma personal. El asiento `totp.restablecido_por_admin` se atribuye al **administrador** (`actor`), con el usuario afectado en `object_ref` y el `motivo` en el `payload`.
  - **Por qué existe, y por qué no se resolvió admitiendo códigos de recuperación al firmar**: los códigos de recuperación reponen el factor pero **no sirven para firmar** (`/internal/totp/verify` solo clasifica un TOTP), y `enroll`/`DELETE /me/totp` exigen el código actual o uno de recuperación. Quien perdía el teléfono **y** los códigos quedaba encerrado, sin más salida que tocar la base de datos a mano. Dejar que un código de papel, de vida larga, firmara habría rebajado el segundo factor de la firma justo en el acto que da fe: se repone el factor, no se degrada.
  - **No se puede usar sobre uno mismo** (`409`): un administrador que se restableciera el suyo podría retirárselo sin demostrar posesión del dispositivo y enrolar otro acto seguido — quien robase su sesión se quedaría con el segundo factor de su firma personal. Hacen falta **dos personas**; el propio administrador usa `/perfil` como todo el mundo.

**Códigos de recuperación (E06, migración 015).** El TOTP es un factor de **posesión**, y hasta esta migración **todas** sus salidas —revocarlo, reenrolarlo, y revocar la clave personal de firma— exigían un código TOTP vigente, es decir, exigían el mismo teléfono que se había perdido: quien lo perdía quedaba **encerrado**, sin más salida que una intervención manual sobre la base. Ahora `activate` devuelve **10 códigos de un solo uso** (`recovery_codes`, `Cache-Control: no-store`) y cualquiera de ellos vale como `step_up_recovery_code` en `enroll` (reenrolar) y en `DELETE /me/totp` (revocar).

- Se guardan **hasheados** (scrypt con sal por código), no cifrados: a diferencia del secreto TOTP, el servidor no necesita recuperarlos en claro, solo comprobarlos — así una copia de la tabla no permite autenticarse. Se muestran **exactamente una vez**; `GET /me/totp` devuelve `recovery_codes_restantes` (un conteo), nunca los códigos.
- **Un solo uso**, garantizado por un `UPDATE ... WHERE used_at IS NULL`: dos peticiones simultáneas con el mismo código compiten por la fila y solo una la gana.
- Se emiten **al activar**, no al enrolar (hasta entonces la credencial no protege nada) y en la **misma transacción** que la activación — no existe un instante con TOTP activo y sin vía de recuperación. Revocar o reenrolar **borra** el lote en la misma transacción.
- **Frontera deliberada: no sirven para FIRMAR.** `POST /internal/totp/verify`, que signature-service invoca al firmar, **no** los acepta (su `code` está validado como TOTP de 6 dígitos). Un código de recuperación está escrito en papel o en un gestor de contraseñas: no es posesión de un dispositivo, y admitirlo como segundo factor de un acto de firma debilitaría la atribución que RF-FIR-13 persigue. Recuperar el acceso y firmar son actos distintos y se exigen pruebas distintas.
- _(Interno, servicio-a-servicio en la red Docker, **no** enrutado por el gateway)_ `POST /internal/totp/verify` `{code, contexto?}` — lo llama signature-service al firmar; deriva la identidad del **bearer reenviado** (no de un `user_id` de cuerpo → sin IDOR), honra `locked_until` antes de comparar, y avanza el anti-replay `last_timestep` en un único `UPDATE` atómico. Lockout tras `TOTP_MAX_FAILED_ATTEMPTS` (default 5) durante `TOTP_LOCKOUT_SECONDS` (default 900). Ventana de skew ±`TOTP_SKEW_STEPS` (default 1).

**Clave de firma PERSONAL PKI (E06/E17 F4, Inc.1 — custodia servidor, `acreditado=false`).** La clave privada de firma de cada usuario vive **solo en auth-service** cifrada AESGCM (KEK `SIGNING_KEY_ENCRYPTION_KEY` **separada** de la del TOTP), y NUNCA sale del servicio.

- `POST /api/v1/auth/me/signing-key/enroll` — genera el par RSA-2048 en memoria, cifra la privada, emite un **CSR** (subject = identidad del usuario + `cargo` mejor-esfuerzo desde grupos RBAC) y queda `pendiente_emision`; devuelve **solo** `{csr_pem, estado}` — **nunca** la clave privada. Exige `PERM_FIRMA`. Un re-enroll revoca la clave activa previa (endurecimiento de step-up diferido a Inc.2). La emisión del cert es **out-of-band** (`scripts/gen-user-cert.sh`, corre con `ca.key` fuera de todo contenedor; sub-CA de usuarios distinta de la del sello).
- `GET /api/v1/auth/me/signing-key` — `{estado, certificado_fingerprint?, not_after?}`. **Nunca** devuelve la clave privada.
- `DELETE /api/v1/auth/me/signing-key` `{step_up_totp_code}` — **revoca la clave propia** exigiendo step-up TOTP (verifica-y-consume; sin código → `422`) → `{estado:"revocado"}` (E06/E17 Inc.2). Deja asiento `signing_key.revocada` (con `revocado_at`/`motivo`) **atómico** con el UPDATE. `404` si no hay clave.
- `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` `{motivo?}` — un **admin** (`USUA_PERM_ADMIN`) revoca la clave de otro usuario (caso "empleado desvinculado"): el `user_id` objetivo va en el path, el actor sale del token (no confundibles). Asiento atómico con `revocado_por`.
- _(Interno, **no** enrutado por el gateway)_ `GET /internal/signing-key/revocation-status?fingerprint=<hex>` — lo consulta signature-service al verificar una firma personal (`X-Internal-Token` + `X-Tenant-Slug`): devuelve **solo** `{status, revocado_at?}` (`status ∈ vigente|revocado|desconocido`) — nunca cert ni clave. Aislado por tenant: un fingerprint de otro tenant / inexistente → `desconocido` (fail-closed, nunca `vigente`). La activación pendiente→activo persiste `user_ca_fingerprint`+`certificado_fingerprint` y valida `leaf.verify_directly_issued_by(sub_ca)` fail-closed.
- _(Interno, **no** enrutado por el gateway)_ `POST /internal/signing-key/sign-digest` `{digest_b64, alg:"RSA-SHA256", prehashed:true, totp_code}` — lo llama signature-service al firmar un acto personal de la cadena: deriva el usuario del **bearer reenviado** (sin IDOR), **verifica-y-consume el TOTP atómicamente** con la firma, descifra la clave **solo en memoria** (vida mínima), firma el digest, y devuelve `{signature_value_b64, certificado_pem, certificado_fingerprint, serial, not_after}` — la privada nunca se devuelve. Es la interfaz "firmar un digest" de un HSM (seam a firma cualificada). `X-Internal-Token` comparado con `compare_digest`. La activación pendiente→activo deja asiento `signing_key.activada` atómico.

### Gestión de usuarios RBAC (E14)

- `POST /api/v1/auth/users` · `GET /api/v1/auth/users` (paginado, `X-Total-Count`) · `GET /api/v1/auth/users/{id}/permissions` — administración de usuarios. Todo el router exige `USUA_PERM_ADMIN`. `POST` deja asiento `rbac.usuario_creado` en `audit_log`, **atómico** con el INSERT (ver nota de auditoría abajo).
  - **`POST` con `is_root: true` → `403 forbidden` si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md), cierre de hallazgo Alto de auto-escalada). Un `USUA_PERM_ADMIN` no-ROOT nunca puede crear un usuario ROOT; ROOT sí. El primer ROOT de un tenant se aprovisiona fuera de banda (`app/ops/grant_root.py`, no HTTP).
- `GET /api/v1/auth/users/pickable?size=N` — proyección mínima `[{id, username}]` de usuarios **activos**, para selectores de UI (p. ej. el `UserPicker` de cadenas de visto bueno, T-08c). Gateado con `PERM_RADI` (no admin); no expone `email` ni metadatos administrativos (D-10). `size` en `[1, 500]`.

### URD y contexto de dependencia (E08)

- `GET /api/v1/auth/users/{user_id}/urd` · `POST` · `DELETE /{urd_id}` — administra las asignaciones Usuario-Rol-Dependencia (una por (usuario, dependencia); una sola principal). Exige `USUA_PERM_ADMIN`. `POST`/`DELETE` dejan asiento `urd.creada`/`urd.eliminada`, **atómico** con la mutación.
- `GET /api/v1/auth/context` — contexto del llamante: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — fija la dependencia activa del propio usuario. **No reemite token** (ADR-013): persiste el contexto y lo audita en `usua_historico` (Canal B, no `audit_log`). `400 no_urd_for_dependency` si no tiene URD activa allí.

### Grupos, membresías y permisos (E08)

Todo el router exige `USUA_PERM_ADMIN`.

- `POST /api/v1/auth/groups` · `GET /api/v1/auth/groups` — alta y listado de grupos. `POST` deja asiento `rbac.grupo_creado`.
- `GET /api/v1/auth/permissions` — catálogo de permisos (`{id, nombre, descripcion}`). **`USUA_PERM_ROOT` se omite del catálogo si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)) — no se lista una opción que el `PUT .../permissions/{id}` de abajo va a rechazar.
- `POST /api/v1/auth/groups/{group_id}/members/{user_id}` · `DELETE .../members/{user_id}` — alta/baja de un usuario en un grupo. Dejan asiento `rbac.miembro_agregado`/`rbac.miembro_removido`.
- `GET /api/v1/auth/groups/{group_id}/members` (paginado, `X-Total-Count`) — usuarios del grupo. Espejo de lectura del alta/baja anterior.
- `PUT /api/v1/auth/groups/{group_id}/permissions/{permission_id}` `{ "crud": 3 }` — fija el CRUD (0-5) de un permiso para el grupo. Deja asiento `rbac.permiso_grupo_asignado` (payload con `permission_id`/`crud`).
  - **Conceder (`crud > 0`) `USUA_PERM_ROOT` → `403 forbidden` si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). Revocar (`crud = 0`) siempre está permitido, incluso a un no-ROOT — solo la concesión está bloqueada. Cierra la vía de auto-escalada vía el grupo del que el propio actor es miembro.
- `GET /api/v1/auth/groups/{group_id}/permissions` — permisos asignados al grupo: `[{permission_id, nombre, descripcion, crud}]`. Espejo de lectura del `PUT` anterior.
- `group_id` de un grupo inexistente **o de otro tenant** → `404` indistinguible (el `search_path` ya acota el schema del tenant; nunca `403`, que confirmaría la existencia del grupo ajeno).

**Auditoría de escrituras RBAC/URD (E08, cierra hueco de trazabilidad):** todo `POST`/`PUT`/`DELETE` de esta sección y de "URD" deja asiento en `public.audit_log` **dentro de la misma transacción** que la mutación (`async with conn.transaction()`) — no _best-effort_: si `audit.append` falla, la mutación se revierte; si la librería de auditoría no está instalada, la escritura responde `500 auditoria_no_disponible` **antes** de tocar ninguna fila (mismo patrón fail-closed que `signing-key`/`totp`). `tenant_slug` es obligatorio internamente (`X-Tenant-Slug`, ya exigido por `get_tenant_conn`); el actor es el `X-User-Id` resuelto por el gate.

### Clasificación de seguridad (E08, RF-SEG-08, Ley 1712/2014)

- `GET /api/v1/auth/security-levels` — catálogo de niveles (1 Pública, 2 Reservada, 3 Clasificada).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — fija el nivel máximo accesible de un grupo (exige `USUA_PERM_ADMIN`). Deja asiento `clearance.grupo_actualizado` con `{group_id, previous_max_level, new_max_level}` (**atómico**, mismas garantías fail-closed que RBAC/URD arriba) — el nivel anterior permite distinguir una subida de una bajada.
  - **No-write-up → `403 forbidden` si `max_level` excede el clearance efectivo del llamante** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). Un admin con clearance RESERVADA (2) no puede fijar CLASIFICADA (3) en ningún grupo, incluido el suyo propio; ROOT no tiene este límite (bypass total, `_MAX_LEVEL`=3).
- `GET /api/v1/auth/groups/{group_id}/clearance` — clearance vigente del grupo: `{group_id, max_level}` (`max_level: null` si el grupo no tiene fila en `role_clearance`, lo que implica PUBLICA=1 por defecto). Exige `USUA_PERM_ADMIN`; mismo `404` indistinguible que el `PUT` para grupo inexistente/de otro tenant.
- `GET /api/v1/auth/clearance` — clearance del llamante: `{user_id, max_level, accessible_levels[]}` (MAX sobre sus grupos; ROOT = 3).
- `GET /api/v1/auth/clearance/check?level=N` — `{level, max_level, allowed}` (mínimo privilegio: `allowed = max_level ≥ level`).

### `GET /api/v1/audit`

Consulta la auditoría inmutable del tenant (ADR-008), más recientes primero. Exige `USUA_PERM_ADMIN`. Filtros: `?action=&object_type=&object_ref=&actor=&page=1&size=20`. Cabecera `X-Total-Count`.

**Recortado por clearance (no-read-up, RF-SEG-08).** `USUA_PERM_ADMIN` es competencia administrativa, no habilitación de seguridad: administrar el tenant no autoriza a conocer los asuntos clasificados que en él se tramitan. Las entradas cuyo objeto es un radicado o un expediente por encima del clearance efectivo del llamante **no se listan ni se cuentan** (`X-Total-Count` refleja el mismo recorte; de lo contrario el total sería un oráculo de cuántos documentos clasificados existen). Detalles del recorte:

- **Lista de admisión, no de denegación**: se listan los tipos que _no pueden_ referirse a un documento ni a un expediente (identidad, RBAC, estructura orgánica, instrumentos y catálogos: `usuario`, `grupo`, `membresia`, `dependencia`, `trd_serie`, `parametro`, `auth_session`…) más los clasificables que resuelvan a un nivel alcanzable. **Todo lo demás queda fuera.** El motivo: `audit_log` la escriben once servicios y varios tipos que no denotan un radicado llevan su identidad en el `payload` — `evento` (dead-letter) serializa el sobre íntegro con `business.tracking_number`, `import` guarda la lista literal de números importados, `file` guarda el nombre del anexo. Con lista de denegación, cada tipo o servicio nuevo nace visible y el olvido es silencioso.
- El nivel se resuelve **en vivo** contra `radicados` / `expedientes` del schema del tenant — nunca denormalizado, para que una reclasificación surta efecto de inmediato. El schema se ancla al `tenant_slug` del request, no al `search_path` de la conexión.
- **Fail-closed**: una `object_ref` que no resuelve a ninguna fila (objeto eliminado, referencia de otro formato) queda fuera; lo que no se puede clasificar no se declara público. Igual si el tenant no tiene las tablas fuente.
- Filtrar por la `object_ref` de un documento fuera de alcance devuelve cero entradas, indistinguible de que no exista.
- Con clearance máximo (3 = CLASIFICADA, y ROOT) **no se aplica recorte alguno**: la auditoría se entrega íntegra, que es lo que su habilitación autoriza.
- **Residuo declarado**: `id` es una secuencia monótona, así que los huecos entre ids visibles revelan _cuántas_ entradas se ocultaron y, por los `ts` vecinos, _cuándo_. No se cierra porque `id` es el que casa con `broken_id` de `/audit/verify`; se documenta.

`GET /api/v1/audit/verify` no se recorta (ver abajo).

```json
[
  {
    "id": 42,
    "ts": "2024-01-15T10:30:00Z",
    "service": "document-service",
    "actor": "uuid",
    "canal": "api",
    "action": "document.radicado_created",
    "object_type": "radicado",
    "object_ref": "2024-ICETEX-E-000001",
    "payload": { "...": "..." },
    "hash": "sha256..."
  }
]
```

### `GET /api/v1/audit/verify`

Recalcula la cadena de hash del tenant y reporta su integridad (ADR-008). Exige `USUA_PERM_ADMIN`. **No** se recorta por clearance: la cadena solo tiene valor probatorio si se recalcula entera, y sobre un subconjunto siempre daría «rota». La respuesta es content-free (un booleano y, si acaso, el id de la fila donde se rompe), así que no revela nada del objeto.

```json
{ "tenant_slug": "icetex", "ok": true, "broken_id": null }
```

- `ok=false` con `broken_id` = id de la primera fila cuya cadena no concuerda.

---

## tenant-service — puerto 8002

### `POST /api/v1/tenants` — **NO enrutado por el gateway**

> **Cierre de hallazgo Crítico (auditoría 2026-07):** el registro de tenants vive
> en el schema `public` (sin `search_path` de tenant) y no tenía **ningún** gate
> de autorización — cualquier usuario autenticado de la entidad A podía
> enumerar (`GET`) y **escribir** (`PATCH`) el registro de la entidad B. La
> corrección quirúrgica fue sacar el prefijo `/api/v1/tenants/` de la tabla de
> ruteo del gateway (`api-gateway/app/routers/proxy.py`): no hay consumidor
> legítimo externo (el frontend no lo llama, y el alta de tenants la hace
> `scripts/init_tenant.py` escribiendo directo a `public.tenants` — operación
> de plataforma, no de aplicación). Los endpoints siguen existiendo en
> tenant-service para ese script/uso interno, pero **ya no son alcanzables**
> desde `http://localhost:19080` — una petición a `/api/v1/tenants/` responde
> `404 route_not_found` sin llegar siquiera a validar el token.

Crea una institución y provisiona su schema PostgreSQL `tenant_{slug}`.

```json
// Request
{ "slug": "icetex", "name": "Instituto Colombiano de Crédito Educativo", "code": "ICETEX" }

// Response 201
{
  "id": "uuid",
  "slug": "icetex",
  "name": "Instituto Colombiano de Crédito Educativo",
  "code": "ICETEX",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

- `slug`: solo minúsculas, números y guiones (`^[a-z0-9-]+$`) — inmutable
- `code`: mayúsculas alfanuméricas (`^[A-Z0-9_]+$`) — aparece en números de radicado — inmutable
- 409 si slug o code ya existen

### `GET /api/v1/tenants` — NO enrutado por el gateway

Lista instituciones. Query: `?status=active&page=1&size=20`. Header `X-Total-Count`.

### `GET /api/v1/tenants/{id}` — NO enrutado por el gateway

Obtener por UUID.

### `GET /api/v1/tenants/by-slug/{slug}` — NO enrutado por el gateway

Obtener por slug. Usado por otros servicios para validar el tenant del JWT.

### `PATCH /api/v1/tenants/{id}` — NO enrutado por el gateway

Actualizar `name` o `status`. `slug` y `code` son inmutables.

### Administración del tenant (E14)

> **Autorización (cierre de hallazgo Crítico, auditoría 2026-07):** el servicio
> no tenía **ningún** gate de autorización (`app/core/authz.py` no existía) —
> cualquier autenticado del tenant podía crear/editar/borrar dependencias,
> catálogos, festivos y parámetros. Las escrituras ahora exigen
> `USUA_PERM_ADMIN` (`app/core/authz.py`, mismo patrón `require_permission` que
> document-service/archive-service) y dejan asiento en `public.audit_log`
> dentro de la misma transacción (fail-closed, patrón `RBACService`). Los `GET`
> quedan **solo autenticados**: son catálogos/calendario que la UI de
> radicación y el cálculo de plazos (RF-RAD-04) necesitan para cualquier
> usuario, no solo administradores.

- **Dependencias** (organigrama): `GET /api/v1/dependencias` (autenticado) · `POST/PATCH/DELETE /api/v1/dependencias` (**`USUA_PERM_ADMIN`**) · `GET /api/v1/dependencias/by-codigo/{codigo}` (jerárquicas, autenticado).
- **Catálogos** (lookup): `GET /api/v1/catalogos/{catalogo}` (autenticado) · `POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` (**`USUA_PERM_ADMIN`**) — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
  - **`causales` distingue el acto al que sirve** (`ambito`, migración tenant 008): `devolucion`, `anulacion`, `reasignacion`, `devolucion_postal` o `general`. `GET /api/v1/catalogos/causales?ambito=devolucion` filtra en la fuente, y `ambito` viaja en el cuerpo de `POST`/`PATCH`. Es el único catálogo que lo admite: pedirlo sobre otro responde **422 `ambito_no_aplica`** en vez de ignorarlo en silencio. Una causal sin ámbito declarado queda en `general` y la ofrecen todos los actos.
- **Parámetros**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}` (autenticados) · `PUT /api/v1/config/params/{key}` (**`USUA_PERM_ADMIN`**).
- **Días no hábiles / festivos**: `GET /api/v1/config/holidays` (`?year=`, autenticado) · `POST /api/v1/config/holidays`, `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year_from, year_to}` → siembra los festivos colombianos calculados automáticamente) — las 3 escrituras exigen **`USUA_PERM_ADMIN`**.
- **Días hábiles**: `GET /api/v1/business-days/calculate?from=&days=` (autenticado) — calcula la fecha de vencimiento aplicando fines de semana + festivos + no laborables del tenant (base de RF-RAD-04).

### PINAR — Plan Institucional de Archivos (E14, RF-ADM-08, Acuerdo AGN 003/2015)

Instrumento de planeación archivística por tenant. Todas las escrituras (ciclo de
vida del plan, aspectos, priorización, objetivos, proyectos, seguimiento,
instrumentos) exigen **`USUA_PERM_ADMIN`** (`app/core/authz.py`, cierre del mismo
hallazgo Crítico que catálogos/dependencias/config — antes no había ningún gate
de rol, pese a que la spec §10.3 restringe la gestión al administrador). **Desde
2026-08-28 los `GET` también** (§4.bis del plan de pruebas): el gate vive en el
propio `APIRouter`, así que lo hereda todo endpoint que se añada. Antes las 11
lecturas quedaban «solo autenticadas» mientras la barra lateral y las tarjetas de
`/admin` escondían la pantalla — un sistema que esconde algo y lo sirve a la vez
no ha decidido nada. Se cierra en vez de abrirse porque un plan en `borrador` es
material de trabajo del Comité, y porque publicar el PINAR aprobado (Decreto
1080/2015 art. 2.8.2.5.8) es un acto hacia fuera —una pantalla pública— y no un
`GET` interno sin gate que se le parece por accidente. El actor imputable llega en
`X-User-Id`.

- **Ejes** (catálogo fijo, seed de 5): `GET /api/v1/pinar/ejes`.
- **Plan**: `POST /api/v1/pinar/planes` (crea en `borrador`, `version = max+1`) · `GET /api/v1/pinar/planes` (`?estado=`) · `GET /api/v1/pinar/planes/{id}` · `PATCH /api/v1/pinar/planes/{id}` (visión/vigencia, solo en `borrador`).
- **Transiciones**: `POST /planes/{id}/aprobar` (`borrador→aprobado`, exige `acto_administrativo` → 422 si falta) · `POST /planes/{id}/ejecutar` (`aprobado→en_ejecucion`, **un único plan en ejecución por tenant** → 409 `plan_en_ejecucion_existe`) · `POST /planes/{id}/cerrar` (terminal). Cada acto de ciclo de vida es **imputable** (X-User-Id obligatorio → 400 si falta) y queda en la **`audit_log` INMUTABLE (E08)**. Al **aprobar** el contenido se **congela** (no más edición estructural, solo seguimiento).
- **Aspectos críticos** (diagnóstico): `POST/GET /planes/{id}/aspectos`, `PATCH/DELETE /aspectos/{id}`.
- **Priorización** (matriz aspecto×eje, impacto 1–10): `PUT /planes/{id}/priorizacion` (carga la matriz) · `GET /planes/{id}/priorizacion` → cada aspecto con su prioridad = **Σ impactos**, ordenada desc (define el mapa de ruta).
- **Objetivos**: `POST/GET /planes/{id}/objetivos`, `PATCH/DELETE /objetivos/{id}`.
- **Proyectos**: `POST/GET /planes/{id}/proyectos`, `PATCH/DELETE /proyectos/{id}`, `PUT /proyectos/{id}/aspectos` (vínculo a los aspectos que mitiga).
- **Seguimiento** (append-only): `POST /proyectos/{id}/seguimiento` (proyecta el `avance_pct` del proyecto) · `GET /proyectos/{id}/seguimiento`.
- **Tableros**: `GET /planes/{id}/tablero` (avance por objetivo y por eje) · `GET /planes/{id}/mapa-ruta` (cronograma por fechas).
- **Articulación** (por referencia, no copia): `POST/GET /planes/{id}/instrumentos`, `DELETE /planes/{id}/instrumentos/{id}` — `tipo ∈ {ccd, trd, fuid, plan_preservacion, plan_contingencia}`.

Edición estructural permitida **solo en `borrador`** (→ 409 `plan_no_editable` en `aprobado`/`en_ejecucion`/`cerrado`: el contenido se congela al aprobar); seguimiento permitido en `{aprobado, en_ejecucion}` (→ 409 `plan_no_seguible`).

---

## document-service — puerto 8003

> Requiere headers `X-Tenant-Slug` y `X-User-Id` (inyectados por el gateway).

### `POST /api/v1/documents`

Registra un nuevo radicado. Asigna número de radicado atómico.

```json
// Request
{
  "doc_type": "E",
  "subject": "Solicitud de certificado de notas",
  "doc_class": "Derechos de Petición",
  "dest_dept_code": 120,
  "sender_name": "María García",
  "sender_entity": "Ciudadana",
  "pages": 2,
  "response_days": 15,
  "nivel_seguridad": 1,
  "metadata": { "area": "juridica", "prioridad": "alta" },
  "anexos": [
    { "file_id": "uuid", "filename": "solicitud.pdf", "file_size": 45231, "mime_type": "application/pdf",
      "tipo_anexo": "soporte", "es_principal": true, "folios": 3 }
  ]
}

// Response 201
{
  "id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "doc_type": "E",
  "year": 2024,
  "sequence": 1,
  "subject": "Solicitud de certificado de notas",
  "status": "registered",
  "nivel_seguridad": 1,
  "registered_at": "2024-01-15T10:30:00Z",
  "due_date": "2024-02-05",
  "dest_dept": "Registro y Control",
  "dest_dept_code": 120,
  "anexos": [{ "id": "uuid", "file_id": "uuid", "filename": "solicitud.pdf", ... }]
}
```

- `doc_type`: `E` (Entrada) | `S` (Salida) | `I` (Interno)
- `tracking_number`: inmutable una vez asignado
- `response_days` (opcional): plazo de respuesta en **días hábiles** (RF-RAD-04). Si se indica, document-service calcula `due_date` consultando `tenant-service` (`GET /api/v1/business-days/calculate`, que aplica festivos colombianos y no laborables del tenant). Si se omite, `due_date` es `null`.
- `due_date`: fecha de vencimiento (ISO `YYYY-MM-DD`) o `null`.
- `origin_dept_code` / `dest_dept_code` (opcionales): código (`depe_codi`) de la dependencia en el organigrama. Si se indican, document-service los valida contra `tenant-service` (`GET /api/v1/dependencias/by-codigo/{codigo}`) y **denormaliza el nombre** de la dependencia en `origin_dept`/`dest_dept` (snapshot legal). Código inexistente o dependencia inactiva → `400 invalid_dependencia`.
- Si `tenant-service` no está disponible al resolver plazo o dependencia, la creación responde `502 tenant_service_unavailable` y **no** se radica el documento.
- `metadata` (opcional): metadatos variables del tipo documental (ADR-007). Si el `doc_class` tiene una **plantilla activa**, los valores se validan contra su JSON Schema antes de radicar; si no cumplen → `422 metadata_validation_failed` y no se radica. Sin plantilla, se almacenan tal cual. La columna está indexada con GIN.
- `nivel_seguridad` (opcional, default `1`): clasificación de seguridad del radicado (RF-SEG-08) — `1`=PUBLICA, `2`=RESERVADA, `3`=CLASIFICADA (= `security_levels.code`). La búsqueda oculta a cada usuario los radicados por encima de su _clearance_ (ver _Full-Text Search_).

> **Control de acceso (catálogo de metadatos):** las **mutaciones** — `POST /api/v1/metadata/templates` y `POST/PATCH/DELETE /api/v1/metadata/elements` — exigen **`USUA_PERM_TRD`** (fail-closed: `403` sin tenant/user-id/permiso), el mismo permiso que gobierna su gemelo `POST /api/v1/expediente-metadata/templates` en archive-service (una plantilla que relaja la validación obligatoria de un tipo documental es tan consecuente como la de un expediente). `DELETE /metadata/elements/{id}` exige además **`crud≥3`** (RF-SEG-03; precedente `DELETE /tipos-documentales/{id}`). Las cuatro escrituras dejan asiento en `audit_log`. Las **lecturas** GET quedan abiertas (solo tenant).

### `POST /api/v1/metadata/templates`

Define la plantilla de metadatos (JSON Schema) de un tipo documental. Una sola plantilla activa por `tipo_documental`. Exige `USUA_PERM_TRD`.

```json
// Request
{
  "tipo_documental": "Factura",
  "version": 1,
  "activo": true,
  "json_schema": {
    "type": "object",
    "required": ["valor"],
    "properties": { "valor": { "type": "number" } }
  }
}
```

- `422 invalid_json_schema` si `json_schema` no es un JSON Schema válido; `409 conflict` si ya existe esa versión o una activa para el tipo.

### `GET /api/v1/metadata/templates`

Lista plantillas; filtro opcional `?tipo_documental=Factura`.

### `/api/v1/metadata/elements` (catálogo de elementos de metadato, RF-MET-03)

CRUD de definiciones reutilizables de campos de metadato (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Atributos: `clave`, `etiqueta`, `tipo_dato` (text/number/date/boolean/select), `longitud`, `ocurrencia_min/max`, `modificable`, `valor_default`, `opciones`, `orden`, `searchable`, `mapeo_dublin_core` (gancho RF-MET-09). `409` si la clave ya existe. `POST`/`PATCH` exigen `USUA_PERM_TRD`; `DELETE` exige `USUA_PERM_TRD` con `crud≥3`.

### `GET /api/v1/documents`

Listar radicados. Filtros: `?doc_type=E&status=registered&page=1&size=20`. **Filtro por metadato** (RF-MET-02): `?meta.<campo>=valor` (uno o varios) usa contención JSONB sobre el índice GIN, p. ej. `?meta.area=juridica`. **Control de acceso (RF-SEG-08):** el listado se acota por la clasificación del radicado igual que la búsqueda — cada usuario solo ve los de `nivel_seguridad ≤` su _clearance_ (_fail-closed_ a PUBLICA).

### `GET` / `PATCH /api/v1/documents/{id}/disposition`

Metadatos de disposición del documento (RF-MET-08): `programa`, `retention_until`, `accion` (conservar/eliminar/transferir/seleccionar), `confirmado`, `marcado_eliminacion`, `fundamento`. `PATCH` hace merge de los campos provistos. **`GET` exige `USUA_PERM_EXPEDIENTE`; `PATCH` exige además `crud≥3`** — antes bastaba el nivel de lectura para marcar un documento oficial para destrucción, mismo permiso y mismo nivel que para consultarlo (mismo umbral que las disposiciones de `storage-service`).

`fundamento` es **obligatorio** cuando `accion="eliminar"` o `marcado_eliminacion=true` (`422` si falta): la eliminación es irreversible (Ley 594/2000 art. 24) y debe quedar motivada. No se exige en el camino no destructivo, para que el campo no se devalúe a base de rellenarlo por rutina. Todo `PATCH` deja asiento `document.disposition_set` en `audit_log` **con el estado anterior**, en la misma transacción que la escritura.

> Alcance declarado: la disposición **no se deriva todavía de la TRD**. La tabla de retención vive en `archive-service` y el radicado no guarda su serie, así que derivarla exige un cambio de modelo y un contrato entre servicios; el fundamento y el asiento son, mientras tanto, el control compensatorio.

**Control de acceso (RF-SEG-08):** `404` tanto si el radicado no existe como si supera el _clearance_ del llamante — indistinguibles, para no revelar la existencia de material clasificado.

### `POST /api/v1/documents/{id}/publicacion`

Acto de **publicación de oficio** (Ley 1712/2014 art. 12). Cuerpo: `{publicar: bool, fundamento: str}` — el fundamento es obligatorio en **ambos** sentidos (`422` si falta), porque retirar una publicación también es una decisión que hay que poder explicar. Respuesta: `{tracking_number, publicable, publicado_at}`.

Exige `PERM_RECLASIFICAR` con `crud≥3`: el mismo permiso que decide la **reserva**, porque publicar y clasificar son las dos caras de la competencia sobre el régimen de acceso del documento; repartirlas entre permisos distintos permitiría que quien no puede desclasificar publicara igualmente.

Antes de este endpoint, «público» se **deducía**: un radicado con `nivel_seguridad=1` y no anulado era cosechable por OAI-PMH y servible por CMIS, es decir, la ausencia de reserva se leía como decisión de publicar. Ahora la columna `publicable` arranca en `FALSE` y solo cambia por este acto, que queda asentado (`document.publicacion_activada` / `document.publicacion_retirada`).

El recorte público exige las **tres** condiciones a la vez (`publicable AND nivel_seguridad = 1 AND status <> 'anulado'`, chokepoint único en `oai_repository._PUBLIC`), y el `UPDATE` exige además `nivel_seguridad = 1` en su propio `WHERE`: marcar como publicable un documento reservado no es un caso a filtrar después, es una operación que no debe existir — si se guardara, la próxima desclasificación lo publicaría sin que nadie lo hubiera decidido. `404` neutro para «no existe», «supera tu clearance», «no es de nivel público» y «está anulado».

> **Compatibilidad**: los radicados existentes quedan **no publicables** tras la migración `019_radicado_publicable.sql`. Una institución que use los canales de cosecha debe publicar expresamente lo que quiera difundir.

**Lectura del estado.** `publicable` y `publicado_at` viajan en `DocumentResponse` — es decir, en `GET /documents/{id}` y en el listado. Añadirlos no fue cosmético: el acto se podía **ejecutar pero no consultar**, así que ni la interfaz podía ofrecerlo sobre un estado conocido ni la entidad acreditar qué había publicado. Un esquema de publicación que no se puede leer no es un esquema, es una escritura ciega.

**Interacción con la reserva (corregido tras auditoría, 2026-08-13)**: clasificar un radicado (`PATCH /{id}/security-level` a nivel ≥ 2) **retira su publicación** en la misma transacción — antes la marca quedaba latente y la siguiente desclasificación, hecha con otro propósito, lo devolvía a los canales sin acto. Y el `UPDATE` exige `nivel_seguridad = 1` **solo al publicar**: retirar una publicación nunca está bloqueado, ni siquiera sobre un radicado ya reservado, que es donde más urge. El recorte de contenido de CMIS (`getContentStream`) exige ahora `publicable` igual que el de metadatos: llevaba una copia del `WHERE` escrita a mano que no se actualizó.

**Pantalla**: el acto se ejecuta desde el drawer «Publicación» de `/bandeja` (ver `docs/es/pantallas.md`), que ofrece siempre el acto contrario al estado vigente y no ofrece formulario alguno sobre un radicado reservado o anulado — el `404` neutro del backend es correcto, pero dejar que alguien redacte un fundamento para recibir después «no existe» sería una pantalla que engaña.

### `GET /api/v1/assistant/alcance`

Marco de alcance del llamante: **qué puede hacer, qué puede consultar y qué no puede — con su vía alternativa**. Sin permiso propio: describe las capacidades de quien pregunta, resueltas de su token.

Existe porque los permisos venían siendo un filtro silencioso: el catálogo escondía lo que el usuario no podía usar, y el límite se descubría **fallando** —o esperando una acción que nunca iba a ocurrir—. El marco lo declara antes de empezar y ofrece la salida cuando la hay («no puede enviar, pero puede trasladar el radicado a correspondencia»).

Se **deriva**, no se mantiene: sale del catálogo de tools —que declara permiso, nivel, operación de dominio y vía alternativa— cruzado con `/auth/me`, `/auth/clearance` y `/auth/context`. Añadir una tool actualiza el marco sola.

Incluye `limites_declarados`: lo que el sistema **no** distingue, para no prometer precisión inexistente — el permiso de radicación no separa por tipo de documento (`PERM_RADI_ENTRADA`/`PERM_RADI_INTERNO` existen y **ningún gate los usa**) y las lecturas se acotan por nivel de seguridad, no por dependencia.

Vive bajo `/api/v1/assistant/` porque es el único prefijo del mcp-server que el gateway enruta. No exige que el asistente esté disponible: saber qué se puede hacer no depende de que haya un modelo detrás.

### `POST /api/v1/expedientes/{id}/publicacion`

Acto de **publicación de oficio del expediente** (Ley 1712/2014 art. 12, migración archive `042`). Mismo contrato que el gemelo de radicados: `{publicar: bool, fundamento: str}` → `{code, publicable, publicado_at}`, `PERM_RECLASIFICAR` con `crud≥3`, fundamento obligatorio en ambos sentidos, acto y asiento en una sola transacción.

Cierra la asimetría que destapó la auditoría cruzada: la corrección del art. 12 se había aplicado a los radicados y **no aquí**, así que `oai_repository._PUBLIC` de archive-service seguía siendo `nivel_seguridad = 1` y el OAI EAD multinivel difundía de oficio todo expediente no clasificado — el valor por defecto. La asimetría era además implícita: quien leyera el recorte de document-service supondría que este servicio hace lo mismo.

Dos diferencias con radicados, **deliberadas**:

- El recorte **no lleva condición de estado**. Un radicado anulado está retirado de la circulación; un expediente `closed`/`transferred` no lo está — son estados normales de su ciclo de vida. Copiar `status <> …` por simetría habría despublicado en silencio todo el archivo cerrado, que es la mayor parte de un fondo.
- Lo que se difunde es de nivel agregado: código, nombre, descripción, fechas extremas y el **número** de radicados. Los `tracking_number` hijos no se enumeran nunca (un expediente público puede contener radicados reservados).

`publicable` y `publicado_at` viajan en `ExpedienteResponse`. **Compatibilidad**: los expedientes existentes quedan **no publicables** tras la migración `042`.

**Pantalla**: drawer «Publicación» en `/expedientes/{id}`. Sobre un expediente reservado no ofrece publicar —y lo explica— pero **sí ofrece retirar**, que es la asimetría real del backend.

### `GET /api/v1/documents/{id}`

Obtener por UUID. Incluye lista de anexos. **Control de acceso (RF-SEG-08):** si el radicado supera el _clearance_ del llamante, responde `404` (no revela su existencia).

### `GET /api/v1/documents/by-tracking/{tracking_number}`

Obtener por número de radicado (ej: `2024-ICETEX-E-000001`). Misma acotación por clearance que el detalle por UUID (`404` si excede el nivel del llamante).

### `PATCH /api/v1/documents/{id}`

Actualizar `subject`, `dest_dept`, `observations`, `status`, `pages`, `doc_class` y `metadata`.
`tracking_number`, `doc_type`, `year`, `sequence` son inmutables.
**Tipificar (`doc_class`) exige `PERM_RADI` con `crud >= 3`; el resto del PATCH, `crud >= 1`.** El tipo documental fija la serie TRD, y con ella la retención y la disposición final del radicado (Ley 594/2000 art. 24): un acto con esa consecuencia no puede ejercerse con permiso de lectura. El umbral se comprueba solo cuando `doc_class` viaja en el cuerpo, así que quien corrige un asunto o un número de folios conserva el nivel que siempre le bastó.
**Retipificar revalida los metadatos.** Los metadatos efectivos —los que vengan en el cuerpo, o los que el radicado ya tuviera— se validan contra la plantilla activa del tipo **nuevo** antes de escribir nada; si no encajan, `422 metadata_validation_failed` y no se toca el radicado. Un documento no puede quedar tipificado como X arrastrando los campos variables de Y. `metadata` se **reemplaza entero**, no se fusiona, y la versión de plantilla usada se reescribe siempre que haya revalidación —incluido el `NULL` cuando el tipo nuevo no tiene plantilla activa—, para que el radicado no siga apuntando al esquema del tipo anterior. Enviar solo `metadata` también revalida: si no, la validación de la radicación sería puenteable con un PATCH posterior.
**Control de acceso (RF-SEG-08):** `404` si el radicado no existe **o** supera el _clearance_ del llamante. El gate es imprescindible porque la respuesta devuelve el radicado completo (asunto, remitente, anexos): sin él, la escritura sería también una lectura.
**`409 radicado_anulado`:** un radicado anulado no admite modificaciones. `status` acepta `registered`, así que sin este guard un PATCH deshacía una anulación aprobada por el circuito de dos pasos, sin causal ni aprobador. El desanulado, si procediera, es un acto propio — no el efecto lateral de editar un campo. Es un `409` y no un `404` porque en ese punto el radicado ya pasó el gate de _clearance_: decir que está anulado no revela nada que el llamante no pudiera leer.
**Auditoría:** deja asiento `document.radicado_actualizado` con el **antes y el después** de cada campo tocado, en la MISMA transacción que la mutación — si el asiento falla, la modificación revierte.

### `PATCH /api/v1/documents/{id}/security-level`

**Reclasificación del nivel de seguridad** de un radicado ya radicado (E08, paridad con el _Cambio Nivel de Seguridad_ de Orfeo). Cambia `nivel_seguridad` (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) sin alterar contenido ni `tracking_number` (inmutabilidad intacta). Requiere permiso **`PERM_RECLASIFICAR`**.

- **Body**: `nivel_seguridad` (1..3, obligatorio), `motivo` (string no vacío, **obligatorio** — el acto de reserva debe motivarse, Ley 1712 art.19/28), y opcionales `causal_reserva`, `fundamento_juridico`, `plazo_reserva_meses`.
- **Control de acceso (RF-SEG-08)**: _no-read-up_ — si el _clearance_ del llamante es menor que el nivel actual del radicado responde `404` neutro (no revela existencia ni nivel); _no-write-up_ — no puede fijar un nivel mayor que su propio _clearance_ (`403`). Si el nivel solicitado coincide con el actual, es _no-op_ idempotente (sin auditoría ni evento).
- **Traza**: registra en el `audit_log` inmutable (`document.radicado_reclassified`) el `nivel_anterior`, `nivel_nuevo`, `motivo` y el fundamento jurídico, de forma atómica con el cambio.
- **Propagación**: emite `document.radicado.reclassified`; el signature-service re-sincroniza el _snapshot_ `nivel_seguridad` de las cadenas de firma del objeto (cierra el residual de _staleness_ de RF-SEG-08).

---

## storage-service — puerto 8005

> Requiere header `X-Tenant-Slug`. Archivos almacenados en MinIO bucket `orpycamcp-{slug}-documents`.

### `POST /api/v1/storage/internal/artefacto`

**Interno** (`X-Internal-Token`, no enrutado por el gateway). Sube un artefacto que produce el propio sistema —hoy, el XML del índice electrónico firmado— con su **propia** lista de formatos (`allowed_system_mime_types`, `application/xml`). Es un camino distinto del de los anexos a propósito: la lista blanca de `/upload` acota lo que entra desde fuera (RF-DIG-04) y no admite XML, así que el índice firmado no se podía almacenar.

### `POST /api/v1/storage/upload`

Sube un archivo (`multipart/form-data`, campo `file`).

- Calcula SHA-256; si ya existe en el tenant retorna el registro existente con `200` (deduplicación)
- Crea el bucket del tenant si no existe
- Retorna `201` en upload nuevo
- **Valida el formato** contra la lista de MIME admitidos (`ALLOWED_MIME_TYPES`, configurable; `*` = todos) → `415 unsupported_media_type` si no está permitido (RF-DIG-04)

```json
// Response 201
{
  "id": "uuid",
  "filename": "solicitud.pdf",
  "mime_type": "application/pdf",
  "file_size": 45231,
  "sha256": "e3b0c44298fc...",
  "uploaded_at": "2024-01-15T10:30:00Z"
}
```

### `GET /api/v1/storage/files/{file_id}`

Metadata del archivo (sin contenido).

### `GET /api/v1/storage/files/{file_id}/download`

Retorna URL pre-firmada de MinIO válida 1 hora.

```json
{
  "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...",
  "expires_in": 3600
}
```

### `GET /api/v1/storage/files/{file_id}/verify`

Reverifica la integridad (RF-DIG-02): descarga el objeto de MinIO, recalcula el SHA-256 y lo compara con el registrado.

```json
{
  "file_id": "uuid",
  "expected_sha256": "...",
  "actual_sha256": "...",
  "integrity_ok": true
}
```

- `409 integrity_check_failed` si el objeto en MinIO no coincide con la huella registrada (alerta de integridad).

### `POST /api/v1/storage/files/{file_id}/replace`

Reemplaza un anexo creando una **nueva versión inmutable** (RF-DIG-01; `multipart/form-data`, campo `file`). La versión anterior se conserva.

- `201` con la nueva versión (`version` incrementado); `200` si el contenido es idéntico (no se crea versión); `409 not_current_version` si `{file_id}` no es la versión vigente; `415` si el formato no está admitido.

### `GET /api/v1/storage/files/{file_id}/versions`

Devuelve la cadena de versiones del anexo (ordenada por `version`). Cada elemento incluye `version` e `is_current`.

### `DELETE /api/v1/storage/files/{file_id}`

Elimina de MinIO y de la base de datos. Responde `204`.

### Preservación digital (E10, OAIS/AGN 001/2024)

- `GET /api/v1/preservacion/plan` (vigente) · `PUT /api/v1/preservacion/plan` (nueva versión) · `GET /api/v1/preservacion/plan/versions` — Plan de Preservación Digital versionado: `formatos_destino`, `num_copias`, `periodicidad_fixity_dias`, `politica_migracion`, `contingencia`.
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` — registro inmutable PREMIS. `tipo` ∈ INGESTA/FIJACION/MIGRACION/VALIDACION_PDFA/WORM/REPLICA; `resultado` ∈ `ok`/`fallo`/**`no_evaluado`** (tercer estado desde E10 C2a, migración `008`: la validación PDF/A con el stub por defecto reporta `no_evaluado` honestamente — `"not evaluated" ≠ "passed"`). Al empaquetar el AIP, cada documento **PDF** (detectado por magic bytes `%PDF`) emite un `VALIDACION_PDFA` (**advisory** — un PDF no-PDF/A no bloquea el AIP) + un evento PREMIS `validation`. Config: `PRESERVATION_PDFA_VALIDATION_ENABLED` (default `false`), `PDFA_VALIDATOR` (`stub`|`verapdf`, default `stub`), `PDFA_PROFILE` (default `2b`). **El validador veraPDF real (subprocess) está implementado (E10 C2b)**: con `PDFA_VALIDATOR=verapdf` + `VERAPDF_BINARY` (ruta absoluta al binario, presente en la imagen de **deploy** — la de dev degrada honestamente a stub `no_evaluado`), `SubprocessVeraPdfValidator` ejecuta veraPDF con modelo de seguridad endurecido (ENV scrubbeado, `killpg`+reap garantizado, semáforo, tempdir 0700, timeout→`no_evaluado`, JSON-only). Tuning: `VERAPDF_TIMEOUT_SECONDS` (60), `VERAPDF_MAX_CONCURRENCY` (1), `VERAPDF_JVM_MAX_HEAP_MB` (512), `VERAPDF_JVM_MAX_METASPACE_MB` (256).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, file_id, nombre}], indice_file_id?, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment B, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** empaqueta el **AIP OAIS real**: por cada documento resuelve su objeto por `file_id`, descarga los bytes de MinIO, **recalcula la fixity** (SHA-256 vs `files.sha256`; mismatch → `409` + evento `FIJACION` fallo), arma un **Bag BagIt RFC 8493** (`data/` con los bytes + `bagit.txt` + `bag-info.txt` con Payload-Oxum real + `manifest-sha256.txt` + `premis.xml` [PREMIS v3, namespace oficial, validado contra un perfil XSD local] + `indice-firmado.xml` [si `indice_file_id`] + `tagmanifest-sha256.txt`), lo **serializa ZIP**, lo sube al **bucket WORM** de preservación con `Retention(COMPLIANCE, retain_until)` derivada de la TRD + `legal_hold`, y lo registra en `preservacion_worm_objeto` (`tipo='aip'` → cubierto por el gate de disposición). Requiere `USUA_PERM_EXPEDIENTE` con **`crud≥3`**. `nombre` se valida (sin `/`, `..`, control chars) y debe ser **único** dentro de `documentos` (`422`). `201` con `{id, expediente_id, aip_sha256 (del ZIP), num_documentos, estado, bucket, object_key, aip_bytes, formato, retain_until, retention_mode, legal_hold, premis_xml, replicado, replica_target, replica_copias_hechas, created_at}`; `400` retención inválida; `404/422` `file_id` inexistente o `nombre` duplicado; `409` fixity mismatch; `503` si `PRESERVATION_ENABLED=false`. **REPLICA (C1b)**: si `PRESERVATION_REPLICA_ENABLED=true` y el plan tiene `num_copias≥2`, tras subir el AIP primario se crea **una réplica inmutable** en un segundo bucket WORM (`replica_target="local"` = segundo bucket del mismo MinIO, _no_ copia geográfica; `="cross-site"` si `MINIO_SECONDARY_ENDPOINT`). Es **best-effort no-fatal**: un fallo de réplica **no** revierte el AIP (`replicado=false` + evento `preservacion_evento` `REPLICA` `resultado=fallo`); `num_copias≤1` → sin réplica.
- `GET /api/v1/preservacion/aip/{expediente_id}` — AIP vigente (requiere `USUA_PERM_EXPEDIENTE` `min_crud=1` — expone `premis_xml` con nombres/hashes de documentos y ubicación WORM). Los campos de ubicación WORM son `null` en filas AIP previas al Increment B (retrocompat).
- `POST /api/v1/preservacion/proteger-indice` `{file_id, expediente_id, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment A, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** protege el **índice electrónico firmado** (ya subido a `files`, referenciado por `file_id`) bajo **WORM / Object-Lock (MinIO)**. Aprovisiona (idempotente) el bucket de preservación por tenant `orpycamcp-{slug}-preservation` (nombre **solo del claim JWT**), **verifica fixity** (recalcula el SHA-256 sobre los bytes y lo contrasta contra el hash **registrado en la subida original** — el llamador **no** declara ningún hash; mismatch → `409` + evento `FIJACION` fallo, no sube), copia el objeto con **`Retention(COMPLIANCE, retain_until)`** por-objeto + `legal_hold`, registra en `preservacion_worm_objeto` y emite eventos PREMIS `WORM`/`FIJACION` + `audit_log`. La **retención la aporta el caller** derivada de la TRD (storage no llama a archive): `retain_until` directo **o** `retencion_anios` (1–100) + `fecha_inicio` (aritmética de años calendario), nunca ambos. Requiere `USUA_PERM_EXPEDIENTE` con **`crud≥3`** (umbral de disposición) + clearance. **Idempotente (E10 Inc.1 wiring)**: `201` con la ubicación WORM si es la primera protección; **`200`** con la fila existente si el índice de ese expediente YA está protegido (no re-sube — corte-circuito antes del `put_object`, respaldado por el índice único parcial `WHERE tipo='indice'` y un **advisory lock de sesión por `(tenant, expediente)`** que serializa `check→put→insert` para no duplicar objetos WORM irreversibles bajo carrera). `400` retención inválida (ausente/pasada/fuera de cotas o fuentes en conflicto); `404` si el `file_id` no existe en el tenant; `409` fixity mismatch; `503` si `PRESERVATION_ENABLED=false`. **Frontera de confianza**: la correspondencia `file_id ↔ expediente_id` (que `file_id` sea el índice firmado del expediente) la garantiza **archive-service** (único invocador); storage valida que el `file_id` exista pero no modela expedientes. **Efecto en disposición**: mientras el índice esté bajo `retain_until` futuro o `legal_hold`, la disposición final del expediente queda **bloqueada** (la eliminación no puede ocurrir antes de expirar la retención — Ley 594/2000, Acuerdo AGN 060/2001).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/proteger-indice-internal` — gemelo **interno** (D-02) de `proteger-indice` con el MISMO servicio/idempotencia, autenticado **solo** por `X-Internal-Token` en vez de `require_permission(crud≥3)` y atribuido al principal de sistema `SYSTEM_RECONCILER_ID`. Existe porque el **cableado WORM del índice** (`IndexService.proteger_worm_indice`, archive-service, E10 Inc.1) tiene dos orígenes: el intento síncrono best-effort tras firmar el índice al cierre (`origen="close"`, usuario real → ruta pública) y la **3ª fase de reconciliación** (`run_once_worm`, `origen="reconciliation"`, `SYSTEM_RECONCILER_ID` sin fila `auth_users` → esta ruta interna; `require_permission` la rechazaría siempre). La retención se deriva de la TRD del expediente (`archivo_gestion_years + archivo_central_years`, `fecha_inicio`=cierre); **fail-closed si la TRD no resuelve** (no protege, marca `trd_sin_retencion`, recuperable por el barrido). Espeja `/signature/internal/sign-indice`.
- `POST /api/v1/preservacion/proteger-artefacto` `{file_id, expediente_id, tipo, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Inc.2 wiring, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** forma **generalizada** de `proteger-indice`: protege bajo WORM/Object-Lock cualquier **artefacto de preservación** del expediente, con `tipo ∈ {`indice`, `acta_transferencia`}` (default `indice` si se omite). MISMO servicio, fixity, retención derivada de la TRD por el caller, RBAC `USUA_PERM_EXPEDIENTE crud≥3` + clearance, e **idempotencia** que el `proteger-indice`, ahora **por `(expediente_id, tipo)`** (índice único parcial `WHERE tipo='acta_transferencia'` en `preservacion_worm_objeto`, migración `010`) y con el **advisory lock de sesión por `(tenant, expediente, tipo)`** — dos tipos del mismo expediente (índice y acta) no colisionan pero cada uno serializa su propio `check→put→insert`. `/proteger-indice` se conserva como **alias duro** que fuerza `tipo="indice"` sea cual sea el body (retrocompat del Inc.1). Códigos y frontera de confianza idénticos a `proteger-indice`. El **acta de transferencia firmada** (sello XAdES, `transferencia_acta_firma`) la cablea archive vía `TransferenciaService.proteger_worm_acta` (disparo síncrono best-effort tras el sello + 4ª fase de reconciliación `run_once_actas_worm`); su retención se deriva de la TRD del expediente de la transferencia con **`fecha_inicio = firmado_at` del acta** (no el cierre — un acta puede firmarse mucho después en transferencias secundarias).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)_ `POST /api/v1/preservacion/proteger-artefacto-internal` — gemelo **interno** de `proteger-artefacto` (con `tipo` en el body), autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Lo usa la reconciliación desatendida del acta (`run_once_actas_worm`, `origen="reconciliation"`), igual que `proteger-indice-internal` para el índice. `/proteger-indice-internal` sigue siendo el alias duro `tipo="indice"`.
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/renovar-retencion-internal` `{expediente_id, tipo, retencion_anios + fecha_inicio | retain_until}` — **(E10 debt: renovación de retención WORM para series de Conservación Total)** extiende (nunca acorta) el `retain_until` de un artefacto **ya protegido** bajo WORM, identificado por `(expediente_id, tipo)` (`tipo ∈ {indice, acta_transferencia}`). Reusa `RetentionMixin`. Semántica **monotónica**: si la fecha resuelta no supera **estrictamente** el `retain_until` ya persistido, es un **no-op idempotente** (`200`, `renovado=false`, sin tocar MinIO). Si la supera, llama a `Minio.set_object_retention` (extensión real; COMPLIANCE rechaza acortar de todos modos) y responde `200` con `renovado=true`; el UPDATE + evento PREMIS `RENOVACION` + asiento `file.worm_retencion_renovada` van en **una tx** (el `set_object_retention` queda fuera, antes). Autenticado **solo** por `X-Internal-Token` (atribuido a `SYSTEM_RECONCILER_ID`): la renovación es un **acto de sistema** conducido por la 5ª fase de reconciliación de archive (`run_once_worm_renovacion`), único que conoce qué serie es CT — **NO hay ruta pública** (se retiró en la remediación: storage no puede imponer el filtro CT, así que una ruta pública con `retain_until` directo sería superficie irreversible sin garantía CT). `404` `worm_object_not_found`; `400` retención inválida; `503` si `PRESERVATION_ENABLED=false`. `retention_mode`/`legal_hold` **no cambian**. Respuesta: `{id, expediente_id, tipo, bucket, object_key, retain_until, retention_mode, renovado, renovaciones, renovado_at}`.
- `GET /api/v1/expedientes/indices/pendientes-renovacion?page=&size=` — **(E10 debt: visibilidad de la renovación CT)** lista los artefactos WORM de series **CT** (índice + acta) cuya renovación está **agotada** (`worm_renovacion_agotado=TRUE`) o **en riesgo** (`worm_retain_until` vence dentro de `worm_renovacion_ventana_dias`) — para que un archivista vea un lock COMPLIANCE de conservación permanente a punto de expirar sin renovar (espejo de `pendientes-firma`). Gate `USUA_PERM_EXPEDIENTE` + **no-read-up** (el filtro `nivel_seguridad <= clearance` vive en el SQL de ambas consultas; `X-Total-Count` refleja el conteo **ya filtrado** por clearance — no es oráculo). Ordena agotados primero, luego vencimiento más próximo. Por fila: `{expediente_id, expediente_code, tipo, worm_retain_until, worm_renovacion_intentos, worm_renovacion_agotado, worm_renovacion_ultimo_error}`.
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)_ `POST /api/v1/preservacion/renovar-retencion-internal` — gemelo **interno** de `renovar-retencion`, autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Único llamador en este incremento: la **5ª fase de reconciliación** de archive-service (`run_once_worm_renovacion`), que barre — por cada tenant activo — los índices y actas **de series de disposición Conservación Total (CT)** cuyo `retain_until` vence dentro de `WORM_RENOVACION_VENTANA_DIAS` (default 400 días) y llama a este endpoint con `retencion_anios=total_TRD` + `fecha_inicio=hoy` (**ventana rodante**, nunca una fecha lejana/9999). El filtro CT es **fail-closed en la consulta SQL** del lado de archive (`disposition ∈ {CT, conserve}`) — series `E`/`S`/`M` nunca entran al barrido, aunque estén próximas a vencer (expiran y se disponen legítimamente). Backoff exponencial propio (`worm_renovacion_intentos`/`worm_renovacion_agotado` en `expediente_indice`/`transferencia_acta_firma`, migraciones `031`/`032`) — un `agotado=true` en este eje es una alarma de **mayor severidad** que en el sellado/protección inicial: implica riesgo de que el lock COMPLIANCE de un registro de conservación **permanente** venza sin haberse podido extender. **AIP queda fuera de alcance** de la renovación (sin espejo en archive-service, deuda futura).

---

## workflow-service — puerto 8006

> Requiere headers `X-Tenant-Slug` y `X-User-Id`.

> **Distribución automática (RF-FLU-04):** además de los endpoints, workflow-service consume el stream `orpycamcp.document.events`. Al radicarse un documento (`document.radicado.created`) evalúa las reglas activas y, si alguna casa, **autoasigna** el radicado a su dependencia (paso + evento append-only, autor = sistema). Es idempotente (no redistribuye si el radicado ya tiene pasos).

### `POST /api/v1/workflows/assign`

Asigna un radicado a una dependencia (primer paso o reasignación). Exige `PERM_TRAMITAR`; `403 forbidden` si falta permiso o tenant (RF-SEG-03).

```json
// Request
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "to_dept": "Dirección Jurídica",
  "assigned_to": "uuid-usuario",
  "action": "assign",
  "notes": "Para concepto jurídico"
}
// Response 201 — FlowStepResponse
```

### `POST /api/v1/workflows/transfer`

Transfiere el radicado a otra dependencia. Requiere `from_dept`. Exige `PERM_TRAMITAR` (RF-SEG-03).

### `GET /api/v1/workflows/{radicado_id}/history`

Historial completo de pasos ordenado por `step_number ASC`.

```json
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "steps": [
    { "step_number": 1, "from_dept": null, "to_dept": "Ventanilla", "action": "assign", "status": "completed", ... },
    { "step_number": 2, "from_dept": "Ventanilla", "to_dept": "Dirección Jurídica", "action": "transfer", "status": "pending", ... }
  ]
}
```

### `GET /api/v1/workflows/inbox`

Bandeja tipada de pasos activos (RF-FLU-03). `?box=entrada|salida|internos` (mapea a `doc_type` E/S/I), `&dept=&assigned_to=&page=&size=`. Prioriza por antigüedad; cabecera `X-Total-Count`. `400 invalid_box` si el box no es válido. Cada paso incluye `due_at` y `semaforo` (verde/amarillo/rojo/vencido, RF-FLU-07).

**Acotado por dependencia (RF-SEG-08):** sin `dept`/`assigned_to`, el llamante ve **lo suyo** — pasos asignados a él o de una dependencia a la que pertenece (URD activa). Pasar `dept` y/o `assigned_to` **amplía** el alcance a otra dependencia u otro usuario y exige `PERM_TRAMITAR` o `USUA_PERM_ADMIN`; sin ese permiso, `403 forbidden` (no se ignora el parámetro en silencio). `X-Total-Count` siempre refleja el mismo filtro que las filas devueltas.

### `POST /api/v1/workflows/overdue/scan`

Barrido de vencimientos (RF-FLU-07): detecta pasos activos vencidos no alertados, emite `workflow.step.overdue` al bus (→ notificaciones) y los marca para no duplicar. Idempotente por paso; pensado para invocación periódica por un scheduler. Responde `{ "alerted": N }`. Exige `USUA_PERM_ADMIN` (barrido transversal del tenant). La reasignación masiva `POST /api/v1/workflows/reassign/cascade` (RF-FLU-08) exige el mismo permiso de administración.

### Vistos buenos secuenciales (paridad legado)

- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — crea la cadena ordenada de revisión (1..20 revisores, sin duplicados). Exige `PERM_RADI` (D-09); `403 forbidden` si falta permiso, identidad o tenant. `409 cadena_existente` si el radicado ya tiene cadena.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — el revisor de turno (menor orden pendiente) aprueba/rechaza. Requiere `X-User-Id` válido (400 si falta). `403 not_a_reviewer` si el actor no está en la cadena de revisores de ese radicado; `403 no_es_su_turno` si está en la cadena pero no es su turno; un rechazo detiene la cadena.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — cadena + estado global (`en_revision|aprobado|rechazado`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (devolución al remitente)

Re-enruta un radicado mal asignado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Cierra el paso activo como `returned` y crea uno nuevo hacia `to_dept`, con la causal en el historial. Exige **`PERM_TRAMITAR`** (como `assign`, `transfer` y `complete`; esta ruta y `rollback` eran las dos únicas escrituras de trámite del router sin ningún gate). Autorización por tenencia además del permiso: solo el responsable actual del paso activo puede devolver. `403 not_current_holder` si el actor no es el tenedor. `404` si no hay paso activo.

### Transacciones de trámite (RF-FLU-01)

- `GET /api/v1/workflows/transaction-types` — catálogo (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), con su permiso atómico y efecto de estado.
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — ejecuta la transacción: aplica el efecto al paso activo (p. ej. `marcar_leido`→in_progress, `anular`→cancelled) y registra el evento append-only. **Enforcement RBAC por-tipo (RF-SEG-03):** cada tipo declara en `transaction_types.requires_permission` el permiso que exige (`anular`→`PERM_ANULAR`, `cerrar_exp`→`PERM_CERRAR_EXP`, `vobo`→`PERM_VOBO`…); sin él, `403 forbidden` y la transacción **no** se aplica. Un tipo con `requires_permission = NULL` **cae al suelo `PERM_TRAMITAR`**, no es abierto: `transaction_types` es un catálogo del tenant, y dejar la columna vacía no puede ser la manera más fácil de abrir la puerta. Hoy los 12 tipos sembrados declaran el suyo, así que el suelo no cambia nada observable. No hay gate de ruta fijo a propósito: exigir `PERM_TRAMITAR` a todas rompería a quien tiene `PERM_VOBO` para dar vistos buenos y nada más. `404 unknown_transaction` / `no_flow`. `PATCH /{step_id}/complete` exige `PERM_TRAMITAR`.

### `POST /api/v1/workflows/{radicado_id}/rollback`

`{motivo}` (**obligatorio**, 5-500 caracteres). Exige **`PERM_TRAMITAR`**. Revierte la última asignación (RF-FLU-08): cancela el paso vigente —solo si sigue `pending`— y reactiva el anterior, atómicamente, con evento `tipo_tx=rollback`. El `motivo` queda registrado en dos trazas atómicas con la mutación (una sola transacción): `flow_events.comentario` (hoja de ruta operativa) y `public.audit_log.payload` (traza legal forense, mismo patrón que la asignación). `409 no_active_step` (sin paso activo) o `cannot_rollback_initial` (es la radicación inicial). `422` si falta el `motivo` o no alcanza el mínimo de caracteres.

### `POST /api/v1/workflows/reassign/cascade`

Reasigna en una transacción todos los pasos activos de un usuario o dependencia a un nuevo responsable, sin dejar radicados huérfanos (RF-FLU-08).

```json
// Request (indicar from_assigned_to o from_dept)
{ "from_dept": "Dirección Jurídica", "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12 }
```

### `GET /api/v1/workflows/{radicado_id}/events`

Historial **append-only** de transacciones del radicado (RF-FLU-02, inalterable). A diferencia de `/history` (proyección de pasos), registra cada transacción y su tabla rechaza UPDATE/DELETE. **Control de acceso (E05 §10 / RF-SEG-08):** es la _hoja de ruta operativa_ (Canal B) — visible a cualquier usuario que pueda **leer** el radicado, acotada por su clearance (no read-up); exige `X-User-Id` y un radicado por encima del clearance devuelve historial vacío. No requiere permiso de administrador (eso es la auditoría forense `audit_log`).

```json
[
  {
    "id": "uuid",
    "tipo_tx": "assign",
    "from_dept": null,
    "to_dept": "Ventanilla",
    "actor": "uuid",
    "comentario": "...",
    "detalles": { "step_number": 1 },
    "ts": "2024-01-15T10:30:00Z"
  }
]
```

### `GET /api/v1/workflows/{radicado_id}/current`

Paso activo actual del radicado. `404` si está cerrado o sin asignar.

### `GET /api/v1/workflows/pending`

Radicados pendientes. Filtros: `?dept=Dirección+Jurídica&assigned_to={uuid}&page=1&size=20`.

**Acotado por dependencia (RF-SEG-08):** mismo criterio que `/inbox` — por defecto, lo propio (asignado al llamante o a una dependencia a la que pertenece por URD activa); `dept`/`assigned_to` amplían el alcance y exigen `PERM_TRAMITAR` o `USUA_PERM_ADMIN` (`403 forbidden` sin el permiso).

### `PATCH /api/v1/workflows/{step_id}/complete`

Marca un paso como completado.

```json
// Request
{ "notes": "Concepto emitido, se adjunta" }
// Response 200 — FlowStepResponse actualizado
```

---

## archive-service — puerto 8004

> Requiere headers `X-Tenant-Slug` y `X-User-Id`.

### Expedientes

> **Control de acceso:** **todos** los endpoints de expedientes (12: create/list/search/get/foliado/eventos/close/transfer/update/link/batch/unlink) y los de `batch.py` (`POST /api/v1/batch/expedientes`, `GET /api/v1/batch/expedientes/{job_id}/status`) requieren `USUA_PERM_EXPEDIENTE` (fail-closed: `403` sin `X-Tenant-Slug`, sin `X-User-Id`, o sin el permiso). **Mínimo privilegio (RF-SEG-03, nivel `crud` 0-5):** las operaciones de **disposición** — `close`, `transfer`, `unlink`, y el `PATCH` que transiciona a `closed`/`transferred` — exigen `USUA_PERM_EXPEDIENTE` con **`crud≥3`** (Crear/Borrar); el resto (lecturas, create, link, PATCH de nombre/serie) basta con `crud≥1`. En `list`/`get`/`eventos`/`search` **y el `PATCH`** la **clearance (RF-SEG-08, no read-up)** filtra **además** lo visible por nivel de seguridad: cada usuario solo ve/edita expedientes de `nivel_seguridad ≤` su _clearance_ (_fail-closed_ a PUBLICA sin `X-User-Id`), un expediente por encima devuelve **404** (no revela su existencia), y en `list`/`search` el **`X-Total-Count` también queda filtrado** (no revela cuántos clasificados existen). Toda consulta de `list`/`search` deja una **traza agregada** en `audit_log` (RF-BUS-10: usuario, criterios, total; sin el texto crudo de `q`), separada de la pista por-expediente. En `link`/`batch`/`unlink`/`indice/rebuild` (M2, RF-SEG-08) la **mutación es legítima sin clearance de lectura** (componer/ordenar el expediente es gestión archivística, ortogonal a leer su contenido) y **siempre** ocurre y **siempre** audita; pero la **respuesta se acota** si el llamante no puede leer el expediente (`nivel_seguridad >` su _clearance_): counts/huella/`added_at` → `null`, y se **suprimen los oráculos** (409 `radicado_already_linked`, 404 `radicado_link_not_found`, 409 `expediente_not_open`) devolviendo un ack **uniforme e indistinguible** sea cual sea el estado — un no-lector no infiere pertenencia, volumen ni ciclo de vida de un expediente clasificado. El permiso se resuelve en la BD del tenant (el api-gateway no gatea por ruta).

#### `POST /api/v1/expedientes`

Crea expediente. Genera código automático `EXP-{AÑO}-{SEQ:04d}`.

```json
// Request
{ "name": "Pensión María García - 2024", "description": "...", "trd_serie_id": "uuid",
  "metadata": { "tipo_contrato": "prestación" } }
// Response 201
{ "id": "uuid", "code": "EXP-2024-0001", "name": "...", "status": "open", "metadata": { ... }, ... }
```

- `metadata` (opcional, ADR-007): si la serie TRD (`trd_serie_id`) tiene una plantilla activa, los valores se validan contra su JSON Schema → `422 metadata_validation_failed` si no cumplen. Columna indexada con GIN.

#### `POST /api/v1/expediente-metadata/templates`

Define la plantilla de metadatos (JSON Schema) de una serie TRD. Una sola activa por serie. `422 invalid_json_schema` / `409 conflict`. `GET` lista (filtro `?trd_serie_id=`).

#### Transferencias documentales (E12)

> **Control de acceso (D-08):** todos los endpoints requieren `USUA_PERM_EXPEDIENTE`. Sin él: `403 {"error": "forbidden"}`.

- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, ubicacion_origen_id?, ubicacion_destino_id?}` — crea (precondición: expediente `closed`; `409 expediente_not_closed` si no). Ciclo `preparada→enviada→recibida|rechazada`. Cada acto (crear/enviar/recibir/rechazar) deja asiento inmutable en `audit_log` (éxito y denegación) con el actor identificado unívocamente por `X-User-Id` (UUID; sin fallback a username). **Integridad (E12 H-L/H-M)**: a lo sumo **una transferencia activa** (`preparada`/`enviada`) por expediente — una 2ª da `409 transferencia_activa_existente` (índice único parcial; tras un `rechazada` se puede re-preparar); y si se aportan `ubicacion_origen_id`/`ubicacion_destino_id` deben ir **ambas** y ser **coherentes con el tipo** (`primaria`: origen `gestion`→destino `central`; `secundaria`: `central`→`historico`; Ley 594/2000 art. 23), con `origen != destino` — si no: `422` (`ubicaciones_par_incompleto` | `ubicaciones_origen_destino_iguales` | `ubicacion_not_found` | `ubicacion_incoherente_con_tipo`). **Obligatoriedad condicionada a la tenencia física**: si el expediente tiene ubicación física localizada (vía `expediente_unidad`, que es M2M), el par es **obligatorio** (`422 ubicaciones_requeridas_fisico`) y `ubicacion_origen_id` debe **cotejar** — estar entre las ubicaciones actuales del expediente (`422 ubicacion_origen_no_actual`; vale cualquiera de las M2M); si el expediente es puramente **electrónico** (sin unidades físicas), el par es opcional (transferencia lógica). El destino no se coteja (solo coherencia de tipo — es a dónde va). Lectura de topología física sin clearance (custodia ortogonal).
- `POST /{id}/enviar`, `POST /{id}/recibir`, `POST /{id}/rechazar`. Al **recibir**, dentro de una única transacción: se **re-verifica la fixity** del acervo (SHA-256 de cada documento vs la huella del índice + re-hash del XML del índice; `409 fixity_mismatch` que revierte sin congelar si el acervo se alteró entre el cierre y la recepción — la respuesta lleva solo `num_documentos_fallidos`, el detalle por-documento queda en `audit_log`), el expediente pasa a `transferred` (congelado), y se **congela el acta de entrega** (ver `/{id}/acta`). El **rechazo** exige `{motivo}` (obligatorio, `min_length=3`, no vacío → `422`); el motivo se guarda en columna propia sin sobrescribir la observación de preparación.
- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (FUID **en vivo** del expediente, regenerado al vuelo).
- `GET /{id}/acta` (`?verify=true`) — **acta de entrega CONGELADA** al recibir (E12 H-I, Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26). Devuelve el snapshot inmutable escrito en la recepción (nunca lo regenera): `TransferenciaActaResponse` con el XML canónico del FUID, su `acta_fuid_sha256` (fixity del acta, recomputable por el cliente), los tres responsables `elaborado_por`/`entregado_por`/`recibido_por` con sus fechas, e `indice_version` transferida. **Bloque `firma`** (E06 H-H): `{presente, valida, xades_level, acreditado, motivo}` — el acta lleva un **sello XAdES-B/T/LT/LTA + OCSP institucional del tenant** (enveloped sobre el XML canónico, cubre los bytes cuyo SHA se ancla en `audit_log`); `presente=true` cuando el sellado convergió a `firmado`; con `?verify=true` se computa `valida` on-demand (descarga el XML firmado de MinIO y lo verifica contra signature-service). `acreditado` **siempre `false`** (sello no acreditado ONAC — fecha cierta **verificable, no oponible en sentido fuerte**; `xades_level` se lee junto a `acreditado`). Sellado **best-effort** (la firma es aditiva): si aún no se selló, `firma.presente=false` con `motivo` (`pendiente_firma`/`sello_ausente`/…) — el acta sigue **íntegra** por fixity + inmutabilidad + anclaje. **No-read-up** en la lectura (el acta expone asunto/serie/signatura = contenido): `404` total si el expediente supera el clearance del llamante — asimétrico con la **generación**, que ocurre con clearance sin restricción (custodia). La inmutabilidad del acta es **forzada por trigger** a nivel de motor (`BEFORE UPDATE/DELETE/TRUNCATE`, migración `023`) además de verificable por fixity y anclada en la hash-chain.
- `POST /{id}/acta/firmar` — reintento **manual** del sellado XAdES del acta `pendiente_firma` (E06 H-H). `USUA_PERM_EXPEDIENTE`, atribuido al humano que lo dispara (`origen="manual"`). `409 acta_ya_firmada` si ya está firmada (idempotente), `503` si sigue sin poder sellarse. **Re-arma** el backoff de reconciliación para que el job automático reanude. La firma del acta también converge sola vía el job de reconciliación generalizado (cadencia menor que el índice).
- `POST /{id}/acta/firmar-personal` `{totp_code, rol?}` — **firma electrónica PERSONAL PKI de un RESPONSABLE del FUID** (ELABORADOR "Elaborado por" rol `elabora`, REMITENTE "Entregado por" rol `entrega`, o RECEPTOR "Recibido por" rol `recibe`) sobre el acta de entrega (E17/F4 Inc.1-3; Ac. AGN 001/2024 Anexo FUID; Ac. AGN 042/2002; Ley 527/1999 art. 7). Paso **explícito posterior** a `recibir()` (nunca inline: preserva la tx de custodia sin 2FA); los roles se firman **en cualquier orden**, cada uno como acto atómico independiente. **El rol se DERIVA de la identidad congelada**: `actor == creada_por` → `elabora`, `actor == enviada_por` → `entrega`, `actor == decidida_por` → `recibe`. El `rol` del body es **opcional** y solo desempata cuando la misma identidad tiene varios roles (auto-traslado: `400 rol_requerido` si falta): NUNCA concede un rol — un tercero que no es ninguno de los tres responsables recibe **`403 firmante_no_es_parte`** (y también si pide vía `rol` un papel que su identidad no otorga), **antes** de consumir el TOTP. `USUA_PERM_EXPEDIENTE` + **no-read-up** real sobre el expediente para todos los roles (`404` neutro sobre-clearance); `409 transferencia_no_recibida` si aún no está `recibida`. Archive **media**: reenvía el `Authorization`/TOTP del responsable a signature-service `POST /signature/sign-personal` (verifica-y-consume el TOTP atómico y firma el `acta_fuid_xml` congelado con su cert personal). **Idempotente** sobre `(transferencia_id, rol)`: repetir un rol ya firmado devuelve `200` con la fila existente, sin re-firmar ni re-quemar TOTP. `503 canal_firma_personal_no_disponible` si signature no responde. Devuelve `TransferenciaActaFirmaPersonalResponse` (`rol`, `firmante_id`, `firma_id`, `xades_level`, `acreditado` **siempre `false`**). Registrada en `transferencia_acta_firma_personal` (**sin** reconciliación desatendida — requiere TOTP interactivo). El gate `acta_firma_personal_require` (default `off`) **nunca bloquea `recibir()`** ni la firma de otro rol; solo modula el veredicto de conformidad.
- `GET /{id}/acta/verificacion` — **verificación agregada** del acta (E17/F4 Inc.1-3). Compone la verificación del **sello institucional** + la de **cada firma personal** presente (descarga los XML firmados de MinIO y los verifica contra signature-service). Devuelve `{sello: {...}, firmas_personales: [{rol, firmante_id, valida, revocacion_status}], completitud, valida, acreditado, auto_traslado, elaborador_firmado}`: `completitud ∈ {sellada, firma_personal_pendiente, firmada_receptor, firmada_remitente, firmada_completa}` (derivada, presencia factual — `firmada_completa` = **ambas PARTES DEL TRASPASO** (entrega+recibe) firmaron = **atribución bilateral reforzada**, NO conformidad jurídica plena). El **ELABORADOR es firmante ADITIVO** (no parte del traspaso): su firma NO condiciona `completitud`, se reporta en el booleano ORTOGONAL **`elaborador_firmado`**. `valida` = cripto + revocación de **todo** lo presente (incluida la firma del elaborador, fail-closed → un cert de elaborador revocado da `valida=false` aun con `firmada_completa`; los dos ejes de ortogonalidad difieren a propósito), `acreditado` **siempre `false`**, `auto_traslado=true` cuando la misma identidad envió y recibió (**observación de conformidad**: sin control dual — el FUID no exige personas naturales distintas, se señala pero no se impide). **No-read-up** idéntico a `/{id}/acta` (`404` neutro sobre-clearance; los cuerpos no enumeran radicados reservados).

#### Archivo físico (E17, ADR-017)

> **Control de acceso (D-08):** todos los endpoints requieren `USUA_PERM_EXPEDIENTE`. Sin él: `403 {"error": "forbidden"}`. `GET /expedientes/{id}/unidades` también lo requiere; el frontend lo llama via `Promise.allSettled` y trata el 403 como error de faceta sin bloquear la vista.

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — topología recursiva (sede→…→gaveta); `codigo` único por tenant; `ruta` derivada (`SED-001 / DEP-001 / EST-003`), resuelta para **todas** las filas del listado con un CTE, no una consulta por fila. Filtros `?parent_id=&activo=&solo_raiz=`.
  > **`solo_raiz` no equivale a omitir `parent_id`.** Sin `parent_id` el listado **no filtra por padre** —lo natural en una API de listado, y lo que necesita quien puebla un desplegable con todo el archivo—; `solo_raiz=true` filtra `parent_id IS NULL`, que es lo que necesita quien pinta el primer nivel de un árbol. Confundirlas hacía que la pantalla listara cada gaveta también como raíz. Pedir las dos juntas responde **422 `filtros_incompatibles`**: es una petición sin significado, y elegir una mitad en silencio sería ignorar la otra.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — unidades de conservación (caja|carpeta|…); al indicar `ubicacion_id` se deriva la **signatura topográfica** (`DEP01-E05-C0124`, única). Filtros `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — vincular expediente a unidad (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — qué contiene la unidad; **filtrado por fila** por clearance (RF-SEG-08, no-read-up): enumerar el contenido de una unidad revela la _existencia_ de sus expedientes clasificados, así que un no-lector no ve los de `nivel_seguridad >` su _clearance_ (Ley 1712/2014 art. 19; _fail-closed_ a PUBLICA, `403` sin `X-User-Id`). `GET /api/v1/expedientes/{id}/unidades` — dónde está el expediente (signatura + `tipo`/`codigo` de la unidad + rango de folios; la signatura dice en qué entrepaño mirar, el código dice qué caja sacar); **sin filtro de clearance a propósito**: se consulta por un `expediente_id` ya conocido y solo devuelve ubicación física (signatura/folios, no asunto/serie) — es gestión de custodia ortogonal al clearance de lectura (mismo criterio que la transferencia).
- **Préstamos (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 si ya prestada), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` se deriva de `fecha_devolucion_esperada`. Las cuatro rutas devuelven `unidad_codigo` y `unidad_signatura` además del `unidad_id`: un préstamo se consulta para saber **qué falta del estante**, y en un depósito nadie identifica una caja por su UUID.
- **Historial (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — movimientos append-only (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — inventario documental (expediente↔unidad↔signatura↔serie) en JSON con orden consecutivo y campos AGN 042/2002 —incluidos `unidad_tipo` y `unidad_codigo`, las fechas extremas y el rango `folio_inicio`–`folio_fin`—; filtros `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exporta en XML. **Cabecera del formato** (Ac. AGN 042/2002, Anexo): `entidad_productora` la resuelve el **servidor** desde `public.tenants` —encabeza un documento archivístico oficial, y un campo libre convertiría el inventario de una entidad en el de cualquier otra—, mientras que `unidad_administrativa`, `oficina_productora` y `objeto` llegan como parámetros de consulta y son **declarativos**: los rellena quien elabora el inventario, igual que en papel. Cada fila lleva además `frecuencia_consulta` (columna 9 del formato: `alta`/`media`/`baja`), **derivada** de los accesos registrados en `audit_log` durante los últimos 12 meses en vez de estimada — el conteo está acotado por `tenant_slug`, porque `audit_log` es la única tabla compartida entre entidades y los códigos de expediente son consecutivos que coinciden entre ellas. **Control de acceso (RF-SEG-08, no-read-up):** el FUID describe **contenido** (`asunto`/serie/`signatura`), así que se filtra **por fila** por la clasificación del expediente — cada usuario solo ve en el inventario los de `nivel_seguridad ≤` su _clearance_ (`total` cuenta solo lo visible; _fail-closed_ a PUBLICA, `403` sin `X-User-Id`). En `GET /transferencias/{id}/fuid` (un solo expediente) el filtro es un **404 total** si el expediente supera el clearance (indistinguible de inexistente). La transferencia en sí (create/enviar/recibir/rechazar) es acto de **custodia**, ortogonal al clearance de lectura (no filtra por él, como el cierre/transferencia de expedientes).

#### TRD/CCD — la TRD como instrumento convalidado (E04, [ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md))

> **Control de acceso (config archivística):** las **mutaciones** de config (`POST/PATCH /api/v1/trd`, `POST /trd/{code}/versiones`, el circuito de convalidación de abajo, `POST/PATCH/DELETE /api/v1/tipos-documentales`, `POST /api/v1/expediente-metadata/templates`) requieren **`USUA_PERM_TRD`** (fail-closed: `403` sin tenant/user-id/permiso) y dejan asiento en `audit_log`. **Disposición de config** — `DELETE /tipos-documentales/{id}`, `PATCH /trd/{id}`, `POST /trd/{code}/versiones` y el circuito de convalidación — exige **`crud≥3`** (RF-SEG-03). Las **lecturas** GET **no son todas iguales**, y desde 2026-08-28 el código lo refleja (§4.bis del plan de pruebas). El **vocabulario de clasificación** —`GET /api/v1/trd`, `GET /trd/{id}`, `GET /trd/{id}/retention` y sus gemelos de TVD, más `GET /api/v1/tipos-documentales` y `GET /api/v1/expediente-metadata/templates`— queda **abierto a cualquier usuario autenticado del tenant, por decisión declarada**: lo consumen `/radicar` y `/expedientes` para poder clasificar, y `USUA_PERM_TRD` lo tiene quien **administra** el instrumento, no quien lo usa —exigirlo aquí dejaría sin radicar a todo operador ordinario—; además el instrumento convalidado es de publicación obligatoria (Ac. AGN 004/2019, compilado por 001/2024; Ley 1712/2014 art. 9). El **circuito** sí exige **`USUA_PERM_TRD`** (`crud≥1`): `GET /trd/revision-pendiente` y `GET /trd/{code}/versiones` + `GET /tvd/{code}/versiones`, porque el historial devuelve los borradores, el `acta_comite`, la `fecha_aprobacion_comite`, la `instancia_convalidante` y el `created_by` de cada versión —la deliberación del Comité, no el instrumento publicado—.
>
> **Máquina de estados del instrumento** (ADR-025, Ac. AGN 001/2024 que compila el 004/2019): `borrador → aprobada → convalidada`, con `migrada` como estado de backfill (series preexistentes a la migración 037, "el sistema no sabe si fueron convalidadas") que puede **ratificarse** directamente a `convalidada` sin pasar por `aprobada`, y `aprobada → borrador` (**devolver**, migración 038) como vía honesta de devolución con observaciones. `borrador`/`aprobada`/`derogada` **no** gobiernan un cierre (`409 trd_version_no_vigente`); `convalidada`/`migrada` sí. La eliminación efectiva (disposición final, sin job todavía) exigirá que el snapshot congelado describa un instrumento convalidado — `convalidada`, o `derogada` con acto (D5, gate corregido en la 038: ver `app/core/trd_disposition.py`) — el resto de las reglas están en el ADR.

#### `GET /api/v1/trd/disposicion-vencida` — cola de vencimientos (RF-TRD-08, 2026-08-28)

`?fase=central|gestion` (por defecto `central`) · `?disposition_code=CT|E|S|M` · `?page` · `?size` · `X-Total-Count`. Exige **`USUA_PERM_TRD`**.

Expedientes cuyo calendario de retención ya venció, leído del **snapshot congelado al cerrar** (`expedientes.disposition`) y nunca de la fila viva de la serie: el calendario que gobierna a un expediente es el que se congeló, y una convalidación posterior no adelanta ni retrasa un vencimiento ya fijado (ADR-025 D3/D4). `fase=gestion` mira `fin_archivo_gestion` (toca transferir al archivo central) y `fase=central` mira `fin_archivo_central` (toca la **disposición final**) — dos colas con dos actos distintos.

Cada fila trae `accion` (qué corresponde, derivado del código de disposición: **un vencimiento no es una orden de destruir** — en `CT` significa transferir al histórico y conservar permanentemente) y `gobierna_eliminacion`, que responde **exactamente lo mismo** que responderá `assert_disposicion_final_autorizada` cuando exista el brazo ejecutor, porque ambos llaman al mismo predicado.

**No-read-up (RF-SEG-08) en la misma condición que las filas**, `COUNT` incluido: recortar solo la página dejaría que el total delatara cuántos expedientes clasificados hay vencidos. Deja **traza agregada de consulta** (RF-BUS-10, `archive.expediente_disposicion_vencida_listado`): enumerar lo que está a un paso de eliminarse debe dejar rastro de quién lo hizo.

> **Este endpoint no ejecuta nada.** La eliminación efectiva (RF-TRD-07: borrado seguro con acta, aval del Comité y publicidad previa del inventario) sigue sin construirse.

#### `POST /api/v1/trd/disposicion-vencida/{expediente_id}/decidir` — la confirmación (RF-TRD-06/08, 2026-08-28)

Body `{fase, decision, motivo?, revisar_de_nuevo_el?}` · `201` · Exige **`USUA_PERM_TRD` con `crud≥3`** (el mismo nivel que el circuito de convalidación: decidir es la precondición de una destrucción, no una anotación) y **actor imputable** (`X-User-Id` fail-closed).

«Ninguna destrucción es automática sin confirmación de usuario autorizado» (RT 04, regla 4). Dos decisiones, las dos **no destructivas**:

- `confirmada` — el usuario autorizado declara que procede la acción de la TRD. **No elimina nada**: la respuesta trae `ejecutada: false` y una nota que lo dice, porque un `200` sobre un endpoint llamado «decidir» invita a creer que algo ocurrió.
- `aplazada` — R.4.9 del Modelo SGDEA. Exige `motivo` (≥10 caracteres) y `revisar_de_nuevo_el` **futura**: un aplazamiento sin fecha de regreso es un olvido con mejor nombre. El vencimiento sale de la cola activa y **vuelve solo** en esa fecha (o antes, con `?incluir_aplazados=true`).

**Lo que no se acepta del cliente**: la fecha del vencimiento y el código de disposición **no viajan en el cuerpo**, salen del snapshot congelado en la base — si viajaran, confirmar una eliminación sería cuestión de mandar la fecha adecuada. El expediente se resuelve **acotado por clearance** y uno fuera de alcance responde `404`, no `403`.

Si la acción termina en destrucción (`E`, `S` o `M` — selección elimina lo no seleccionado y microfilmación elimina el original), se exige que el instrumento congelado la gobierne: llama a `assert_disposicion_final_autorizada` (ADR-025 D5) y responde `409 trd_version_no_convalidada_para_disposicion_final`. **Sobre `CT` el gate no aplica**: conservación total no termina en destrucción. La decisión queda en la tabla append-only `disposicion_decisiones` (migración `046`) y en la **`audit_log` inmutable, en la misma transacción** — si la cadena no puede recibir el asiento, la decisión no se toma.

- `POST/GET/PATCH /api/v1/trd` — series y **subseries** documentales. Campos: `code`, `name`, `parent_id` (subserie), `archivo_gestion_years`/`archivo_central_years` (retención en dos fases), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`, `estado`, `valid_to`, `acto_administrativo`, `acta_comite`, `fecha_aprobacion_comite`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`, `fecha_publicacion`, `supersede_a`, `created_by`. `POST` crea siempre **`version=1` en `borrador`** (`estado` no es settable por el cliente). `PATCH` es **append-only** (D1): solo aplicable sobre `borrador` — cualquier otro estado responde `409 trd_version_inmutable`, salvo que el único campo enviado sea `pdfa_profile` (política de preservación técnica, editable en cualquier estado). `is_active` ya no se acepta en `PATCH` (la baja de una versión es `derogar`, abajo).
- `POST /api/v1/trd/{code}/versiones` — clona como fila **nueva** `version+1` en `borrador` la versión **vigente** (`convalidada`/`migrada`) del código; si el código no tiene vigente (todas sus versiones `derogada`), cae a la **última versión por número, cualquier estado** (migración 038 — un código derogado sin sucesora nunca queda sin camino de vuelta). `404 trd_code_not_found` solo si el código nunca existió; `409 trd_version_en_tramite` si ya hay una versión `borrador`/`aprobada` en trámite.
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **calendario de retención**: `fin_archivo_gestion`, `fin_archivo_central` y disposición final (`disposition_code`/`disposition_label`) derivados de la serie y la fecha de cierre, más la **procedencia del instrumento** (`serie_version`, `serie_estado`, `acto_administrativo`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`) — se declara tal cual está en la fila, nunca se afirma una convalidación que no ocurrió (RF-FIR-15).
- `GET /api/v1/trd/{code}/versiones` — **(ADR-026, gemelo declarado por ADR-025 y nunca implementado)** historia completa (cualquier estado) de un código, más reciente primero; lista vacía si el código nunca existió (no es un `404`).

**Circuito de convalidación (ADR-025 D2/D6, Increment 2 + migración 038)** — máquina de estados aislada en el service layer, validada antes de tocar BD (`409 trd_transicion_invalida` en toda transición fuera de las listadas):

- `POST /api/v1/trd/{code}/versiones/{version}/aprobar` `{acta_comite, fecha_aprobacion_comite}` — `borrador → aprobada` (aprobación del Comité Institucional de Gestión y Desempeño; ambos campos **obligatorios**, migración 038 — antes el endpoint no recibía body y el sistema no podía registrar el primer acto del circuito). Condición necesaria y **no suficiente** para gobernar un cierre.
- `POST /api/v1/trd/{code}/versiones/{version}/devolver` `{motivo}` — **nuevo (038)**: `aprobada → borrador`, con `motivo` obligatorio. Vía honesta de "devolución con observaciones" (Ac. AGN 001/2024) — antes la única salida de `aprobada` era `convalidar`, que exige declarar un acto que tal vez no existe todavía. Limpia `acta_comite`/`fecha_aprobacion_comite` (la aprobación queda VOID).
- `POST /api/v1/trd/{code}/versiones/{version}/convalidar` `{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}` — **una misma acción, dos orígenes**: (a) desde `aprobada` (circuito normal, exige que `acta_comite`/`fecha_aprobacion_comite` ya consten — `409 trd_aprobacion_comite_requerida` si no, migración 038): `aprobada → convalidada`, y **en la misma transacción** deroga la versión previamente vigente del mismo código (si existía: `valid_to=hoy`, `estado='derogada'`, `is_active=false`; si la derogación pierde una carrera concurrente, aborta `409` en vez de dejar FK huérfanas) y **re-apunta** (`UPDATE`, no clona, colisión-segura — una clave que ya existe en la versión nueva se **omite**, nunca revienta la tx) las FK de `tipos_documentales`/`expediente_metadata_templates` de la versión derogada a la nueva; (b) desde `migrada` (**ratificación D6**): `migrada → convalidada` **sin crear versión nueva ni derogar nada** — la misma fila gana los campos del acto, y se limpian `trd_revision_requerida`/`trd_revision_motivo` de los expedientes `closed`/`transferred` amarrados a ella cuyo motivo sea **exactamente** `cerrado_bajo_serie_migrada` (otros motivos, p. ej. `serie_no_resoluble`, no se resuelven así). `acto_administrativo`/`fecha_convalidacion`/`instancia_convalidante` son obligatorios en el body (`422` si faltan; el CHECK `trd_series_convalidada_acto_check` los exige también a nivel BD); `rusd_radicado`/`fecha_publicacion` opcionales. Una vez `convalidada`, el trigger de la 038 blinda a nivel de MOTOR el acto (`acto_administrativo`/`fecha_convalidacion`/`instancia_convalidante` inmutables, `estado` solo puede ir a `derogada`).
- `POST /api/v1/trd/{code}/versiones/{version}/registrar-rusd` `{rusd_radicado?, fecha_publicacion?}` (al menos uno) — **nuevo (038)**: registra la inscripción RUSD y/o la fecha de publicación sobre una versión `convalidada` — la norma da hasta 30 días hábiles DESPUÉS de la convalidación para completarlos, y antes de la 038 el único momento en que el sistema los aceptaba era `convalidar`. Cada campo solo se puede rellenar **una vez** (NULL → valor); `409 trd_rusd_ya_registrado`/`trd_fecha_publicacion_ya_registrada` si ya tenía valor — el trigger de la 038 lo blinda también a nivel de motor.
- `POST /api/v1/trd/{code}/versiones/{version}/derogar` — `convalidada`/`migrada` → `derogada` (baja explícita del instrumento, **sin sucesora**; `valid_to=hoy`, `is_active=false`). **Decisión (038, hallazgo #3 del dictamen):** bloqueada con `409 trd_derogacion_bloqueada_expedientes_abiertos` si algún expediente `open` está clasificado bajo el código — derogar el único instrumento vigente los dejaría sin ninguno que gobierne su cierre. No exige una sucesora ya convalidada (ese requisito bloquearía el caso de uso legítimo de retirar una serie que ya no produce documentos); el código nunca queda sin camino de vuelta gracias al fallback de `POST /versiones` de arriba. No afecta expedientes ya cerrados bajo esta versión (snapshot congelado, D3/D4).
- `GET /api/v1/trd/revision-pendiente` (`?page=1&size=50`, `X-Total-Count`) — informe de expedientes con `trd_revision_requerida=true` (cualquier motivo: `cerrado_bajo_serie_migrada`, `cierre_sin_snapshot_de_retencion`, `serie_no_resoluble`), paginado. **No-read-up (RF-SEG-08, corregido en la 038):** filtra por `nivel_seguridad ≤ clearance` del llamante en el `COUNT` y en la página — antes cualquier portador de `USUA_PERM_TRD` (permiso ORTOGONAL al clearance) enumeraba RESERVADA/CLASIFICADA; deja además traza agregada de consulta (RF-BUS-10) cuando `X-User-Id`/`X-Canal` llegan. **Pantalla**: tercera pestaña de `/admin/trd` («Revisión pendiente»), que distingue los tres motivos por lo que los resuelve — solo `cerrado_bajo_serie_migrada` se limpia al convalidar la serie; los otros dos no tienen vía automática y la interfaz lo declara en vez de ofrecer una acción inútil.

#### `/api/v1/tvd` — Tablas de Valoración Documental de fondo acumulado (E04, [ADR-026](adr/ADR-026-tvd-fondo-acumulado.md), Increment 1)

La TVD es **el mismo instrumento que la TRD** (Ac. AGN 001/2024, que compila el 004/2019: mismo circuito Comité → Consejo → RUSD → publicación), modelado como **discriminador `tipo_instrumento ∈ {TRD, TVD}`** sobre la misma tabla `trd_series` — no hay tabla propia (ADR-026 D1: bifurcar el seam `expedientes.trd_serie_id`, que alimenta `retain_until` WORM COMPLIANCE en siete puntos irreversibles, es el diseño que el ADR descarta). Rutas **espejo 1:1** de `/api/v1/trd`, mismo permiso **`USUA_PERM_TRD`** (misma potestad archivística, mismo Comité — crear un permiso separado exigiría una migración de RBAC por una distinción que la norma no hace), `tipo_instrumento` **fijado por el router, nunca aceptado del cliente**.

**Qué distingue a la TVD** (campos propios, todos **obligatorios** al crear y **NULL por CHECK** en toda TRD — `trd_series_tvd_forma_check`/`trd_series_trd_forma_check`, migración 039): `fondo_nombre` (la entidad productora, a menudo **extinta**), `fecha_extrema_inicial`/`fecha_extrema_final` (fechas extremas del fondo — la TVD no tiene fase de **archivo de gestión**, `archivo_gestion_years` queda siempre `NULL`/`0`), `justificacion_valoracion` (el **objeto** del instrumento — el fundamento que se citaría en un acta de eliminación, RF-FIR-15). El espacio de `code` es **único y compartido** con la TRD (Capa 1 del ADR): registrar una TVD con un código que ya usa una TRD responde `409 instrumento_code_en_uso` indicando el tipo del ocupante.

**Propiedad que define el Increment 1: una TVD todavía no gobierna ningún expediente.** El trigger de amarre (`trg_expedientes_pin_tipo`) rechaza en el motor **cualquier** `trd_serie_id`/`trd_id` de expediente que apunte a una fila TVD — riesgo WORM **cero**, garantizado por construcción, no por omisión. La entidad puede registrar, aprobar, convalidar, inscribir en RUSD y publicar su TVD (que era el bloqueo original) sin que exista todavía ninguna ruta por la que gobierne un cálculo de retención. El Increment 2 (fuera de este incremento) relajará el trigger a `expedientes.origen ↔ tipo_instrumento` y dará a `compute_retention` una fecha base explícita (`fecha_extrema_final`, nunca `closed_at` — D4 del ADR).

- `POST /api/v1/tvd` `{code, name, description?, parent_id?, disposition, total_retention, archivo_central_years?, pdfa_profile?, fondo_nombre, fecha_extrema_inicial, fecha_extrema_final, justificacion_valoracion}` — crea la agrupación **versión 1 en `borrador`**. **No** acepta `archivo_gestion_years`, `estado` ni `tipo_instrumento`. `409 instrumento_code_en_uso` si el código ya pertenece a otro instrumento.
- `GET /api/v1/tvd?page=&size=&is_active=` — lee **`v_tvd_agrupaciones`** (nunca la tabla base): el aislamiento entre TRD/TVD vive en el `FROM`, no en un `WHERE` olvidable. `X-Total-Count`.
- `GET /api/v1/tvd/{id}` — `404` si el id existe pero es TRD (lee la vista: no "se filtra", directamente no está).
- `PATCH /api/v1/tvd/{id}` — solo `borrador` (+ `pdfa_profile` en cualquier estado), mismo gate append-only que TRD (`409 trd_version_inmutable`).
- `POST /api/v1/tvd/{code}/versiones` — clona la vigente como `borrador` `version+1`.
- `GET /api/v1/tvd/{code}/versiones` — historia completa, más reciente primero.
- `POST /api/v1/tvd/{code}/versiones/{version}/aprobar` `{acta_comite, fecha_aprobacion_comite}` — `borrador → aprobada`.
- `POST /api/v1/tvd/{code}/versiones/{version}/devolver` `{motivo}` — `aprobada → borrador`.
- `POST /api/v1/tvd/{code}/versiones/{version}/convalidar` `{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}` — `aprobada → convalidada` (deroga la vigente previa del mismo código en la misma transacción, re-apunta FK huérfanas). **A diferencia de TRD, el único origen es `aprobada`**: una TVD nunca alcanza `migrada` (nace en `borrador`, la ratificación D6 de ADR-025 no aplica — la población TVD nace vacía).
- `POST /api/v1/tvd/{code}/versiones/{version}/registrar-rusd` `{rusd_radicado?, fecha_publicacion?}`.
- `POST /api/v1/tvd/{code}/versiones/{version}/derogar` — `convalidada → derogada`.

Nombres de asiento de auditoría **distintos** a propósito (`archive.tvd_*`, objeto `tvd_agrupacion`): en `audit_log` la decisión es la contraria a la del modelo de datos — quien filtra `archive.trd_version_convalidada` no debe tener que acordarse de mirar además un campo del payload.

#### `POST /api/v1/expedientes/{id}/radicados/batch` (inclusión masiva)

Vincula varios radicados de una vez `{radicados:[{radicado_id, tracking_number, content_hash?, formato?, tamano_bytes?, folios?, dependencia_productora_codigo?, dependencia_productora_nombre?}]}` (hasta 500; idempotente, omite los ya vinculados — a los ya vinculados no les actualiza metadatos); regenera el índice una sola vez. Los campos RT-15 (`formato`/`tamano_bytes`/`folios`) y la **dependencia productora** (código+nombre, v4) son opcionales pero **código y nombre deben ir juntos o ninguno** (validación Pydantic `422`, espejo de `RadicadoLink`). Devuelve `{vinculados, omitidos, total}`. `409` si el expediente no está `open` (para un llamante sin clearance de lectura sobre un expediente clasificado, `vinculados`/`omitidos` llegan `null` y el `409` se sustituye por el ack acotado — ver la nota de control de acceso arriba).

#### Ciclo de vida del expediente (E02)

- `POST /api/v1/expedientes/{id}/close` — cierra el expediente (`open→closed`), materializa la disposición TRD en `disposition` y **genera+firma (XAdES-B) la versión final del índice electrónico** vía signature-service (E06 Inc.1). El sellado es **best-effort y no bloquea el cierre**: si la firma se obtiene deja `expediente_indice.estado='firmado'`; si falla (sello no aprovisionado, servicio inalcanzable) deja **`estado='pendiente_firma'`** y emite el audit `archive.indice_firma_pendiente`. El audit de cierre incluye `indice_estado`/`firma_id`. **Respuesta**: `ExpedienteDisposicionResult` **acotado** (`id`, `code`, `status`, `disposition`, `closed_at`, `transferred_at`) — **no** el expediente completo: la disposición es ortogonal al clearance de lectura (RF-SEG-08 Opción B), así que la respuesta no expone `metadata`/`description`/`radicados`/`opened_by`, y un usuario con autoridad de disposición (`crud≥3`) pero sin clearance sobre un CLASIFICADA recibe **200** (antes: 404 espurio pese a cerrar con éxito).
- `POST /api/v1/expedientes/{id}/indice/firmar` — **reintenta** el sellado XAdES-B de un índice `pendiente_firma` (E06 Inc.1). Idempotente: `409` si ya `firmado`, `503` si el sellado sigue sin obtenerse. Requiere `USUA_PERM_EXPEDIENTE` **+ `PERM_FIRMA`** y `X-User-Id` (400 si el UUID es malformado).
- `POST /api/v1/expedientes/{id}/transfer` — transfiere (`closed→transferred`). `409` si la transición es inválida **o si el índice no está `firmado`** (un índice `pendiente_firma` bloquea la transferencia — Acuerdo AGN 001/2024 art. 4.3.2.4). El mismo bloqueo aplica en el `PATCH` genérico con `status=transferred` y en la recepción de transferencias (E12). **Respuesta**: `ExpedienteDisposicionResult` acotado (igual que `/close`), no el expediente completo.
- `GET /api/v1/expedientes/{id}/eventos` — **hoja de ruta append-only del expediente** (E02 §9, RF-EXP-09): lista cronológica de `{id, accion, actor, comentario, detalles, ts}` con `accion` ∈ `abierto|radicado_vinculado|radicado_excluido|cerrado|transferido|...`. **Control de acceso (Canal B / RF-SEG-08):** visible a quien pueda **leer** el expediente, acotada por su clearance (no read-up); no requiere admin. Distinta de `audit_log` (auditoría forense) y de `/unidades/{id}/movimientos` (movimientos físicos, E17).
- `GET /api/v1/expedientes/{id}/foliado` — foliado: radicados ordenados con `folio` secuencial (`{expediente_id, code, total_folios, items[]}`). (El **índice electrónico** normativo es E15, abajo.)

#### Índice electrónico (E15, Acuerdo AGN 001/2024)

> **Perfil v4 "conforme completo" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md); XSD `schemas/indice-v4.xsd`):** los índices nuevos se generan en `urn:orpycamcp:indice:v4`, que **cierra los 4 residuales de v3**. **Por documento**: metadatos RT-15 (`<formato>`/`<tamano>`/`<folios>`/`<fechaIncorporacion>`), **`<orden>` estable** (inmutable), **`<dependenciaProductora codigo="N">`** (aportada al vincular; se **omite** en filas legacy sin dato) y **`<fechaDeclaracion>`** (= `added_at`, declaración ≡ incorporación). **En cabecera**: `<contexto>` (`fechaPrimerUso` + serie/subserie por nombre), **`<listaControlAcceso>`** con `<nivelSeguridad>` + **`<politicaAcceso modelo="no-read-up" requiere="USUA_PERM_EXPEDIENTE" clearanceMinimo="N">`** (política estable/portable — la **matriz RBAC viva NO se incrusta**, se consulta por endpoint), **`<pistaAuditoria registro="public.audit_log" objetoRef cadenaVerificable>`** (atesta la pista, **no** incrusta entradas vivas) y **`<conformidad perfil="v4" productoraPresente pistaAccesoDesde>`** (marcador per-instancia; `productoraPresente="false"` sobre conjunto vacío). El **content_hash v4 canonicaliza el árbol completo** (v3 solo `<documentos>`). La **exclusión** de un radicado es **soft-delete** (`<documento excluido="true">` con ordinal + `<fechaExclusion>`/`<causalExclusion>` → **hueco trazable**, AGN 001/2024 art. 4.3.2.3). Los índices v1/v2/v3/v4 **ya firmados quedan inmutables** (`verify` agnóstico por versión). **Label**: "conforme completo — **aplicabilidad prospectiva**" (instancias legacy pueden omitir productora y su pista de acceso inicia en la instrumentación; cada índice lo auto-declara en `<conformidad>` — RF-FIR-15, no sobre-declarar). **Todas** las lecturas del índice exigen `USUA_PERM_EXPEDIENTE` + clearance (no-read-up, RF-SEG-08) y **dejan asiento de acceso** en `audit_log` (`archive.indice_consultado`, solo actor real + canal `api|ui|mcp`).

- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — índice vigente o por versión: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`. Cada item incluye `formato`, `tamano_bytes`, `folios`, `fecha_incorporacion` (nulos si no se declararon al vincular).
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — cuerpo XML (`application/xml`, namespace `urn:orpycamcp:indice:v4` en índices nuevos; `v1`/`v2`/`v3` en los ya firmados).
- `GET /api/v1/expedientes/{id}/export.zip` — **exportación del expediente a ZIP** (E02/export, Ley 594/2000 art. 19). Copia de CONSULTA/ENTREGA (distinta del AIP de preservación E10 y de la transferencia FUID E12). El ZIP contiene: `indice-electronico.xml` (E15, si está firmado — best-effort), `manifiesto.csv` (radicados **vinculados y excluidos** —estos con su causal, sin bytes—, con `orden_documental`/`folios`/`formato`/`dependencia_productora`/`fecha`), `documentos/{tracking}/{idx}-{filename}` (bytes de los anexos), `LEEME.txt` (leyenda de alcance) y `checksums.txt` (SHA-256 por miembro, para validar integridad). Requiere `USUA_PERM_EXPEDIENTE`. **No-read-up POR-RADICADO** (RF-SEG-08): `404` si el expediente supera el clearance del llamante, y **los radicados individuales por encima del clearance se omiten por completo** (ni su existencia se enumera en el manifiesto) — no basta el gate a nivel de expediente. archive orquesta; el ensamblado de bytes lo hace storage-service (endpoint interno D-02, inalcanzable desde el gateway) sin persistir nada; `arcname` saneado anti zip-slip. La exportación deja una entrada de auditoría agregada (`archive.expediente_exportado`, RF-BUS-10). _Deuda: PDF combinado, streaming para expedientes grandes, `asunto`/`tipo documental` en el manifiesto._
- `GET /api/v1/expedientes/{id}/historial-acceso` (`?page&size`, E15 v4 R3) — **pista de acceso** del expediente: lee `public.audit_log` (solo lectura, filtro `tenant_slug` explícito) filtrado por `object_ref = code` y `object_type ∈ (expediente, expediente_indice)`, orden `ts DESC`, con `X-Total-Count`. Devuelve `{ts, actor, canal, action, ...}`. Requiere `USUA_PERM_EXPEDIENTE` + no-read-up (**404** si el expediente supera el clearance del llamante, no 403 — no filtra existencia).
- `GET /api/v1/expedientes/{id}/roles-autorizados` (E15 v4 R4) — **snapshot vivo** (no firmado) de la matriz de acceso: grupos que tienen `USUA_PERM_EXPEDIENTE` (`crud≥1`) **y** `role_clearance.max_level ≥ nivel_seguridad` del expediente, como `[{grupo, clearanceMax, crud}]` ordenado por nombre. Mismo gate (`USUA_PERM_EXPEDIENTE` + no-read-up). Es el dato que el índice firmado deliberadamente **no** incrusta (evita churn del artefacto probatorio y fuga de topología IAM en transferencias AGN).
- `GET /api/v1/expedientes/{id}/indice/versions` — historial de versiones (append-only) con `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifica integridad: contrasta el `valor_huella` del índice contra el hash actual de cada documento **y** re-hashea el XML almacenado contra `xml_sha256` (`huella_indice_ok` — detecta alteración del propio XML del índice, incluso no firmado). Con firma XAdES-B descarga el XML firmado desde MinIO y lo verifica contra signature-service — el bloque `firma` incluye `{presente, valida, nivel_conformidad, acreditado, motivo}` (`motivo='signed_xml_unavailable'` → `valida=false`). `valido` es la conjunción de fixity por documento + `huella_indice_ok` + firma (si presente). Requiere `USUA_PERM_EXPEDIENTE`.
- `GET /api/v1/expedientes/indices/pendientes-firma` — lista operacional (paginada, `X-Total-Count`) de los índices en estado `pendiente_firma` de expedientes `closed`/`transferred`, con sus contadores de reconciliación (`reconcile_intentos`, `reconcile_agotado`, `reconcile_ultimo_error`, versión, `expediente_code`) — visibilidad directa para el archivista de los índices que aún no se sellaron, **incluidos los `reconcile_agotado=true`** que exigen intervención humana (montar el sello + retry manual). **No-read-up**: filtra por `nivel_seguridad <= clearance` del llamante (en `COUNT` y filas), un clearance bajo no ve índices de expedientes clasificados. Requiere `USUA_PERM_EXPEDIENTE` (E15/E06 Inc.8).
- `POST /api/v1/expedientes/{id}/indice/firmar` — reintento **manual** del sellado XAdES del índice `pendiente_firma` (exige `USUA_PERM_EXPEDIENTE`+`PERM_FIRMA`, atribuido al humano). `409 indice_ya_firmado` si ya está firmado (idempotente), `503 index_signing_failed` si sigue sin poder sellarse. Un retry manual **re-arma** el backoff de reconciliación (`reconcile_agotado`→false) para que el job automático reanude tras corregir la causa raíz. El asiento `archive.indice_firmado` lleva `origen="manual"`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenera (nueva versión si cambió el conjunto o los metadatos; `{unchanged:true}` si idéntico). **`409 expediente_not_open`** si el expediente no está `open`. Se crea la **versión 0** automáticamente al crear el expediente. Requiere `USUA_PERM_EXPEDIENTE`. Cada regeneración deja un asiento `archive.indice_regenerated` (con `xsd_version`) en `audit_log`.
- Al vincular un radicado (`POST /api/v1/expedientes/{id}/radicados`) se puede aportar `content_hash` (SHA-256, `valor_huella`), los metadatos RT-15 **`formato`** (MIME), **`tamano_bytes`** y **`folios`** (opcionales, `ge=0`) y, en **v4**, la **`dependencia_productora_codigo`** (INTEGER) + **`dependencia_productora_nombre`** (texto) — opcionales pero **juntos o ninguno** (`422` el caso parcial). Se persiste un snapshot inmutable por versión del índice, de modo que `GET /indice?version=N` muestra la productora tal como estaba al generarse esa versión. Vincular/excluir regenera el índice automáticamente; sobre un expediente no `open` devuelve `409`.

#### `GET /api/v1/expedientes/search` (búsqueda FTS, E09)

Búsqueda full-text de expedientes (ADR-014). `q` opcional (`tsvector` sobre `code`/`name`/`description` + ranking `ts_rank`); filtros `?status=&date_from=&date_to=&meta.<campo>=`. Cabecera `X-Total-Count`. Sin texto = consulta filtrada ordenada por `opened_at`. **Control de acceso (RF-SEG-08 / RF-BUS-04):** solo se devuelven expedientes con `nivel_seguridad ≤` el _clearance_ del llamante (_fail-closed_ a PUBLICA). El nivel se fija al crear el expediente (`POST /api/v1/expedientes`, campo `nivel_seguridad`, default 1).

#### `/api/v1/tipos-documentales` (3.er nivel TRD, RF-MET-07)

CRUD del catálogo de tipos documentales (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Campos: `code`, `nombre`, `trd_serie_id` (serie/subserie), `obligatorio`, `metadata_template` (plantilla asociada). Filtros `?trd_serie_id=&activo=`. `409` si la `(serie, code)` ya existe.

#### `GET /api/v1/expedientes`

Listar con filtros `?status=open&page=1&size=20`.

#### `GET /api/v1/expedientes/{id}`

Obtener por UUID. Incluye lista de radicados vinculados. **Control de acceso (RF-SEG-08):** si el expediente supera el _clearance_ del llamante, responde `404`.

#### `PATCH /api/v1/expedientes/{id}`

Actualizar `name`, `description`, `trd_serie_id`, `status`.
Transiciones válidas: `open → closed → transferred`. No se puede reabrir.

#### `POST /api/v1/expedientes/{id}/radicados`

Vincular radicado al expediente.

```json
{ "radicado_id": "uuid", "tracking_number": "2024-ICETEX-E-000001" }
```

#### `DELETE /api/v1/expedientes/{id}/radicados/{radicado_id}` (`?causal=…`)

Excluir (desvincular) radicado. Es **soft-delete** (E15 v3): marca `estado='excluido'` (+ `excluido_at`, `excluido_causal` del query param opcional) — **no** borra la fila; el `orden_documental` no se reutiliza y el índice deja el hueco trazable. `204`. Exige `USUA_PERM_EXPEDIENTE` con `crud≥3` (disposición). `404` si no estaba vinculado.

### TRD

> Ver la sección **"TRD/CCD — la TRD como instrumento convalidado"** más arriba (§ archive-service) para el contrato completo y actualizado (ADR-025: versionado append-only, máquina de estados, circuito de convalidación). Los ejemplos de aquí quedan solo como referencia rápida de forma del body.

#### `POST /api/v1/trd`

Crear serie documental (siempre `version=1`, `estado='borrador'`).

```json
{
  "code": "100",
  "name": "Contratos",
  "retention_years": 5,
  "total_retention": 10,
  "disposition": "conserve"
}
```

#### `GET /api/v1/trd`

Listar series. Filtro: `?is_active=true&page=1&size=50`.

#### `GET /api/v1/trd/{id}`

Obtener serie por UUID.

#### `PATCH /api/v1/trd/{id}`

Actualizar serie — solo aplicable si `estado='borrador'` (append-only, ADR-025 D1); en cualquier otro estado usar `POST /trd/{code}/versiones`.

---

## notification-service — puerto 8007

### `POST /api/v1/notifications/send`

Envío de notificación por email. Responde `202` (aceptado para envío async). El historial se persiste en la BD del `tenant_slug` indicado.

```json
{
  "recipient_email": "juridica@icetex.gov.co",
  "subject": "Nuevo radicado",
  "body": "...",
  "tenant_slug": "icetex",
  "sensible": false
}
```

**Endpoint servicio-a-servicio (D-02):** es la vía por la que signature-service despacha los OTP de 2FA (los demás envíos entran por el stream de eventos, no por HTTP). Exige `X-Internal-Token` (secreto compartido `INTERNAL_SERVICE_TOKEN`) — sin él `401`, inválido `403`. **Fail-closed:** sin el token configurado en el despliegue, rechaza toda llamada. El sello impide que un usuario externo, vía el gateway, envíe correo arbitrario con la identidad SMTP de la institución.

- `sensible` (opcional, default `false`): marca el correo como **sensible** (p. ej. el OTP del 2FA de firma, RF-FIR-13). Con `sensible=true` el correo se **envía por SMTP con el body real** pero el historial persiste el `body` **redactado** (`[contenido sensible omitido]`) y un `last_error` genérico — el secreto nunca queda en BD/backups. Todo emisor de correos con secretos **debe** fijar este flag.

### `GET /api/v1/notifications/history`

Historial de notificaciones del tenant (persistido en BD por institución). Requiere `X-Tenant-Slug`. Query: `?limit=20`. **Exige `USUA_PERM_ADMIN`:** devuelve TODAS las notificaciones del tenant sin filtro por usuario (asuntos/cuerpos de correos ajenos), así que es una vista administrativa/auditoría, no un buzón personal.

### `GET /api/v1/notifications/{id}`

Obtener notificación por UUID. Requiere `X-Tenant-Slug` y `USUA_PERM_ADMIN` (misma razón que el historial). `404` si no existe.

### Firma electrónica — signature-service, puerto 8008 (E06, ADR-016)

Proveedor **enchufable** (`app/providers/`): `nativa` (HMAC hash+identidad, default para todo objeto) y **`xades_local`** (XAdES-B enveloped real vía `signxml`, activo solo para el **índice electrónico**). Selección: `xades_local` cuando `objeto_tipo=="indice"` **y** `SIGNER_PROVIDER=="xades_local"` (default; kill-switch `SIGNER_PROVIDER=nativa`); cualquier otro caso → `nativa`. Ver [ADR-016 addendum Inc.1](adr/ADR-016-firma-electronica.md#addendum--incremento-1-2026-07-02-xades-b-del-índice-electrónico--sello-institucional).

- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version?}` — firma el payload y persiste la firma. Con `objeto_tipo="indice"` produce **XAdES-T** (XAdES-B con el sello institucional del tenant **+ sello de tiempo RFC 3161** de una TSA, E06 Inc.4) y, si `SIGNER_LT_ENABLED=true` **y** el sello del tenant trae material de CA (`ca.crt`+`crl.der`), escala a **XAdES-LTA** (E06 Inc.5): añade `xades:CertificateValues` (solo la CA) + `xades:RevocationValues` (CRL) + `xadesv141:ArchiveTimeStamp`. Con `SIGNER_OCSP_ENABLED=true` (default `false`) **y** el par de responder OCSP montado (`ocsp.crt`+`ocsp.key`, per-tenant junto al sello), añade además una respuesta **OCSP stapled (RFC 6960)** como `xades:OCSPValues/EncapsulatedOCSPValue` **hermana del CRL** dentro del mismo `RevocationValues` (E06 Inc.7) — firmada por un **responder delegado** emitido offline por la CA del tenant, con `certStatus` derivado de la CRL, y queda cubierta por el `ArchiveTimeStamp`. Exige `PERM_FIRMA` + `X-User-Id` (el firmante **nunca** sale del cuerpo, RF-FIR-11). Devuelve `{firma_id, payload_sha256, provider, minio_key_firmado?, xades_level?, nivel_conformidad?, tipo_firma?, certificado_fingerprint?, acreditado, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ...}`. `xades_level` ∈ `XAdES-B|XAdES-T|XAdES-LT|XAdES-LTA` según lo alcanzado (nunca lo intencionado; OCSP **no** cambia el nivel, enriquece la evidencia de revocación); **sin material CA la firma se queda en XAdES-T** y deja audit `firma.lt_downgrade_no_ca`; **con OCSP habilitado pero sin par de responder** deja audit `firma.ocsp_downgrade_no_responder` (se queda en LT/LTA con CRL sola). `acreditado` **siempre `false`** (CA/TSA/responder OCSP de dev, no ONAC — material de validación **verificable, no acreditado**, RF-FIR-15; `nivel_conformidad` ∈ `xades_b|xades_lt|xades_lta_no_acreditado`, revocación de dev marcada con `revocation_provenance="dev"`). `503 signing_seal_unavailable` si falta el sello; **`503 timestamp_authority_unavailable`** si la TSA no está disponible; **`503 lt_material_required`** si `SIGNER_LT_REQUIRE=true` y falta material CA; **`503 ocsp_material_required`** si `SIGNER_OCSP_REQUIRE=true` y no se produjo respuesta OCSP (falta material de CA o de responder); `503 signed_index_upload_failed` si storage rechaza la subida.
- `POST /api/v1/signature/sign-personal` `{payload_xml, objeto_tipo, objeto_id?, totp_code, firmante_id?}` — **firma personal PKI de un payload XML arbitrario** con el certificado del actor autenticado (E17/F4 Inc.1; primitivo `personal_signing.sign_personal_xml` reutilizado por la cadena de radicados y por la firma del acta de transferencia). Autenticado como **usuario** (`get_actor` + `PERM_FIRMA`, `X-User-Id`/JWT — **no** `X-Internal-Token`: lleva identidad + TOTP del firmante real, a diferencia de `/internal/sign-indice`). **ASSERT `firmante_id == user(JWT)`**: si el body trae `firmante_id` y difiere del actor derivado del token → `403 firmante_no_coincide` (no suplantable por el body; la fila persistida usa siempre el actor del JWT). Arma el XAdES con clave RSA efímera + el cert público real, pide a auth-service que firme el digest de `SignedInfo` **consumiendo el TOTP atómicamente** (split firma-remota), y sube el XML firmado a MinIO con nombre determinista que incluye el `firma_id` (patrón H-H, sin sobrescritura por carrera). Devuelve el `FirmaResponse` estándar (`firma_id`, `minio_key_firmado`, `xades_level`, `provider="xades_personal"`, `acreditado` **siempre `false`**, `nivel_conformidad='xades_personal_no_acreditado'`). **El gate no-read-up ya no depende de `objeto_tipo`**: `objeto_tipo` es texto libre aquí (tiene que serlo — por esta ruta pasan las actas institucionales), y mientras el gate colgaba de `== "radicado"`, declarar cualquier otro valor sobre el identificador de un radicado CLASIFICADA lo evaluaba contra nivel 1 y lo saltaba. Ahora el nivel se resuelve por el `objeto_id` contra `radicados`/`expedientes`, sea cual sea el tipo declarado; si no resuelve, es un objeto sin nivel propio (acta, borrador) y queda en PUBLICA, y para esos el no-read-up lo sigue aplicando **el servicio mediador** (archive) **antes** de llamar — verificado que una llamada directa no es bypass (solo firma el payload que el propio caller aporta, no inyecta en las tablas de dominio). `4xx` si el TOTP es inválido; `503` si el canal de auth cae.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifica. **Firmas `xades_local`**: `payload_xml` debe ser el **XML firmado completo** (el de `minio_key_firmado`); valida criptografía + cobertura de la raíz + vigencia del cert + huella del sello + coincidencia con `signed_xml_sha256`; **XAdES-T** valida además el token RFC 3161 (cubre el `ds:SignatureValue`, firma CMS, cert TSA **atado a `firma.tsa_cert_fingerprint`**); **XAdES-LT/LTA** valida además (fail-closed) la **cadena `seal→CA`** (anclada a la CA **del tenant** vía pinning `ca_fingerprint` — un sello de la CA de otro tenant falla), la **firma y no-revocación de la CRL** (serial del sello no revocado), y el **`ArchiveTimeStamp`** sobre el conjunto cubierto determinista; `crl_next_update` se expone sin hard-fail si caducó (no acreditado). Si la firma trae **`xades:OCSPValues`** (E06 Inc.7), valida además (fail-closed) la respuesta **OCSP (RFC 6960)**: `responseStatus=successful`, responder emitido por la CA del tenant + vigencia, EKU `OCSPSigning` + `id-pkix-ocsp-nocheck`, firma del `BasicOCSPResponse`, **CertID atado al sello** (serial + issuer hashes), `certStatus=good`, y **pinning** del fingerprint del responder contra `firma.ocsp_responder_fingerprint` (sin él → fail-closed); su ausencia es retrocompat (LT válida sobre CRL sola). **Firmas `xades_personal`** (firma personal PKI por-usuario, E06/E17 Inc.1-2): valida cripto + cobertura + vigencia + huella del leaf; si la firma embebe la sub-CA (`xades:CertificateValues`, Inc.2) ancla además la **cadena leaf→sub-CA de usuarios del tenant** con pinning `user_ca_fingerprint` (rama separada de la del sello — no exige CRL embebida); y **consulta la revocación en línea** (estado en BD, sin caché) → `valida=false` (`motivo=certificado_revocado`) si el cert fue revocado. El veredicto se desglosa: `firma_criptograficamente_valida` (cripto+cadena a secas) y `revocacion_status ∈ {vigente, revocado, no_verificable, desconocido}`; `valida = cripto AND revocacion_status=='vigente'`; un canal de revocación caído → `valida=false`/`motivo=revocacion_no_verificable` pero `firma_criptograficamente_valida=true` (default fail-closed, `verify_personal_revocation_require`). Firmas `xades_personal` de Inc.1 (sin `CertificateValues`) verifican como XAdES-B/T (grandfather) pero **siguen siendo revocables** por identidad del cert. **Firmas `nativa`**: `payload_xml` es el original en claro. Devuelve `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?, sello_tiempo_valido?, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ocsp_cert_status?, firma_criptograficamente_valida?, revocacion_status?}` (en `/verify` el campo **autoritativo** del nivel realmente verificado es `xades_level`, derivado del resultado). Endpoint **público** (sin auth). `404` si la firma no existe.
- `GET /api/v1/signature/firmas/{firma_id}` — metadatos de una firma (mismos campos que `sign-xml`, sin `certificado_pem` completo). Público. `404` si no existe.
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/signature/internal/sign-indice` `{payload_xml, objeto_id, version, objeto_tipo?, indice_id?}` — sella un objeto XAdES-elegible **atribuido al principal de sistema `SYSTEM_RECONCILER_ID`** (no a un humano), para los **jobs de reconciliación** de archive-service (índice E15/E06 Inc.8, y acta de transferencia H-H). Autenticado **solo** por `X-Internal-Token` (fail-closed: settings vacío → 503, header ausente → 401, errado → 403); **no** exige `PERM_FIRMA` ni `X-User-Id`. `objeto_tipo` (default `"indice"`) se **valida contra el allow-set `{"indice","acta_transferencia"}`** (`422` fuera de él) → solo esos dos tipos producen el sello institucional del tenant (que NO deriva del actor); **estructuralmente no puede firmar la cadena personal**. El asiento `firma.creada` lleva `origen="reconciliation"`. La firma personal (`/requests/{id}/sign`, `/sign-xml`) NUNCA acepta este token.

**Cadena de firma (bandeja del firmante, RF-FIR-04).** Todas exigen `PERM_FIRMA`; el firmante se deriva del JWT (nunca del cuerpo, RF-FIR-11). El turno activo es el de menor `orden` aún pendiente. La firma personal exige **2FA** (RF-FIR-13): **OTP-correo** (E06 Inc.2) o, más fuerte, **TOTP RFC 6238** (posesión de dispositivo, E06 Inc.6, ver [ADR-016 addendum Inc.6](adr/ADR-016-firma-electronica.md#addendum--incremento-6-2026-07-06-2fa-por-totp-rfc-6238-en-la-firma-personal)). El secreto TOTP vive en **auth-service** (cifrado AESGCM); ver `POST /api/v1/auth/me/totp/*` abajo. **Formato de la firma personal (E06/E17 F4, Inc.1)**: si el firmante tiene una **clave de firma PKI activa** (ver `/me/signing-key/*`), la cadena produce una firma **XAdES-B/T por-usuario** con su propio certificado (verificable por terceros vía `/verify`, `provider=xades_personal`); si no, degrada a la **firma HMAC nativa** (`provider=nativa`, retrocompat). Ambas son firma electrónica **art. 7 no acreditada** (`acreditado=false`) — la PKI por-usuario añade verificabilidad/formato/separación criptográfica, no valor legal reforzado (custodia servidor ⇒ sin sole-control). **Gate XAdES por nivel (Inc.3, `FIRMA_PERSONAL_REQUIRE_XADES` ∈ `off`|`clasificados`|`todos`, default `off`)**: con `clasificados`, un acto de `nivel_seguridad>=2` **ya no degrada** a nativa — sin cert activo → **`422 firma_xades_requerida`** (con guía de enrolamiento), auth-service caído → **`503 canal_firma_personal_no_disponible`** (fail-closed); con `todos`, aplica en cualquier nivel. La denegación deja asiento `firma.xades_requerida_denegada` atómico y **no consume** ningún factor 2FA. En batch se evalúa **por ítem**. El env legacy bool se acepta (`false`→`off`, `true`→`todos`); un valor no reconocido aborta el arranque. Habilitar `clasificados` solo tras completar el enrolamiento de los firmantes con clearance>=2 (la emisión del cert es offline).

- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…], nivel_seguridad?}` — crea la cadena (turnos `orden` 1..N). `409 cadena_existente` si el objeto ya tiene una cadena abierta. _Nota:_ para firmar con 2FA la solicitud debe tener `payload_sha256` estable.
  - **`objeto_tipo` acotado** a `radicado` · `expediente` · `borrador` · `documento` (`422` fuera del conjunto). No es validación cosmética: el nivel de seguridad de la cadena se resolvía comparando este campo —texto libre del body— contra el literal `"radicado"`, así que `"Radicado"` bastaba para saltarse la fuente autoritativa (RF-SEG-08).
  - **El nivel lo decide el objeto, no el body.** Si `objeto_id` resuelve contra `radicados` o `expedientes`, manda el `nivel_seguridad` real de esa fila y el del body se ignora — se resuelve **sin mirar `objeto_tipo`**, de modo que declarar `borrador` sobre el identificador de un radicado CLASIFICADA tampoco rebaja nada. `nivel_seguridad` solo surte efecto para objetos sin registrar (borrador/documento), donde no hay tabla que consultar.
  - **Fail-closed**: `objeto_tipo` ∈ {`radicado`, `expediente`} cuyo `objeto_id` no resuelve → `404 solicitud_not_found`, el mismo neutro del no-read-up (no distingue «no existe» de «no lo alcanzas»). Antes se caía al nivel declarado en el body.
- `GET /api/v1/signature/requests/{id}` — estado de la cadena: cabecera + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — cadena abierta de un objeto (`404` si no hay).
- `GET /api/v1/signature/pending?page=&size=` — bandeja: solicitudes cuyo **turno activo** es del usuario autenticado (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** emite el reto 2FA: genera un OTP de 6 dígitos ligado a `(solicitud, firmante del turno activo, hash_documento)`, lo envía por correo al firmante y devuelve `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). Solo el firmante del turno activo (`403 no_es_su_turno`); `404 solicitud_no_encontrada` (neutro); **`422 payload_no_fijado`** si la solicitud no tiene `payload_sha256`; `429 challenge_cooldown` si se reemite antes de 30s; `503 canal_2fa_no_disponible` si el correo no se puede enviar (fail-closed). El OTP **nunca** aparece en la respuesta ni en logs.
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id?, otp?, totp_code?}` — firma el turno activo (reusa el firmante nativo HMAC) exigiendo el **2FA verificado**; avanza la cadena y, al firmar el último turno, la solicitud pasa a `firmado`. **Selección de factor**: si viene `totp_code` (6 dígitos) y el firmante tiene TOTP **activo** → se verifica contra auth-service (**no** requiere `/challenge` previo); si vienen `challenge_id`+`otp` → OTP-correo; si viene `totp_code` pero el firmante **no** tiene TOTP activo → **`422 totp_no_activo`** (no cae en silencio al correo); ambos → prevalece TOTP; ninguno → **`409 2fa_requerido`**. Errores TOTP: `401 totp_incorrecto` `{intentos_restantes}`, `423 totp_bloqueado`, `503 canal_2fa_no_disponible` si auth no responde (fail-closed → rollback). Errores OTP-correo: `401 otp_incorrecto` `{intentos_restantes}` (5 intentos → `challenge_bloqueado`), `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`, `403 challenge_ajeno`. Comunes: `403 no_es_su_turno`; `409` si la solicitud no está pendiente o se pierde la carrera del turno (lock `FOR UPDATE`). El 2FA es **no desactivable** para `nivel_seguridad ≥ 2` (RESERVADA/CLASIFICADA) — ambos factores lo satisfacen; TOTP es el más fuerte.
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rechaza el turno activo y **detiene** la cadena (solicitud `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id?, otp?, totp_code?}, …]}` — firma en lote los turnos del usuario, **un segundo factor por documento** (RF-FIR-13): OTP-correo (`challenge_id`+`otp`) o `totp_code`; no aborta ante un fallo individual (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). _(Contrato cambiado en E06 Inc.2: antes `{solicitud_ids:[…]}`.)_

### Envíos postales (E20) — document-service

Ciclo de vida del despacho físico de un radicado de salida. Máquina de estados `registrado → en_transito → entregado/devuelto/fallido` (`entregado`/`devuelto`/`fallido` terminales). Las rutas humanas exigen **`PERM_RADI_SALIDA`** y aplican no-read-up por _clearance_.

- `POST /api/v1/documents/{document_id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado con un operador (guía autogenerada). `201`. Resuelve el _clearance_ del llamante → `404 radicado_not_found` si el radicado no existe **o** lo supera (indistinguibles): no se despacha lo que no se puede ver.
- `GET /api/v1/documents/{document_id}/envios` — envíos del radicado (filtrados por _clearance_).
- `GET /api/v1/envios?estado=&page=&size=` — listado transversal del tenant (`X-Total-Count`, no-read-up).
- `GET /api/v1/envios/{shipment_id}` — detalle (`404` si el radicado supera el _clearance_ del llamante).
- `POST /api/v1/envios/{shipment_id}/estado` `{estado, descripcion?, motivo?}` — transición **manual** (despacho humano). `motivo` alimenta `motivo_devolucion` —la causal en su propio campo, consultable— igual que ya hacía el webhook entrante del operador; sin él la causal solo quedaba dentro del texto libre de `descripcion`. Resuelve el _clearance_ del llamante → `404` si el radicado lo supera (cierre D-14); `409 transicion_invalida` para un salto no permitido.
- `GET /api/v1/envios/{shipment_id}/tracking` — **consulta por guía (pull, F5)** contra el operador (stub `consultar_estado`); devuelve `{estado_interno_actual, estado_operador, estado_interno_mapeado, operador_conectado}`. Solo lectura, no aplica la transición.
- `POST /api/v1/public/postal/callback/{tenant_slug}` `{operador, guia, estado_operador, event_id, descripcion?, motivo?}` — **webhook ENTRANTE del operador (F5, RF-POR-08)**. **Sin JWT** (bajo `/api/v1/public/`); autenticación **HMAC-SHA256** con cabecera `X-OrpycaMCP-Signature: sha256=<hex>` sobre el cuerpo crudo, con el secreto per-tenant/per-operador de `postal_operator_credential` (identidad propia del operador, separada del permiso humano — cierra D-14 #3). El `tenant_slug` del path fija el `search_path`; el secreto vive en el schema de ese tenant. **Idempotente** por `(operador, event_id)` (reintento → no-op). Mapea `estado_operador` a la máquina interna (`422 estado_operador_no_mapeable` si no mapea). Devuelve `{status: "applied"|"duplicate"|"recorded_no_change", shipment_id, estado}`. `401 firma_invalida` (sin credencial o firma mala, indistinguibles); `404 envio_no_encontrado` (guía desconocida); `400 cuerpo_invalido`. Una notificación que implicaría un retroceso (o sobre un terminal) se registra como `recorded_no_change` sin mutar el estado. Cada transición emite un evento `document.envio.*` para E16 y deja asiento en `audit_log` (`actor=postal:<operador>`).

### Interoperabilidad — OAI-PMH (E11, RF-INT-02/INT-06) — document-service

> **APAGADO POR DEFECTO desde la auditoría de producción.** Estos endpoints publican de forma **anónima** el registro de correspondencia (número, asunto, dependencia, fecha) y, vía CMIS `cmisselector=content`, los **bytes de los anexos**. Su único recorte es `nivel_seguridad = 1`, que es el **valor por defecto** de la columna: la polaridad efectiva era «público salvo que alguien se acuerde de clasificar». Se habilitan con `PUBLIC_INTEROP_ENABLED=true` en **document-service Y archive-service** (mismo nombre de variable en ambos a propósito, para no quedarse a medias). Apagados responden **404**, indistinguible de una ruta inexistente. Encendidos, cada cosecha y cada descarga dejan asiento (`document.oai_cosecha`, `document.cmis_consulta`, `document.cmis_descarga_anexo`, `archive.oai_cosecha`) con el verbo, los argumentos y el tamaño entregado. Publicar el registro es el esquema de publicación de la entidad (Ley 1712, art. 12), no un efecto de desplegar el software.

- `GET /api/v1/public/oai/{tenant}?verb=...` — **cosecha de metadatos OAI-PMH 2.0** de los radicados **públicos** del tenant. **Sin JWT** (bajo `/api/v1/public/`, como el webhook postal); el `tenant` del path fija el `search_path`. Respuesta `application/xml`. Verbos: `Identify`, `ListMetadataFormats`, `ListIdentifiers`, `ListRecords`, `GetRecord` (args `identifier`+`metadataPrefix`), `ListSets`. Cosecha selectiva `from`/`until` (fecha OAI) y `set` (= `doc_type` E/S/I). Identificador OAI `oai:{tenant}:{tracking_number}`. Paginación por `resumptionToken` (100/página; el token preserva el `metadataPrefix`). Errores OAI estándar (`badVerb`, `badArgument`, `cannotDisseminateFormat`, `idDoesNotExist`, `noRecordsMatch`, `badResumptionToken`).
  - **Dos formatos de diseminación (RF-INT-06):**
    - `metadataPrefix=oai_dc` — **Dublin Core** simple (title=asunto, identifier=número de radicado, type, date, publisher, language).
    - `metadataPrefix=ead` — **EAD 2002 / ISAD(G)** (namespace `urn:isbn:1-931666-22-9`): cada radicado se disemina como un fragmento EAD autocontenido con `<archdesc level="item">` (unidad documental simple) y los **6 elementos obligatorios de ISAD(G)/NTC 4095 para intercambio** — 3.1.1 `<unitid countrycode="CO" repositorycode>` (código de referencia país+repositorio+número), 3.1.2 `<unittitle>` (asunto), 3.1.3 `<unitdate normal>` (fecha), 3.1.4 `@level="item"`, 3.1.5 `<physdesc>` (soporte), 3.2.1 `<origination>` (dependencia origen) — más 3.4.1 `<accessrestrict>` (público, Ley 1712/2014), 3.7.2 `<descrules>` (ISAD(G) 2ª ed. / NTC 4095), `<langmaterial>` y `<repository>`. **Deuda declarada** (roadmap): descripción monolvel `item`; el contexto multinivel fondo→sección→serie→expediente (archive-service, TRD/CCD) se inyectará en un fast-follow.
  - **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** el recorte (`_PUBLIC` en la fuente) es **ortogonal al formato** — elegir `ead` no sortea el filtro ni crea oráculo de reservados; un id reservado/anulado/inexistente → `idDoesNotExist` indistinguible en ambos formatos.
- **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** la cosecha **solo** expone lo **público** (`nivel_seguridad=1`, no anulado). El recorte se aplica en la fuente, así la omisión de reservados/clasificados es **indistinguible**: `completeListSize` cuenta solo público, `ListSets` no enumera un _set_ solo-reservado, y `GetRecord`/`ListMetadataFormats` sobre un identificador reservado devuelven `idDoesNotExist` (no confirman su existencia). No hay credencial de tercero con clearance en este alcance (cosecha pública).

### Interoperabilidad — OAI-PMH de expedientes / EAD multinivel (E11, RF-INT-06) — archive-service

- `GET /api/v1/public/archive/oai/{tenant}?verb=...` — **cosecha de metadatos OAI-PMH 2.0 de los EXPEDIENTES públicos** del tenant, con descripción archivística **EAD 2002 / ISAD(G) MULTINIVEL**. **Sin JWT** (bajo `/api/v1/public/`; el gateway rutea `/api/v1/public/archive/` a archive-service **antes** del catch-all de document-service). Respuesta `application/xml`. Verbos OAI completos; `set` = `status` del expediente (open/closed/transferred). Identificador `oai:{tenant}:expediente:{code}`. Formatos `oai_dc` y `ead`.
  - **EAD multinivel (`metadataPrefix=ead`):** caminando la cadena `trd_series.parent_id` (el CCD, serie↔subserie), cada expediente se disemina anidado de lo general a lo específico — `<archdesc level="fonds">` (institución) → `<dsc>` → `<c level="series">` → (`<c level="subseries">`…) → `<c level="file">` (el expediente) — cumpliendo ISAD(G) 2.2 (general→específico) y 2.4 (vínculo al nivel superior). El nivel `file` lleva los 6 obligatorios ISAD(G)/NTC 4095 (`<unitid countrycode="CO" repositorycode>`=code, `<unittitle>`=nombre, `<unitdate normal>`=apertura→cierre, `@level="file"`, `<physdesc>`=conteo de documentos, `<origination>`=institución productora) + `<scopecontent>` (descripción), `<accessrestrict>` (público, Ley 1712), `<descrules>` (ISAD(G)/NTC 4095). Sin `trd_serie_id` → `<archdesc level="file">` plano (fallback honesto).
  - **Complementa** el EAD **item-level** de radicados (RF-INT-06 en document-service): document = radicados (unidad documental simple), archive = expedientes (unidad documental compuesta con su contexto de clasificación).
  - **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** chokepoint `_PUBLIC = "nivel_seguridad = 1"` en `expedientes`, aplicado en la fuente → un expediente reservado es **indistinguible** (`idDoesNotExist`, `completeListSize`/sets solo público). **Frontera anti-fuga item-level:** la descripción **para en `level="file"`** — un expediente público puede contener radicados reservados y archive no tiene su nivel de seguridad, así que **nunca enumera los `tracking_number` hijos** (solo un conteo agregado en `<physdesc>`). CTE de la cadena de series acotada a profundidad 50 (guarda anti-DoS de ciclos en el endpoint anónimo).

### Interoperabilidad — CMIS 1.1 perfil mínimo de lectura (E11, RF-INT-02) — document-service

Browser Binding (JSON). **Solo lectura** sobre los radicados **públicos** del tenant. **Sin JWT** (bajo `/api/v1/public/`); tenant por path.

- `GET /api/v1/public/cmis/{tenant}` — **repositoryInfo**: `{repositoryId: {repositoryName, cmisVersionSupported: "1.1", rootFolderId: "root", capabilities}}`. Las `capabilities` reflejan solo-lectura (query/escritura/versionado en `none`/`false`).
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=children[&maxItems=&skipCount=]` — **getChildren** de la raíz: `{objects:[{object:{properties}}], numItems, hasMoreItems}` (radicados públicos como `cmis:document`).
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=object&objectId={numeroRadicado}` — **getObject**: propiedades CMIS (`cmis:objectId` = número de radicado, `cmis:name` = asunto, `cmis:baseTypeId`, fechas en millis). `objectId=root` → la carpeta raíz sintética.
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=content&objectId={numeroRadicado}` — **getContentStream**: descarga (streaming) el primer anexo del radicado. `objectId` sin valor → `400 invalidArgument`; selector no soportado → `400 notSupported`.
- **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** mismo recorte que OAI (`_PUBLIC` en la fuente). `getObject`/`getContentStream` sobre un id reservado/anulado/inexistente → `404 objectNotFound` **indistinguible**; `getChildren`/`numItems` solo cuentan público. El content stream **nunca** sirve bytes de un anexo de un radicado no público (`get_public_anexo`, JOIN público-only).

### Interoperabilidad — Exportación de paquete (E11, RF-INT-01) — document-service

- `POST /api/v1/export` `{comentario?, year?, doc_type?}` — **exporta radicados a un paquete interoperable** (ZIP). **Autenticado** (no público); gate `USUA_PERM_EXPEDIENTE` + **no-read-up por-radicado** (solo se exporta lo que el llamante puede leer; los sobre-clearance se omiten y se reportan content-free en el manifiesto). Respuesta `application/zip` con cabeceras `X-Export-UUID`, `X-Export-Count`, `X-Export-Excluded` (las tres reenviadas por el gateway: están en `FORWARDED_RESPONSE_HEADERS` y un guardián de `shared/tests` censa los servicios para que ninguna cabecera propia se quede fuera de esa lista — el ZIP viaja como cuerpo, así que son lo único que dice cuánto se exportó y cuánto recortó el clearance). Contenido del ZIP: `manifiesto.json` (UUID de exportación, comentario, marcas inicio/fin, lista de radicados con SHA-256, excluidos), `esquema/radicado.schema.json` (**JSON Schema draft 2020-12** publicado), `radicados/{numero}.json` (datos por radicado que **validan contra el esquema**: metadata sistema+contextual, disposición, clasificación, anexos, historial), `anexos/{numero}/{i}-{archivo}` (binarios), `checksums.txt` (SHA-256 por miembro). El **número de radicado se preserva** como identidad (Ac. AGN 060/2001). Empaquetado físico ZIP provisional (BagIt/OAIS+WORM = E10). Filtros opcionales `year` (2000–2100) y `doc_type` (E/S/I).

- `POST /api/v1/import` — **importa un paquete interoperable** de radicados. **Autenticado**; gate `USUA_PERM_EXPEDIENTE` + **no-write-up por clearance**. Recibe el ZIP de INT-01 como **cuerpo crudo** (`Content-Type: application/zip`, tope 512 MiB). **Validación TODO-o-NADA antes de ingerir**: valida cada radicado contra el JSON Schema de confianza del servicio y **verifica el SHA-256 de cada binario**; si algo falla → `422 paquete_invalido` **sin dejar estado parcial**. Reingiere **preservando el número de radicado** (una colisión se **omite**, no se sobreescribe); reconstruye la relación E↔S (`responde_a`) por número; sube los binarios a storage; audita `radicado.import`. Guardas anti zip-slip/zip-bomb. Respuesta `{import_uuid, importados, omitidos_existentes, omitidos_clearance, relaciones_reconstruidas}`. Un radicado con `nivel_seguridad` > clearance del importador se **omite** (`omitidos_clearance`).

### Capa MCP (E18, ADR-019) — mcp-server, puerto 8009

Expone la API como _tools_ a agentes/LLM, siendo él mismo cliente del gateway. **Protocolo MCP** disponible por **stdio** (`python -m app.mcp_stdio`) y **HTTP streamable**.

**Se enruta por el gateway** (`/api/v1/mcp/` → mcp-server), lo que antes no ocurría: el catálogo y el protocolo quedaban fuera, de modo que un cliente MCP externo no tenía por dónde entrar salvo hablando con el servicio a espaldas del gateway, sin validación JWT ni `X-Tenant-Slug`. Se puede exponer porque el gate de escritura ya no depende del cliente (propuesta firmada, ver arriba). El transporte streamable se monta en `POST /api/v1/mcp/stream` además de en `/mcp`.

El contexto de sesión son **dos cabeceras**: `Authorization` y `X-Tenant-Slug`. `X-User-Permissions` y `X-User-Roles` **ya no se leen** — la primera no la producía nadie y el gateway la descarta por forjable, así que llegaba vacía y toda tool con permiso quedaba invisible por ese transporte; los permisos salen de `GET /api/v1/auth/me` con el token del llamante, y con ellos el **nivel CRUD**. Los ajustes de entorno equivalentes (`MCP_USER_PERMISSIONS`, `MCP_USER_ROLES`) se han retirado.

**Marco de alcance**: la tool `mi_alcance` y el resource `orfeo://alcance` devuelven lo que la persona puede ejecutar, lo que puede consultar y lo que **no** puede con su motivo y su **vía alternativa**. Se ofrece siempre, aunque no alcance ninguna escritura: el conjunto de tools dice qué se puede hacer y calla lo demás, y callar es cómo un límite acaba descubriéndose fallando.

API HTTP simple del catálogo:

- `GET /api/v1/mcp/tools` — catálogo de tools que ESTE llamante puede usar, resuelto contra `/auth/me` (permiso **y** nivel).
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invoca la tool: propaga `Authorization` + `X-Tenant-Slug` al gateway. La identidad y los permisos se resuelven contra `GET /api/v1/auth/me` con el token del llamante (permiso **y** nivel CRUD; `403` si no alcanza). Tools: `radicar_documento`, `consultar_radicado`, `buscar_radicados`, `consultar_expediente`, `indice_expediente`, `listar_trd`, `bandeja_tramite`, `consultar_fuid`, `buscar_conocimiento` (búsqueda semántica E21; POST `/api/v1/knowledge/search`, solo lectura, ACL server-side).
  **Las operaciones de escritura NO se ejecutan aquí: devuelven una propuesta.** `X-Confirm-Write` ya **no se lee** — era un booleano que cualquier cliente podía poner y que no ataba la confirmación a ningún acto concreto (confirmaba «algo», no «esto»). La respuesta es `{kind:"proposal", ejecutado:false, proposal:{id, tool, operacion, campos, expira_en, token}, aviso}`: `campos` son las etiquetas legibles del acto —redactadas por el **catálogo**, nunca por el modelo— y `token` es un valor opaco firmado con HMAC-SHA256 que lleva dentro la tool y los argumentos ya validados. Vence a los 120 s y está atado al usuario y a la entidad que lo pidieron.
- `POST /api/v1/mcp/proposals/execute` `{token}` — ejecuta el acto que va firmado en el token. **El cuerpo lleva solo el token**: los argumentos salen de él, así que lo ejecutado es exactamente lo descrito. Se **reautoriza** al confirmar (entre proponer y confirmar pueden haberle retirado el permiso: la propuesta acredita _qué_ se aprobó, no que quien la presenta siga pudiendo hacerlo) y se **consume** antes de despachar. Errores: `400 propuesta_invalida`, `403 propuesta_ajena` / `permiso_insuficiente`, `409 propuesta_consumida`, `410 propuesta_expirada`.
  El mismo acto por el **protocolo MCP**: las escrituras devuelven la propuesta como resultado estructurado y se confirman con la tool `confirmar_accion(token)`; donde el transporte tiene canal de vuelta (**stdio**) se pregunta en banda por **elicitación** con esos mismos campos. En HTTP streamable (`stateless`) no hay sesión que preguntar y queda el camino de dos tools — límite declarado. Las tools llevan `annotations` MCP (`readOnlyHint`, `destructiveHint`) como **pistas** para que el cliente ponga su propia fricción: la garantía sigue viviendo en la propuesta firmada, no en ellas.
  _Límite declarado_: el registro de consumo vive en memoria del proceso — cubre el doble clic y el reintento, no el multi-réplica. `Idempotency-Key: <jti>` se emite hacia el gateway como gancho para que cada épica lo implemente, no como garantía.

El catálogo actual (45 tools, 16 de escritura). Toda escritura devuelve una propuesta firmada y exige confirmación explícita (ver más arriba):

| Tool                        | Endpoint                              | Permiso (crud)                  |                                            |
| --------------------------- | ------------------------------------- | ------------------------------- | ------------------------------------------ |
| `radicar_documento`         | `POST /documents`                     | `PERM_RADI` (1)                 | **W** consume número inmutable             |
| `tipificar_radicado`        | `PATCH /documents/{id}`               | `PERM_RADI` (**3**)             | **W** fija la serie TRD                    |
| `actualizar_radicado`       | `PATCH /documents/{id}`               | `PERM_RADI` (1)                 | **W** asunto, destino, estado, folios      |
| `consultar_radicado`        | `GET /documents/{id}`                 | _clearance_                     | R                                          |
| `consultar_por_numero`      | `GET /documents/by-tracking/{tn}`     | _clearance_                     | R                                          |
| `buscar_radicados`          | `GET /search`                         | _clearance_                     | R                                          |
| `listar_anexos`             | `GET /documents/{id}/anexos`          | _clearance_                     | R                                          |
| `consultar_disposicion`     | `GET /documents/{id}/disposition`     | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `listar_tipos_documentales` | `GET /tipos-documentales`             | _sin permiso_                   | R                                          |
| `crear_expediente`          | `POST /expedientes`                   | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                      |
| `actualizar_expediente`     | `PATCH /expedientes/{id}`             | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                      |
| `vincular_radicado`         | `POST /expedientes/{id}/radicados`    | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                      |
| `cerrar_expediente`         | `POST /expedientes/{id}/close`        | `USUA_PERM_EXPEDIENTE` (**3**)  | **W** IRREVERSIBLE                         |
| `consultar_expediente`      | `GET /expedientes/{id}`               | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `buscar_expedientes`        | `GET /expedientes/search`             | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `consultar_foliado`         | `GET /expedientes/{id}/foliado`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `hoja_ruta_expediente`      | `GET /expedientes/{id}/eventos`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `indice_expediente`         | `GET /expedientes/{id}/indice`        | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `listar_trd`                | `GET /trd`                            | _sin permiso_                   | R                                          |
| `bandeja_tramite`           | `GET /workflows/inbox`                | _sin permiso_                   | R                                          |
| `pendientes_equipo`         | `GET /workflows/pending`              | _sin permiso_                   | R                                          |
| `tipos_transaccion`         | `GET /workflows/transaction-types`    | _sin permiso_                   | R                                          |
| `hoja_ruta_radicado`        | `GET /workflows/{id}/history`         | _sin permiso_                   | R                                          |
| `paso_actual`               | `GET /workflows/{id}/current`         | _sin permiso_                   | R                                          |
| `asignar_radicado`          | `POST /workflows/assign`              | `PERM_TRAMITAR` (1)             | **W**                                      |
| `trasladar_radicado`        | `POST /workflows/transfer`            | `PERM_TRAMITAR` (1)             | **W**                                      |
| `completar_paso`            | `PATCH /workflows/{step_id}/complete` | `PERM_TRAMITAR` (1)             | **W** el id es del PASO                    |
| `ejecutar_transaccion`      | `POST /workflows/{id}/transactions`   | por tipo, suelo `PERM_TRAMITAR` | **W**                                      |
| `consultar_fuid`            | `GET /transferencias/{id}/fuid`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `buscar_conocimiento`       | `POST /knowledge/search`              | _clearance_                     | R                                          |
| `buscar_antecedentes`       | `POST /knowledge/antecedentes`        | _clearance_                     | R **query XOR radicado_ref**               |
| `redactar_con_antecedentes` | `POST /knowledge/rag`                 | _clearance_                     | R devuelve texto **con citas**, no escribe |
| `listar_respuestas`         | `GET /documents/{id}/respuestas`      | _clearance_                     | R                                          |
| `crear_respuesta_vinculada` | `POST /documents/{id}/respuesta`      | `PERM_RESPUESTA_VINCULADA` (1)  | **W** consume número inmutable             |
| `listar_plantillas`         | `GET /plantillas`                     | `PERM_RADI` (1)                 | R                                          |
| `listar_borradores`         | `GET /borradores`                     | `PERM_RADI` (1)                 | R                                          |
| `crear_borrador`            | `POST /borradores`                    | `PERM_RADI` (1)                 | **W** sin número; no es acto de archivo    |
| `editar_borrador`           | `PATCH /borradores/{id}`              | `PERM_RADI` (1)                 | **W**                                      |
| `aprobar_borrador`          | `POST /borradores/{id}/aprobar`       | `PERM_RADI` (1)                 | **W** _no_ es requisito para radicar       |
| `radicar_borrador`          | `POST /borradores/{id}/radicar`       | `PERM_RADI` (1)                 | **W** consume número inmutable             |
| `consultar_serie`           | `GET /trd/{id}`                       | _sin permiso_                   | R                                          |
| `consultar_retencion`       | `GET /trd/{id}/retention`             | _sin permiso_                   | R cálculo, no la disposición registrada    |
| `ubicacion_expediente`      | `GET /expedientes/{id}/unidades`      | `USUA_PERM_EXPEDIENTE` (1)      | R dónde está, no qué contiene              |
| `listar_prestamos`          | `GET /prestamos`                      | `USUA_PERM_EXPEDIENTE` (1)      | R                                          |
| `reporte_radicacion`        | `GET /reports/radicados`              | _sin permiso_                   | R solo agregados                           |

**Exclusión mutua declarada, no descubierta con un 422.** `buscar_antecedentes` acepta el asunto (`query`) o el radicado de referencia (`radicado_ref`), nunca ambos ni ninguno — lo impone `AntecedentesRequest` con un `model_validator`, y JSON Schema no lo expresa con las piezas del catálogo. El catálogo lo declara con `exactly_one_of` y `coerce_args` responde `parametros_excluyentes` **antes** de que salga la petición: el 422 del backend dice lo mismo, pero solo después de hacer esperar a la persona.

**`aprobar_borrador` SÍ es requisito** (corregido el 2026-08-17). El ciclo del borrador es crear → editar → aprobar → radicar, y ahora lo exige el backend: `POST /borradores/{id}/radicar` responde `409 borrador_sin_aprobar` si el borrador no está aprobado. Antes solo lo exigía la pantalla (`canRadicar = estado === "aprobado"`), de modo que un `POST` directo saltaba la aprobación y consumía el número igualmente — un control que vive solo en el navegador no es un control.

**Actos irreversibles: frase tecleada, exigida por el backend.** Una propuesta cuya tool declara `destructive` lleva `irreversible: true` y `frase_requerida: "CONFIRMO"`, y ejecutarla exige enviar esa frase (`{token, frase}`). Quién dice que el acto es irreversible **no es el cliente**: es el token firmado, del que sale la tool — así no hay manera de confirmarlo declarando que no lo era. La interfaz deshabilita el botón por cortesía; la barrera vive en `write_gate`, porque una frase validada solo en el navegador es teatro.

**El asistente conversacional también propone.** `POST /api/v1/assistant/message` devuelve `propuestas: []` (campo aditivo) con los actos que el modelo preparó. El `tool_result` que vuelve al modelo dice explícitamente `ejecutado: false` para que no afirme haber hecho algo que no hizo, y el token no entra en su contexto. La confirmación es `POST /api/v1/assistant/confirmar` con `{token, frase?}`.

**Fuera del catálogo por decisión, no por olvido**: anular y aprobar anulaciones, marcar para eliminación, fijar la disposición, reclasificar el nivel de seguridad, publicar, desvincular un radicado (rompe la foliación), transferir, derogar TRD/TVD, firmar, y toda la administración de la entidad. Y la vinculación **masiva** (`/radicados/batch`, hasta 500) queda fuera mientras su tarjeta de confirmación no pueda leerse: una tarjeta que dice «500 radicados» no permite confirmar con conocimiento de causa, que es justo para lo que existe.

**Dos invariantes que cruzan servicios** viven en `shared/tests/`, el único carril de CI que ve el repositorio entero: `test_rutas_mcp_alcanzables.py` (toda ruta del catálogo está en la tabla de ruteo del gateway — el hueco que ya se coló con la carga masiva) y `test_permisos_declarados.py` (lo que el catálogo declara es lo que el endpoint exige, en los dos sentidos: declarar de menos ofrece la tool a quien recibirá un 403; declarar de más la esconde a quien sí puede).

### Asistente conversacional (E18, ADR-019/ADR-020) — vía gateway

A diferencia del catálogo/protocolo MCP (que **no** se expone por el gateway), el asistente conversacional **sí** se enruta por el `api-gateway` (`/api/v1/assistant/` → mcp-server), de modo que hereda su validación JWT y la inyección de `X-Tenant-Slug`. El mcp-server orquesta un **loop LLM (Claude)** que ejecuta las tools de **solo lectura** del catálogo como llamadas al gateway **re-propagando el token del usuario** — el asistente corre _as-the-user_, nunca con credenciales propias, y cada tool revalida su RBAC/clearance en el backend.

- `POST /api/v1/assistant/message` `{mensaje: string(1..4000), conversation_id?: string}` — envía un turno del usuario. Devuelve `{conversation_id, message: {id, role:"assistant", texto, formato:"markdown", ts}}`. El `conversation_id` (generado si se omite) mantiene el hilo; el historial es **efímero por proceso** y **aislado por tenant** (un `conversation_id` nunca cruza tenants). **v1 = SOLO LECTURA**: la única tool de escritura del catálogo (`radicar_documento`) **se excluye** del conjunto ofrecido al modelo (defensa en profundidad: filtrada al construir el prompt y denegada de nuevo en el despacho). El contenido devuelto por las tools (p. ej. radicados de Entrada redactados por terceros) es **dato, nunca instrucción** (frontera anti-inyección en el system prompt). `503 assistant_unavailable` si el asistente no está configurado (sin `ANTHROPIC_API_KEY`) o el SDK falla — **degradación honesta**, nunca una respuesta simulada (RF-FIR-15); `401` sin token; `409 conversation_limit` al agotar el tope de turnos del hilo. Respuestas de tool `403`/`404` se comunican de forma **genérica** ("no encontré ese registro"), sin revelar la existencia de registros clasificados (no-oráculo, RF-SEG-08 / Ley 1712/2014).

### Capa de conocimiento (E21, ADR-006) — knowledge-service, puerto 8011

Capa **advisoria/derivada y opt-in** (no es fuente de verdad). Embeddings con proveedor pluggable (stub local por defecto).

- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — calcula el embedding y persiste el fragmento (pgvector) con su ACL. Para proteger contenido clasificado, el ingestor debe incluir el nivel en `acl.nivel_seguridad` (1–3); ausente = PUBLICA (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — recuperación semántica (kNN coseno). **Control de acceso resuelto en el servidor (RF-SEG-08 / RF-BUS-04):** se devuelven **siempre** solo fragmentos con `nivel_seguridad ≤` el _clearance_ del llamante (derivado del JWT/BD, no del cuerpo de la petición); el `acl` opcional del cliente es solo _narrow_ adicional (p. ej. por dependencia), nunca amplía el acceso. Devuelve `[{id, source_type, source_ref, texto, metadata, score}]`. Los _tombstones_ (fragmentos purgados por reclasificación) quedan **excluidos** de la búsqueda.
- `POST /api/v1/knowledge/antecedentes` `{query|radicado_ref, source_type?, top_k?}` (XOR `query`/`radicado_ref`, E21 Inc.2, Patrón A) — recuperación pura ANCLADA y CITADA, sin LLM: reusa `search` (mismo pre-filtro ACL), devuelve `{antecedentes:[{source_type, source_ref, snippet, score}], advisory}` con un aviso **fijo** (CC-01/CC-02). `radicado_ref` inexistente **o** que excede el clearance del solicitante colapsan al mismo `404 antecedente_source_not_found` (anti-oráculo, sin 403 diferenciado).
- `POST /api/v1/knowledge/rag` `{query, top_k?}` (E21 Inc.3, Patrón B) — RAG generativo con citas. Recupera con el mismo pre-filtro ACL de `search` y, si `AI_PROVIDER` es un proveedor de generación real (`ollama_local` default soberano, `ollama_cloud`/`openai_compatible` externos, `anthropic` con Citations API nativa), sintetiza una respuesta ANCLADA. **Barrera dura de soberanía (sin override)**: con proveedor externo, excluye del contexto enviado al LLM los fragmentos `nivel_seguridad ≥ RESERVADA` — visibles por ACL, pero nunca egresados a un tercero; si no queda contexto generable, cae a recuperación pura (Patrón A) sobre todo lo recuperado. `AI_PROVIDER=disabled` (o no configurado) también cae siempre a Patrón A. Devuelve `{answer, citations:[{cited_text, source_type, source_ref, score}], proveedor, modelo, confianza, suggestion_id, advisory}` — `answer`/`modelo`/`suggestion_id` son `null` cuando no hubo generación real. Cada generación real se registra en `kb_suggestions` (CC-03: `proveedor`/`modelo` en columnas separadas) y en `audit_log`. `503 rag_unavailable` ante cualquier fallo del proveedor (SDK ausente, credencial ausente, error de red/API) — degradación honesta (RF-FIR-15), nunca una respuesta simulada.

> **Ingesta event-driven (E21, MVP):** además del `POST /ingest` manual, el knowledge-service **consume `orpycamcp.document.events`** (consumer group `knowledge-ingest` + DLQ, ADR-021) y auto-indexa cada radicado al crearse (`document.radicado.created` → `subject`/`sender`, `source_type="radicado"`, `source_ref=tracking_number`). El `nivel_seguridad` se **replica del evento** (fail-closed a CLASIFICADA si falta) y se **mantiene consistente ante reclasificación** (`document.radicado.reclassified`): una subida a RESERVADA+ con proveedor de embeddings externo **purga el contenido** (tombstone), y el nivel de un fragmento **nunca baja** salvo por una reclasificación explícita, sea cual sea el orden de llegada de los eventos. Material RESERVADA+ **no se embebe** con un proveedor externo (queda registrado en `audit_log`). Un radicado **anulado** (`document.radicado.annulled`) se marca _voided_ de forma **permanente** y queda excluido de la búsqueda a cualquier clearance (no se re-indexa aunque un evento de creación se reprocese después). Es una capa **derivada/advisoria** (ADR-006): no es fuente de verdad; el registro legal vive en document/archive.

### Webhooks salientes (E11, ADR-018)

- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — suscribe un endpoint externo (`event_types` vacío = todos los eventos). El `secret` no se devuelve. **Exige `USUA_PERM_ADMIN`** (todo el CRUD): registrar una suscripción entrega cada evento del tenant a la URL indicada, así que es config de administración — sin el gate sería un canal de exfiltración (y SSRF). **`422` nuevo** si `url` apunta a un destino interno/no enrutable (loopback `127.0.0.0/8`/`::1`/`localhost`, link-local `169.254.0.0/16`/`fe80::/10` — incluye el endpoint de metadata cloud, privado `10.0.0.0/8`/`172.16.0.0/12`/`192.168.0.0/16`/`fc00::/7`, un nombre de servicio de la red Docker interna de OrpycaMCP, o un esquema distinto de `http`/`https`): el `detail` explica el motivo concreto del rechazo, no un "URL inválida" genérico. Estricto por defecto; relajable solo en desarrollo con `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` en notification-service (kill-switch, nunca en producción).
- `GET /api/v1/webhooks` — lista las suscripciones (`X-Total-Count`). Exige `USUA_PERM_ADMIN`.
- `DELETE /api/v1/webhooks/{id}` — elimina una suscripción. Exige `USUA_PERM_ADMIN`.
- Al ocurrir un evento de dominio, OrpycaMCP hace `POST` del sobre canónico a las suscripciones que coinciden, firmado en la cabecera `X-OrpycaMCP-Signature: sha256=<hmac>` (best-effort con reintentos). El receptor debe ser idempotente. Cada intento de entrega **re-valida el destino con resolución DNS real** (no solo al crear la suscripción): cierra el caso de un dominio que resolvía a IP pública al suscribirse y luego se re-apunta a una IP interna (DNS rebinding), y también cubre suscripciones preexistentes a este endurecimiento — si el destino ya no es válido, la entrega se omite y se registra, pero la suscripción **no se borra ni se desactiva** automáticamente. Limitación aceptada: queda una ventana TOCTOU entre esa revalidación y la conexión real de `httpx` (ver `app/core/webhook_security.py`).

---

## Eventos Redis Streams

El workflow-service publica en `orpycamcp.workflow.events` cuando cambia el estado de un flujo:

```json
{
  "event_type": "flow_step_created",
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "to_dept": "Dirección Jurídica",
  "action": "assign",
  "tenant_slug": "icetex",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

El notification-service consume este stream (consumer group `notification-service`) y envía email al departamento destino.

### Gestión del dead-letter (Fase 5, ADR-021)

Los eventos que no se pueden procesar tras `max_deliveries` reintentos quedan en la tabla `evento_dead_letter` (por tenant) con `status='pending'`. Cada servicio consumidor expone una superficie admin para inspeccionarlos y resolverlos, gateada por el permiso **`PERM_DLQ_ADMIN`** (rol de plataforma; asignación restringida). Implementado en **signature-service, workflow-service y notification-service** (roadmap ADR-021 completo). `evento_dead_letter` es una **tabla física compartida**: cada servicio filtra por su `origin_group` para no operar sobre los dead-letters de otro.

| Método | Ruta                                                                                                      | Descripción                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------ | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter?status=pending&origin_stream=&page=&size=` | Lista paginada (`X-Total-Count`). Solo cabeceras (sin payload de negocio) y **filtrada por clearance del llamante** (no-read-up fila-a-fila sobre el nivel vivo del radicado referenciado, misma resolución que el detalle): una fila cuyo objeto excede el nivel del admin se omite por completo (no aparece ni cuenta en `X-Total-Count`), consistente con el `404` neutro del detalle. Fail-closed ante cualquier error de resolución. La paginación se aplica en la capa de aplicación, después del filtrado. El conjunto de candidatas se acota a `1000` antes de filtrar; si se alcanza ese tope la respuesta incluye el header **`X-Truncated: true`** (advierte que `X-Total-Count` puede sub-reportar). |
| `GET`  | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}`                                | Detalle (sobre íntegro + fallo). Gateado por clearance (no-read-up sobre el nivel vivo del radicado; `404` **neutro** si insuficiente o inexistente —cuerpo byte-idéntico—). Una vista autorizada deja un asiento `evento.dead_letter.viewed` en `audit_log` (una vista bloqueada NO audita, para no confirmar existencia).                                                                                                                                                                                                                                                                                                                                                                                      |
| `POST` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}/replay`                         | Reprocesa el evento. `200` → `status='resolved'`; `409` si no está `pending`; `404` si no existe o sin clearance. **signature/workflow**: reproceso idempotente y atómico; `502` si falla (queda `pending`). **notification** (correo no transaccional): _best-effort claim-then-send_ —limpia el dedup y reenvía—; `422` si el `origin_stream` es desconocido o el tenant no cuadra, `502` si el envío falla (queda `pending`).                                                                                                                                                                                                                                                                                 |
| `POST` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}/discard`                        | Cierra como `discarded`. Body `{ "reason": "..." }` (obligatorio, ≥10 caracteres tras `strip`). Mismos códigos.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

Toda acción (`replay`/`discard`/`viewed`) se registra en el `audit_log` inmutable con el actor humano (`X-User-Id`). En notification la **auditoría es dependencia dura** de la disposición: si el módulo de auditoría no está disponible, `replay`/`discard` responden `503` **antes** de transicionar (no se dispone sin dejar traza legal). Los eventos malformados sin `tenant_slug` (`SYSTEM_TENANT`) no son replayables (gestión de plataforma).

---

## Batch API — document-service

> Permite crear múltiples radicados o expedientes en una sola llamada. El procesamiento es asincrónico.
>
> **Contrato de ruteo (fix de desfase, 2026-08).** `document-service` y
> `archive-service` comparten el prefijo raíz `/api/v1/batch`, pero cada uno
> expone un tipo de trabajo disjunto. El estado del job queda **anidado bajo el
> tipo** (`/batch/documents/{job_id}/status`, `/batch/expedientes/{job_id}/status`)
> — antes ambos servicios exponían la misma forma `GET /api/v1/batch/{job_id}/status`,
> lo que hacía **imposible** que el api-gateway decidiera a qué upstream reenviar
> por prefijo (el `job_id` no basta). El `POST` ya distinguía por subruta
> (`/documents` vs `/expedientes`); ahora el `GET` sigue el mismo criterio.
> Requiere `PERM_RADI` (igual que `POST /api/v1/documents/`, la creación
> individual) — antes esta ruta no exigía ningún permiso.

### `POST /api/v1/batch/documents`

Crea un job para registrar 1-1000 radicados. Retorna `202 Accepted` inmediatamente. Requiere `PERM_RADI`.

El procesamiento corre en una tarea de fondo **con su propia conexión del pool** — nunca la de la petición, que se devuelve al pool en cuanto se responde (ver `acquire_tenant` en `app/core/database.py`). Un job que no puede procesarse queda en `failed` con su motivo en `error_message`: `pending` significa que sigue en cola, no que algo salió mal. `pages` es opcional en la petición y se registra como `0` cuando no se envía, igual que en el camino síncrono.

```json
// Request
{
  "documents": [
    { "doc_type": "E", "subject": "Solicitud 1", "sender_name": "Juan Pérez" },
    { "doc_type": "E", "subject": "Solicitud 2", "sender_name": "Ana Gómez" }
  ]
}

// Response 202
{ "job_id": "uuid", "job_type": "documents", "status": "pending", "total_items": 2, "created_at": "..." }
```

### `GET /api/v1/batch/documents/{job_id}/status`

Monitorea el progreso del job. Requiere `PERM_RADI`.

```json
// Response 200
{
  "job_id": "uuid",
  "job_type": "documents",
  "status": "completed", // pending | processing | completed | failed
  "total_items": 2,
  "processed_items": 2,
  "failed_items": 0,
  "items": [
    { "item_index": 0, "status": "success", "result_id": "uuid-radicado" },
    { "item_index": 1, "status": "failed", "error_message": "..." }
  ]
}
```

> **Auditoría (cerrado 2026-08).** La creación masiva de radicados deja un
> asiento `document.radicado_created` en `public.audit_log` **por cada
> radicado creado con éxito** (no uno por lote) — espejo de
> `POST /api/v1/documents/` (creación individual) y de `batch/expedientes`
> en archive-service. `actor` es el `X-User-Id` que validó el router al
> encolar el job, nunca un campo del body. Cada ítem se procesa en su propia
> transacción (secuencia + `INSERT` + asiento, todo o nada): un lote
> parcialmente exitoso deja tantos asientos como ítems realmente creados, y
> los fallidos quedan marcados `failed` sin asiento.

---

## Batch API — archive-service

### `POST /api/v1/batch/expedientes`

Crea 1-100 expedientes con radicados vinculados. Retorna `202 Accepted`. Requiere `USUA_PERM_EXPEDIENTE`.

```json
// Request
{
  "expedientes": [
    {
      "name": "Pensión García 2024",
      "trd_serie_id": "uuid",
      "radicado_ids": ["uuid1", "uuid2"],
      "tracking_numbers": ["2024-ICETEX-E-000001", "2024-ICETEX-E-000002"]
    }
  ]
}
// Response 202 — mismo formato que batch documents
```

### `GET /api/v1/batch/expedientes/{job_id}/status`

Monitorea el progreso del job. Requiere `USUA_PERM_EXPEDIENTE` (deja asiento en `audit_log` por expediente creado).

---

## Full-Text Search & Reportes — document-service

### Plantillas y borradores de Salida (paridad legado)

- `GET/POST/DELETE /api/v1/plantillas` — plantillas reutilizables (`nombre`, `cuerpo`, `tipo_documental`).
- `POST /api/v1/borradores` `{doc_type, subject, dest_dept?, cuerpo?, plantilla_id?}` — crea un borrador (si se da `plantilla_id` y no `cuerpo`, hereda el cuerpo de la plantilla).
- `GET /api/v1/borradores` (`?estado=`), `GET /{id}`, `PATCH /{id}` (editar solo si no radicado), `POST /{id}/aprobar` (borrador→aprobado), `POST /{id}/radicar` (→ genera el radicado real y marca el borrador `radicado`).

### Respuesta rápida (paridad legado)

- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, observations?, response_days?}` — crea un radicado de **Salida** en respuesta a este (hereda asunto `RE: …` y dependencia si se omiten), enlazado al antecedente (`responde_a`). Requiere permiso `PERM_RESPUESTA_VINCULADA`. `403 forbidden` si falta. Devuelve el nuevo radicado. **No-read-up (RF-SEG-08):** `404` neutro si el antecedente no existe o supera el _clearance_ del llamante — precisamente porque la Salida **hereda el asunto** del antecedente, responder sin ese gate exfiltraría el asunto de una Entrada reservada a un documento legible.
- `GET /api/v1/documents/{id}/respuestas` — salidas que responden a este radicado.

### Anulación de radicado en dos pasos (paridad legado)

La ley prohíbe borrar radicados: se anulan con aprobación supervisora.

- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — solicita (paso 1). Requiere permiso `PERM_SOL_ANULAR`. `403 forbidden` si falta. `409` si ya está anulado o hay solicitud pendiente. **Las cuatro rutas de anulación** aplican no-read-up (RF-SEG-08): un radicado que supera el _clearance_ del llamante devuelve el mismo `404` que uno inexistente — la causal y el motivo describen el documento.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — aprueba (paso 2; nivel jefe): el radicado pasa a `anulado` (el número se conserva). Requiere `PERM_PANU_CODI`. `403 forbidden` si falta.
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rechaza. Requiere `PERM_PANU_CODI`. `403 forbidden` si falta. `GET /api/v1/documents/{id}/anulacion` — última solicitud.
- `GET /api/v1/anulaciones?page&size` — **cola del aprobador**: solicitudes en estado `solicitada`, la más antigua primero (FIFO — una anulación pendiente bloquea el ciclo de vida del documento). Requiere `PERM_PANU_CODI`, el **mismo** permiso que decidir. Cada fila trae los datos de la solicitud y los del radicado (`tracking_number`, `subject`, `doc_type`, `radicado_status`). `X-Total-Count` con el total. **No-read-up (RF-SEG-08) aplicado por igual a las filas y al recuento**: contar sin filtrar delataría cuántas solicitudes hay sobre radicados que el llamante no puede ver.

**Ventanilla única pública** (sin autenticar):

- `GET /api/v1/public/{tenant}/ventanilla/tipos` — qué tipos de trámite declara recibir la entidad (`tipos_documentales.admite_ventanilla_publica`, **opt-in**). No expone el catálogo interno.
- `POST /api/v1/public/{tenant}/ventanilla` — radicación externa. El tipo debe estar declarado admisible (**422 `tipo_no_admitido_en_ventanilla`** si no; nunca se reclasifica en silencio). Se enruta a la dependencia con `depe_rad_tp2` («habilita radicación web»); si la entidad no ha declarado ninguna, **503 `ventanilla_no_habilitada`** — entrar sin destino dejaría el documento fuera de toda bandeja. El remitente puede ser persona **o entidad** (`sender_entity`). Quien se identifica debe indicar un medio de respuesta; la radicación **anónima** es legítima. Devuelve número de radicado, código de verificación y dependencia destino.
  **Multipart**: los campos van en la parte `datos` (JSON) y los anexos en partes `anexos`. Una sola puerta para el caso simple y para la tutela con soportes. Límites: **5 archivos, 10 MB cada uno, 25 MB el conjunto**; solo PDF, JPG, PNG y TIFF, **comprobados por el contenido** —no por la extensión ni por el `Content-Type`, que los escribe el cliente— y el MIME que se guarda es el deducido. **No hay antivirus.** Los anexos se trasladan ANTES de asignar número: si el almacén falla, **503 `anexo_no_almacenado`** y no se radica nada, porque un número de radicado es inmutable y no puede quedar emitido sin sus soportes. El fichero se guarda sin actor (`uploaded_by = NULL`).
- `POST /api/v1/public/{tenant}/pqrs` — **legado**, se conserva por ser superficie pública ya publicada; delega en la ventanilla y no se amplía.

### Firma electrónica (E06, ADR-016)

- `POST /api/v1/documents/{id}/signatures` `{ "content_hash": "<sha256 hex>", "anexo_id"?, "reason"? }` — firma el radicado/anexo: registra identidad (claims del gateway), `content_hash` y sello de tiempo. `422` si el hash no es SHA-256. **Las tres rutas de firma** exigen `PERM_FIRMA` (el mismo permiso que gobierna signature-service) y aplican no-read-up: `404` neutro si el radicado no existe o supera el _clearance_.
- `GET /api/v1/documents/{id}/signatures` — lista las firmas (`X-Total-Count`). `404` si el radicado no es visible; `[]` (200) si es visible y aún no tiene firmas — un sub-_clearance_ recibe siempre `404`, nunca `[]`, así que el par no es oráculo.
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` si alguna firma del radicado coincide con ese hash (si el contenido cambió, deja de verificar). Gateado igual que el listado: sin el filtro, el `valid` true/false sería un **oráculo de contenido** sobre documentos reservados.

### Envíos postales (E20)

- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado por operador postal (4-72/servientrega/otro); genera la guía y estado `registrado`.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — envíos del radicado / detalle con historial de eventos.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — actualiza el estado de entrega (callback/sondeo del operador). Máquina de estados `registrado→en_transito→entregado|devuelto|fallido`; `409` si la transición es inválida.

### Cuentas de correo (E19, `USUA_PERM_ADMIN`)

Sustituyen a la cuenta única de las antiguas variables `IMAP_*` (ya retiradas), que no podía expresar lo que exige el Acuerdo AGN 060/2001 art. 13 —«las entidades […] asignarán responsabilidades de acuerdo con la cantidad de cuentas habilitadas»— y guardaba la contraseña en claro en el entorno.

- `POST|GET /api/v1/email/apps-oauth` — **aplicación OAuth que la ENTIDAD registró** en su propio Google Cloud o Azure AD. `{proveedor, client_id, client_secret, azure_directory_id?, redirect_uri, notas?}`; una fila por proveedor (`409 app_ya_registrada`), y la misma sirve para todas las cuentas de ese proveedor. Microsoft exige `azure_directory_id`; Google no lo usa (`422` en ambos sentidos). El `client_secret` se cifra en reposo y **nunca se devuelve**: la respuesta solo dice `tiene_secreto`.

  > **Por qué la registra la entidad y no el despliegue.** Google exige verificación y una **evaluación de seguridad CASA anual** por un tercero acreditado a cualquier aplicación que pida el scope `https://mail.google.com/` (el que cubre IMAP), pero **exime a las internas**: «_the app is only used by people in your Google Workspace or Cloud Identity organization […] its OAuth Consent Screen must be configured for internal use_». Con una aplicación central de OrpycaMCP habría que sostener esa acreditación, y OrpycaMCP es software que cada institución despliega. **No se pide en el alta de la entidad**: al crearla nadie sabe aún qué correo usará, quien la da de alta rara vez tiene los permisos de Azure o Google Cloud a mano, y muchas entidades usarán IMAP de hosting sin necesitarlo.

- `POST|GET /api/v1/email/cuentas` — buzones. Tres tipos: **institucional** compartida (con funcionarios asignados), **personal** del funcionario y varias de recepción a la vez. `carpeta_origen` es el **filtro humano** —la carpeta a la que alguien arrastra lo que sí se radica, dejando fuera publicidad y spam— y `carpeta_radicados` a dónde se mueve el correo tras radicarlo; no pueden ser la misma. `modo` es `asistida` (un funcionario revisa y radica, con lo que existe el «funcionario responsable del trámite» del art. 2) o `automatica`. `direccion` y `usuario_login` son campos distintos: el login puede ser `DOMINIO\usuario` o solo la parte local.

  La **credencial de una cuenta personal no se guarda en absoluto** — se pide al entrar y vive en la sesión; custodiarla convertiría al sistema en titular del correo entero de un funcionario. Las institucionales se cifran con AESGCM y AAD `tenant|cuenta`. Crear una cuenta `gmail` o `microsoft` con contraseña se rechaza con la razón: esas plataformas ya no la aceptan por IMAP.

  **Visibilidad**: una cuenta personal la ve solo su dueño —**ni el administrador**—, y una institucional, sus funcionarios asignados más el administrador. El recorte va en el `WHERE`, y `404` no distingue «no existe» de «no la alcanzas».

- `POST /api/v1/email/cuentas/{id}/probar` `{password?}` — abre una sesión y **devuelve las carpetas del buzón**, sin tocar ningún correo. Las carpetas son la mitad del valor: la de filtrado se elige de esa lista en vez de escribirse a mano, y una errata se manifestaría como «la ingesta no trae nada», indistinguible de «no ha llegado correo». `password` solo para cuentas personales.

- `POST /api/v1/email/cuentas/{id}/oauth/iniciar` — devuelve `{authorization_url}`, la URL del proveedor a la que mandar al administrador. Lleva **PKCE S256** (RFC 7636) y un `state` de un solo uso (RFC 6749 §10.12), y `login_hint` con la dirección del buzón: sin él, quien tenga varias cuentas abiertas autoriza con la que el proveedor elija y el token queda emitido para el buzón equivocado. Para Google añade `access_type=offline` y `prompt=consent`, sin los cuales **no** hay token de refresco y el acceso caducaría en una hora.

- `POST /api/v1/email/cuentas/{id}/oauth/canjear` `{code, state}` — cierra el flujo y guarda el token de refresco cifrado. El `state` se valida y se marca usado **en el mismo `UPDATE`**, con cuatro condiciones (un solo uso, no caducado, misma cuenta, mismo iniciador), de modo que una redirección reproducida no encuentra fila. `400 state_invalido` no dice cuál de las cuatro falló. Lo invoca la propia aplicación tras recibir la redirección, con la sesión ya establecida, para no abrir superficie sin autenticar en el gateway.

  Scopes: Google `https://mail.google.com/`; Microsoft `https://outlook.office.com/IMAP.AccessAsUser.All` **más** `offline_access` — no `outlook.office365.com`, que es el del flujo de credenciales de aplicación y produce un token que el servidor IMAP rechaza.

**Bandeja del buzón (E19).** El espejo del buzón en la base, que es lo que permite que varios funcionarios trabajen la misma cuenta sin pisarse. Gate `PERM_RADI`; el recorte de qué cuentas alcanza cada quien es el mismo de arriba, en el `WHERE`.

- `POST /api/v1/email/cuentas/{id}/bandeja/sincronizar` `{password?}` — trae de la carpeta de filtrado los correos que aún no están en la bandeja. Primero lista identificadores —que no descarga nada— y solo baja los que faltan: sondear un buzón sin novedades no mueve un byte de mensaje. La identidad estable es el `Message-ID` (RFC 5322 §3.6.4), no el `uid`, que solo identifica mientras el `UIDVALIDITY` de la carpeta no cambie (RFC 3501 §2.3.1.1); antes de traer o mover se comprueba, o una restauración del buzón radicaría **otro** correo bajo este asunto. La fecha de recibo sale del `INTERNALDATE` del servidor de la entidad, no de la cabecera `Date`, que la pone el cliente del remitente y puede fecharse en el pasado para ganar plazo.

- `GET /api/v1/email/cuentas/{id}/bandeja?estado=` — los correos, FIFO por fecha de recibo. `X-Total-Count`.

- `GET /api/v1/email/cuentas/{id}/bandeja/estado` — pendientes, ocupados y `ultima_novedad`, para que la pantalla sepa si tiene que recargar sin traerse la lista entera.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/reservar` — reserva de 15 minutos. Es lo que impide que dos funcionarios radiquen el mismo correo dos veces: la reserva se toma en el `WHERE`, así que el «cero filas» distingue «lo tomé» de «alguien se me adelantó» sin ventana de TOCTOU.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/liberar` — suelta la reserva propia; **no toca la de otro**, y por eso responde `204` tanto si soltó algo como si no había nada que soltar: quien abre un correo, ve que no le corresponde y cierra, no debe dejarlo bloqueado un cuarto de hora para toda la ventanilla.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/radicar` — crea la Entrada. Orden por reversibilidad: primero los binarios (anexos y el `.eml` original, que Ley 527/1999 art. 12 obliga a conservar en su formato con origen, destino, fecha y hora), después el radicado, después el movimiento a la carpeta «Radicados», y por último la constancia al remitente si el buzón la tiene activada. Vuelve a comprobar la reserva antes de cerrar.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/descartar` `{motivo}` — aparta un correo que llegó a la carpeta y no debía radicarse. Exige reserva y motivo, y deja asiento `email.mensaje.descartado` en `audit_log` **en la misma transacción**: no es best-effort. Decidir que una comunicación recibida **no** se registra es tan decisión como registrarla —de hecho es la que hace desaparecer el documento del archivo—, y hasta aquí solo dejaba un `UPDATE` sobre una tabla corriente, reescribible después sin rastro. El asiento **no** lleva el asunto ni el remitente: un correo descartado nunca se radicó, así que no tiene nivel de clasificación con el que recortar su lectura, y en `audit_log` lo vería todo `USUA_PERM_ADMIN`. El motivo sí, porque es el texto de la decisión, no el del documento.

> **Ruta heredada retirada.** Existieron `GET /api/v1/ingest/email/estado` y `POST /api/v1/ingest/email/poll`, que operaban sobre un único buzón declarado en el entorno con las variables `IMAP_*` y radicaban de golpe todo lo no leído. Están retiradas junto con esas variables: un buzón por despliegue no expresa lo que exige el Acuerdo AGN 060/2001 art. 13, la contraseña vivía en claro en el entorno, y radicar la bandeja entera radicaba también la publicidad. Los sustituyen los endpoints de cuentas y bandeja de arriba, donde la entidad separa lo que se radica de lo que no.

### `POST /api/v1/public/{tenant}/pqrs` (radicación ciudadana, Ley 1755/2015)

**Sin autenticación.** Un ciudadano radica una PQRS: `{ "tipo": "peticion|queja|reclamo|sugerencia|denuncia", "nombre", "identificacion"?, "email"?, "asunto", "descripcion" }`. Crea un radicado de Entrada (`metadata.canal=pqrs_ciudadano`) y devuelve `{radicado_id, tracking_number, verification_code}` para seguimiento posterior con el endpoint de verificación.

### `GET /api/v1/public/{tenant}/verify/{code}` (consulta pública, E13)

**Sin autenticación.** Verifica la trazabilidad de un radicado por su código de verificación (token no adivinable, distinto del número de radicado). El tenant viaja en la ruta. Devuelve solo información NO sensible: `tracking_number`, `doc_type`, `status`, `registered_at`, `dest_dept`. `404` si el código no existe; `400` si el slug de tenant es inválido.

### `GET /api/v1/reports/radicados`

Resumen estadístico de radicados del tenant (E09): `total` y conteos `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia`. Filtros opcionales `?date_from=&date_to=`. Agregaciones SQL nativas (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`

Exporta el resumen en **CSV** (`seccion,clave,valor` con secciones tipo/estado/mes/dependencia) — para reportes periódicos al AGN/entes de control. Mismos filtros `?date_from=&date_to=`.

```json
{
  "total": 8,
  "by_doc_type": { "E": 5, "S": 3 },
  "by_status": { "registered": 6, "archived": 2 },
  "by_month": [
    { "month": "2024-01", "count": 4 },
    { "month": "2024-02", "count": 4 }
  ]
}
```

### `GET /api/v1/reports/indice-reservado` · `.csv`

**Índice de información clasificada y reservada** (E08, Ley 1712/2014 art. 20 + Decreto 1081/2015). Registro de todos los radicados `nivel_seguridad ≥ 2` con la metadata del acto de clasificación. Gate **`PERM_RECLASIFICAR`**. **CONTENT-FREE** (nunca expone el asunto/contenido) y **ORTOGONAL al clearance** (el registro es COMPLETO por mandato legal — es una publicación de metadata, no una lectura de contenido). Mapeo: `nivel 2 = reservada → art. 19`, `nivel 3 = clasificada → art. 18`. Cada ítem: `{numero_radicado, tipo_radicado (E/S/I, NO serie TRD), nivel_seguridad, clasificacion, excepcion_ley_1712, fundamento_juridico, causal_reserva, plazo_reserva_meses, fecha_clasificacion, fecha_radicacion}`. La metadata se puebla al **radicar clasificado** o **reclasificar** (ambos exigen `fundamento` al pasar a nivel≥2, `422` si falta — Ley 1712 art. 19/28) y se limpia al desclasificar. El `.csv` neutraliza inyección de fórmulas en los campos de texto libre. La generación deja una entrada agregada en `audit_log` (RF-BUS-10).

### `GET /api/v1/search`

Búsqueda avanzada de radicados (E09, ADR-014: PostgreSQL FTS). Texto completo con ranking (`tsvector`/`ts_rank`) + difuso (`pg_trgm`), combinable con filtros estructurados.

Busca en: `tracking_number` (peso A), `subject` (B), `sender_name` / `sender_entity` (C), `dest_dept` / `observations` (D).

**Query params** (todos opcionales): `q` (texto, min 2 chars — **opcional**: sin texto es una consulta filtrada ordenada por fecha), `doc_type`, `status`, `date_from`/`date_to` (rango sobre `registered_at`), `dest_dept_code`, `meta.<campo>=valor` (filtro por metadato, contención JSONB/GIN), `page`, `size`. Cabecera `X-Total-Count`.

**Control de acceso (RF-SEG-08 / RF-BUS-04):** además del aislamiento por tenant (`search_path`), los resultados se acotan por la **clasificación** del radicado: cada usuario solo ve los de `nivel_seguridad ≤` su _clearance_ (nivel máximo accesible = MAX de la matriz rol→nivel `role_clearance` sobre sus grupos; `is_root` accede a todo). El clearance lo resuelve la BD por request (ADR-013); política _fail-closed_ a PUBLICA si no hay identidad o clearance asignado. La **misma acotación** se aplica al listado `GET /api/v1/documents` y a la búsqueda de expedientes `GET /api/v1/expedientes/search`.

```bash
GET /api/v1/search?q=tutela&doc_type=E&date_from=2024-01-01&meta.area=juridica
```

```json
// Response 200
{
  "query": "tutela derechos",
  "total": 3,
  "page": 1,
  "size": 20,
  "hits": [
    {
      "id": "uuid",
      "tracking_number": "2024-ICETEX-E-000042",
      "doc_type": "E",
      "subject": "Acción de tutela derechos fundamentales",
      "sender_name": "María García",
      "status": "registered",
      "registered_at": "2024-06-01T10:30:00Z",
      "rank": 0.756
    }
  ]
}
```

---

## Workflow Rules Engine — workflow-service

> Configura reglas de enrutamiento automático sin código. Cuando llega un radicado, se evalúan las reglas activas por orden de prioridad.

Requiere `X-Tenant-Slug` (y `X-User-Id` para registrar el autor).

### `POST /api/v1/workflow/rules`

Crear una regla. Dos formas de condición (compatibles):

- **Motor con operadores** (`conditions` + `match_mode`): lista de `{field, op, value}` combinada por `all` (AND) o `any` (OR). Campos: `doc_type, doc_class, dest_dept, origin_dept, subject, sender_name, sender_entity, pages`. Operadores: `eq, ne, contains, not_contains, in, gt, lt, gte, lte, regex`.
- **Legada** (columnas fijas `doc_type/doc_class/dest_dept/subject_contains`, AND): se usa si `conditions` está vacío.

```json
// Request (motor con operadores)
{
  "name": "Tutelas voluminosas → Jurídica",
  "priority": 10,
  "match_mode": "all",
  "conditions": [
    { "field": "doc_type", "op": "in", "value": "E,S" },
    { "field": "subject", "op": "contains", "value": "tutela" },
    { "field": "pages", "op": "gt", "value": 10 }
  ],
  "assign_to_dept": "Dirección Jurídica",
  "assign_notes": "Responder en máximo 10 días hábiles"
}
// Response 201 — WorkflowRuleResponse con id, conditions, match_mode, timestamps
```

### `GET /api/v1/workflow/rules`

Listar reglas. Query: `?active_only=true&page=1&size=50`.

### `GET /api/v1/workflow/rules/{id}`

Obtener regla por UUID.

### `PATCH /api/v1/workflow/rules/{id}`

Actualizar parcialmente (ej: cambiar prioridad, activar/desactivar).

### `DELETE /api/v1/workflow/rules/{id}`

Eliminar regla. Responde `204`.

### `POST /api/v1/workflow/rules/evaluate`

Evaluar reglas contra un radicado (retorna la primera que coincida).

```json
// Request
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000042",
  "doc_type": "E",
  "subject": "Acción de tutela por mora en servicio"
}

// Response 200 — si hay coincidencia
{ "matched": true, "rule_id": "uuid", "rule_name": "Tutelas → Dirección Jurídica", "assign_to_dept": "Dirección Jurídica", "assign_notes": "..." }

// Response 200 — sin coincidencia
{ "matched": false }
```

### `POST /api/v1/workflow/rules/evaluate/batch`

Evalúa varios radicados (1-500) contra el mismo conjunto de reglas en una sola llamada.

```json
// Request
{ "items": [ { "radicado_id": "uuid", "tracking_number": "T1", "doc_type": "E" }, ... ] }
// Response 200
{ "results": [ { "matched": true, "radicado_id": "uuid", "assign_to_dept": "...", ... }, ... ] }
```

## Asistente conversacional — contrato front↔gateway (E18/E22, ADR-020)

> **Corrección de una afirmación caducada (2026-08-07):** este apartado se titulaba
> "contrato definido, backend PENDIENTE" y declaraba el `mcp-server` como _scaffold_.
> Es **falso desde E18**: el backend está implementado y documentado más arriba
> (véase [Asistente conversacional (E18, ADR-019/ADR-020) — vía gateway](#asistente-conversacional-e18-adr-019adr-020--vía-gateway)),
> con un loop LLM real que ejecuta las tools de solo lectura _as-the-user_. Se
> conserva esta sección porque documenta la otra mitad del contrato —**lo que el
> proxy SSR del frontend hace con la respuesta**—, que no está en la del backend.
>
> El `503 assistant_unavailable` sigue existiendo, pero ya no significa "el backend
> no está construido": lo devuelve el propio mcp-server cuando le falta
> `ANTHROPIC_API_KEY` o el SDK falla (degradación honesta, RF-FIR-15). El proxy
> SvelteKit traduce además a ese mismo código cualquier ausencia de ruta
> (404/501/5xx/conexión rehusada) — la UI muestra "asistente no disponible", nunca
> una respuesta simulada.

### `POST /api/v1/assistant/message`

Envía un turno del usuario y devuelve la respuesta del asistente. Autorización: **autenticado** (sin permiso `--op` fino; cada acción que el asistente dispare hacia adelante se re-valida contra el RBAC/clearance del usuario en el servicio que la ejecute). Headers de identidad (`X-Tenant-Slug`/`X-User-Id`/`X-Username`) los inyecta el proxy SSR desde `locals`, nunca del body.

```json
// Request  (allowlist en el proxy: solo estos dos campos)
{ "mensaje": "string (≤4000, trim)", "conversation_id": "uuid (opcional)" }
// Response 200
{
  "conversation_id": "uuid",
  "message": { "id": "uuid", "role": "assistant", "texto": "respuesta (AUTORITATIVO)", "formato": "markdown|text", "ts": "ISO-8601" }
}
```

- `texto` es la **fuente de verdad**; `formato` es una pista de render. El proxy convierte `formato:"markdown"` a HTML con `marked` y lo **sanea server-side** (`sanitizeHtml`) antes de cruzar al cliente — el output del asistente es **contenido no confiable** (XSS/prompt-injection). El backend **no** debe enviar HTML; si lo hiciera, el proxy lo ignora.
- Reservado para el futuro (el slice actual los ignora sin romperse): `acciones[]`, `citations[]`, `usage`.
- Preparado para **streaming** (no construido): un `POST .../message/stream` (NDJSON/SSE) con el mismo `conversation_id` y `message.id` estables por turno permitiría _appendear_ deltas al mismo turno sin cambiar el shape.
- **Nota de contrato (a reconciliar)**: el proxy envía `X-Username` desde el claim `preferred_username` (el `SessionUser` del front no tiene `username`).
