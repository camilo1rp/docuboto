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

## Rutas transversales de todos los servicios

- `GET /health` · `GET /health/ready` — sonda de vida y de disponibilidad, en **los once** servicios. `/health` responde si el proceso está en pie; `/health/ready` además comprueba sus dependencias (pool de PostgreSQL, Redis, almacén de objetos según el servicio). En los servicios con consumidor de eventos (workflow, signature, notification, knowledge), `/health/ready` añade `checks.dead_letter` (`ok`/`degraded`: `degraded` si la DLQ de Redis tiene entradas o el último barrido de reclamación aparcó mensajes) y un objeto `consumer` con `dead_letter_stream_len`, `reclamados`, `dead_lettered_ultimo_barrido`, `dead_lettered_total`, `ultimo_barrido` y `reinicios`; solo informa, nunca decide el `503`. El `/health/ready` de **auth-service** añade `audit_particiones` (`ok`/`degraded`/`unknown`): `degraded` —y `"degraded": ["audit_particiones"]` en la raíz— si falta la partición del mes siguiente de `audit_log` (el cron externo de `scripts/audit_partitions.py` dejó de correr); también solo informa y responde `200`. Las usa el `healthcheck` de Docker Compose y el arranque ordenado: un servicio que responde `/health` pero no `/health/ready` está vivo y **todavía no debe recibir tráfico**, y confundirlas produce el arranque en cascada que parece un fallo intermitente.
- `GET /api/v1/openapi.json` — el esquema OpenAPI del servicio, servido por FastAPI. En el gateway es el que agrega la documentación interactiva de `/docs`.

**Endpoints internos (`/internal/...` y sufijo `-internal`)**: sellados con `X-Internal-Token` (D-02) y **no enrutados por el gateway** —esa cabecera no está en `FORWARDED_REQUEST_HEADERS`—, así que no son alcanzables desde fuera de la red interna por mucho que se conozca la ruta. Se documentan uno a uno en la sección de su servicio; los que existen hoy son:

| Endpoint interno | Servicio | Para qué |
|---|---|---|
| `POST /api/v1/internal/documentos/anexos` | document | Inventario TOTAL de anexos de un lote de radicados (acto de sistema, sin recorte de clearance) — lo pide archive al exportar o empaquetar |
| `POST /api/v1/internal/documentos/metadata` | document | Metadata (`subject`/`doc_type`) de radicados **visibles**, para enriquecer el manifiesto de exportación — este sí recorta por clearance. Desde 2026-10 devuelve además **`tracking_number`** y **`doc_class`**: archive-service los coteja al vincular un radicado (T6 / AREA-07-02, AREA-06-08), y sin `tracking_number` falla cerrado, así que document-service se despliega primero |
| `POST /api/v1/internal/documentos/purgar` | document | **Destruye** radicados y sus anexos por disposición final (RF-TRD-07 fase 5) |
| `GET /api/v1/internal/documentos/radicados/{ref}/huella` | document | SHA-256 del **documento principal** de un radicado (`ref` = número o UUID; SOLO el anexo `es_principal`, el más reciente si hubiera varios; **un PDF de soporte no cuenta**: sin principal, `409 radicado_sin_documento`; la huella la calculó storage al subirlo). `404 radicado_not_found` · `409 radicado_sin_documento`/`anexo_sin_huella` · `503 huella_no_verificable` (el cuerpo del 503 no reenvía lo que respondió storage). Lo consulta signature-service al abrir y al firmar una cadena (AREA-04-09) |
| `GET /api/v1/internal/expedientes/{ref}/indice/huella` | archive | SHA-256 (`xml_sha256`, C14N) del **índice electrónico vigente** de un expediente (`ref` = código o UUID; última versión). `404 expediente_not_found` · `409 indice_no_disponible`. Router propio con gate `X-Internal-Token` (`INTERNAL_SERVICE_TOKEN`, = `ARCHIVE_INTERNAL_TOKEN` de signature); sin token configurado, 401 fail-closed (AREA-04-09) |
| `POST /api/v1/storage/internal/artefacto` | storage | Sube un artefacto producido por el SISTEMA (índice firmado, `.eml` original), con su propia lista de formatos — no la de anexos |
| `POST /api/v1/storage/internal/anexo-externo` | storage | Anexo recibido por la ventanilla única pública |
| `POST /api/v1/storage/internal/purgar-disposicion` | storage | **Borrado físico** del almacén de objetos + fila, con gate WORM |
| `POST /api/v1/storage/internal/purgar-huerfanos` | storage | **Borrado seguro** de binarios huérfanos de una importación fallida: solo lo creado por esa importación y sin ninguna referencia en el tenant (deduplicación por SHA-256) y que **ningún `upload` reutilizó por deduplicación** (`files.dedup_hit_at`: lo compartido se conserva siempre; una tabla de referencias ausente falla cerrado); resultado por archivo |
| `GET /api/v1/storage/files/{id}/content` | storage | Bytes para procesar servicio-a-servicio (mediación de descarga, AIP) |
| `POST /api/v1/preservacion/proteger-indice-internal` · `proteger-artefacto-internal` · `renovar-retencion-internal` | storage | Cableado y renovación WORM desde la reconciliación desatendida |
| `POST /api/v1/preservacion/aip-internal` | storage | Empaquetado del AIP conducido por la reconciliación (`SYSTEM_RECONCILER_ID`) |
| `POST /api/v1/preservacion/export-zip-internal` | storage | Exportación del expediente en ZIP pedida por archive-service |
| `GET /internal/signing-key/revocation-status` · `POST /internal/signing-key/sign-digest` | auth | Estado de revocación y firma de un digest con la clave custodiada |
| `GET /api/v1/signature/internal/circuitos/{tipo}/{id}` | signature | Estado del circuito de aprobación (lo consulta document-service antes de aprobar un borrador) |
| `POST /api/v1/signature/internal/circuitos/{tipo}/{id}/invalidar` | signature | Deja sin efecto los circuitos vivos de un objeto **cuyo texto cambió**; lo pide document-service al editar un borrador ya sometido a firma |
| `POST /api/v1/signature/internal/sign-indice` | signature | Sellado XAdES diferido del índice/acta

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
  "roles": ["operator"],
  "expires_in": 287
}
```

`expires_in` son los segundos que le quedan al token (`exp − ahora`, ≥ 0): el api-gateway acota con él el TTL de su caché de validación (Redis), y **no cachea** si falta o es ≤ 0. Las entradas de esa caché se sellan con HMAC-SHA256 (`AUTH_CACHE_MAC_SECRET`, secreto exclusivo del gateway); sin secreto la caché queda apagada. El token debe ser un *access token* (`typ=Bearer`) emitido por un cliente declarado (`azp` ∈ `ALLOWED_AZP`, por defecto `orpycamcp-frontend,api-gateway`) y traer `exp`, `iat`, `iss` y `sub`; un ID token o el de otro cliente da `valid:false`.

### `GET /api/v1/auth/me`

Información del usuario autenticado extraída del token, incluyendo sus permisos efectivos resueltos desde la BD del tenant (D-07).

Requiere `Authorization: Bearer <token>`. Si se proporciona `X-Tenant-Slug`, se resuelven los permisos desde la BD del tenant. Sin esa cabecera (superadmin / llamada directa), `permissions` devuelve `[]` y `permission_levels` `{}` (fail-closed). Una cabecera mal formada es `400 invalid_tenant`. **Entidad sin schema** (registrada como `pending` antes de `init_tenant`, o inexistente) ⇒ `200` con `permissions` y `permission_levels` vacíos y `user_id` = `sub` (B7): es el mismo caso de «usuario no aprovisionado» y no se distingue de él (no es un oráculo); antes era un `500 UndefinedTableError`, y `/me` es el arranque del frontend.

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
- `permission_levels`: **mismas claves que `permissions`**, con el **nivel CRUD** de cada una — `0` ninguno · `1` leer · `2` editar · `3` crear/borrar · `4` nivel_1 · `5` nivel_2 (`auth_group_permissions.crud`, ver `migrations/tenant/001_rbac.sql`). ROOT resuelve `USUA_PERM_ROOT: 5`. Es el campo que un espejo RBAC de interfaz **debe** consultar. **Aviso de alcance real** (medido en B13, septiembre de 2026): `require_permission` tiene `min_crud=1` por defecto, y **85 de las 135 escrituras con gate no suben ese valor** — es decir, hoy las puede ejecutar quien tenga el permiso en modo lectura. Los gates que SÍ exigen nivel (`min_crud=2` editar, `min_crud=3` crear/borrar, RF-SEG-03) niegan con `403` nombrando el nivel que falta; los demás no niegan nada. La lista de las 85 está declarada, endpoint por endpoint, en `shared/tests/test_escritura_exige_nivel.py`, que impide que crezca. Hasta que se cierre, un espejo de interfaz no puede dar por hecho que el backend rechazará una escritura solo porque el nivel sea 1. `permissions` se conserva por compatibilidad con los consumidores existentes; ambos campos son **aditivos entre sí**, no alternativos.
- Si el usuario no está aprovisionado en el tenant, `permissions` es `[]` y `permission_levels` es `{}` (fail-closed). Ninguno de los dos se omite ni se devuelve `null`: un consumidor nunca debe poder leer su ausencia como "sin restricción".

### 2FA TOTP — credencial del usuario (E06 Inc.6, RF-FIR-13)

El secreto TOTP (RFC 6238) es una **credencial de identidad durable** y vive en auth-service (tabla `auth_user_totp` por tenant, cifrada con **AESGCM**; AAD `tenant:{slug}:user:{id}:totp`). El secreto en claro se revela **una sola vez** en el `otpauth://` de `enroll` (`Cache-Control: no-store`); nunca vuelve a salir. Todos son auto-servicio (bearer del propio usuario).

- `POST /api/v1/auth/me/totp/enroll` — genera el secreto (20 bytes, base32) en estado `pendiente_activacion` y devuelve `{otpauth_uri, estado}` (para QR en un autenticador). Re-enrolar sobre un TOTP **activo** exige step-up (un código actual válido): `409 totp_ya_activo` / `401 step_up_invalido`.
- `POST /api/v1/auth/me/totp/activate` `{code}` — activa la credencial con un primer código válido → `{estado:"activo", activated_at}`. `409 totp_no_pendiente`; `401 codigo_invalido` (suma a `failed_attempts`); `423 totp_bloqueado`.
- `GET /api/v1/auth/me/totp` — `{estado, activated_at, locked_until}`. **Nunca** devuelve el secreto.
- `DELETE /api/v1/auth/me/totp` `{step_up_totp_code | step_up_recovery_code}` — revoca la credencial **activa** exigiendo step-up → `{estado:"revocado"}`. `401 step_up_invalido`; `404 no_totp`.
- `POST /api/v1/auth/me/totp/recovery-codes` `{step_up_totp_code | step_up_recovery_code}` — **regenera** el lote de códigos de recuperación, invalidando el anterior por completo. `200 {recovery_codes:[…]}` con `Cache-Control: no-store`. `409 totp_no_activo`; `401 step_up_invalido`; `423 totp_bloqueado`.
- `POST /api/v1/auth/users/{user_id}/totp/revoke` `{motivo}` — **restablecimiento administrativo** (`USUA_PERM_ADMIN` nivel 3): retira el segundo factor de **otra** persona para que pueda volver a registrarlo ella misma desde `/perfil`, y anula sus códigos de recuperación en la misma transacción. `200 {estado:"revocado", usuario_id, estado_previo, codigos_recuperacion_eliminados}`; `404 no_totp`; `409 restablecimiento_propio_no_permitido`; `422` si el `motivo` no llega a 8 caracteres. **Nunca** devuelve un secreto ni enrola en nombre de nadie — quien pudiera enrolar por otro tendría de hecho su firma personal. El asiento `totp.restablecido_por_admin` es **atómico con la baja** (misma transacción: si el asiento falla, el factor no se retira; igual `totp.desenrolado*` en el auto-servicio) y se atribuye al **administrador** (`actor`), con el usuario afectado en `object_ref` y el `motivo` en el `payload`.
  - **Por qué existe, y por qué no se resolvió admitiendo códigos de recuperación al firmar**: los códigos de recuperación reponen el factor pero **no sirven para firmar** (`/internal/totp/verify` solo clasifica un TOTP), y `enroll`/`DELETE /me/totp` exigen el código actual o uno de recuperación. Quien perdía el teléfono **y** los códigos quedaba encerrado, sin más salida que tocar la base de datos a mano. Dejar que un código de papel, de vida larga, firmara habría rebajado el segundo factor de la firma justo en el acto que da fe: se repone el factor, no se degrada.
  - **No se puede usar sobre uno mismo** (`409`): un administrador que se restableciera el suyo podría retirárselo sin demostrar posesión del dispositivo y enrolar otro acto seguido — quien robase su sesión se quedaría con el segundo factor de su firma personal. Hacen falta **dos personas**; el propio administrador usa `/perfil` como todo el mundo.

**Códigos de recuperación (E06, migración 015).** El TOTP es un factor de **posesión**, y hasta esta migración **todas** sus salidas —revocarlo, reenrolarlo, y revocar la clave personal de firma— exigían un código TOTP vigente, es decir, exigían el mismo teléfono que se había perdido: quien lo perdía quedaba **encerrado**, sin más salida que una intervención manual sobre la base. Ahora `activate` devuelve **10 códigos de un solo uso** (`recovery_codes`, `Cache-Control: no-store`) y cualquiera de ellos vale como `step_up_recovery_code` en `enroll` (reenrolar) y en `DELETE /me/totp` (revocar).

- Se guardan **hasheados** (scrypt con sal por código), no cifrados: a diferencia del secreto TOTP, el servidor no necesita recuperarlos en claro, solo comprobarlos — así una copia de la tabla no permite autenticarse. Se muestran **exactamente una vez**; `GET /me/totp` devuelve `recovery_codes_restantes` (un conteo), nunca los códigos.
- **Un solo uso**, garantizado por un `UPDATE ... WHERE used_at IS NULL`: dos peticiones simultáneas con el mismo código compiten por la fila y solo una la gana.
- Se emiten **al activar**, no al enrolar (hasta entonces la credencial no protege nada) y en la **misma transacción** que la activación — no existe un instante con TOTP activo y sin vía de recuperación. Revocar o reenrolar **borra** el lote en la misma transacción.
- **Frontera deliberada: no sirven para FIRMAR.** `POST /internal/totp/verify`, que signature-service invoca al firmar, **no** los acepta (su `code` está validado como TOTP de 6 dígitos). Un código de recuperación está escrito en papel o en un gestor de contraseñas: no es posesión de un dispositivo, y admitirlo como segundo factor de un acto de firma debilitaría la atribución que RF-FIR-13 persigue. Recuperar el acceso y firmar son actos distintos y se exigen pruebas distintas.
- _(Interno, servicio-a-servicio en la red Docker, **no** enrutado por el gateway)_ `GET /internal/totp/status` — (AREA-04-02) `{activo, user_id}`: ¿el dueño del bearer tiene un TOTP **activo**? Content-free; doble gate `X-Internal-Token` + identidad derivada del bearer; sin usuario aprovisionado o sin tenant → `activo=false` sin `user_id`. Lo consulta signature-service para vetar el OTP-correo en actos de nivel ≥ 2. `POST /internal/totp/verify` `{code, contexto?}` — lo llama signature-service al firmar; **mismo doble gate** que `/status`: `X-Internal-Token` (`SIGNING_KEY_INTERNAL_TOKEN`; ausente ⇒ `401`, erróneo ⇒ `403`, sin configurar ⇒ `503`) **más** el bearer del usuario, comprobados antes de leer el bearer (la identidad sigue saliendo de él) (la respuesta válida lleva ahora **`user_id`**, el `auth_users.id` de quien se verificó, que signature-service cruza con el actor — AREA-04-05; `GET /internal/signing-key/status` lleva el mismo `user_id`); deriva la identidad del **bearer reenviado** (no de un `user_id` de cuerpo → sin IDOR), honra `locked_until` antes de comparar, y avanza el anti-replay `last_timestep` en un único `UPDATE` atómico. Lockout tras `TOTP_MAX_FAILED_ATTEMPTS` (default 5) durante `TOTP_LOCKOUT_SECONDS` (default 900). Ventana de skew ±`TOTP_SKEW_STEPS` (default 1).

**Clave de firma PERSONAL PKI (E06/E17 F4, Inc.1 — custodia servidor, `acreditado=false`).** La clave privada de firma de cada usuario vive **solo en auth-service** cifrada AESGCM (KEK `SIGNING_KEY_ENCRYPTION_KEY` **separada** de la del TOTP), y NUNCA sale del servicio.

- `POST /api/v1/auth/me/signing-key/enroll` — genera el par RSA-2048 en memoria, cifra la privada, emite un **CSR** (subject = identidad del usuario + `cargo` mejor-esfuerzo desde grupos RBAC) y queda `pendiente_emision`; devuelve **solo** `{csr_pem, estado}` — **nunca** la clave privada. Exige `PERM_FIRMA`. Un re-enroll revoca la clave activa previa (endurecimiento de step-up diferido a Inc.2). La emisión del cert es **out-of-band** (`scripts/gen-user-cert.sh`, corre con `ca.key` fuera de todo contenedor; sub-CA de usuarios distinta de la del sello).
- `GET /api/v1/auth/me/signing-key` — `{estado, certificado_fingerprint?, not_after?}`. **Nunca** devuelve la clave privada.
- `DELETE /api/v1/auth/me/signing-key` `{step_up_totp_code, motivo?}` — **revoca la clave propia** exigiendo step-up TOTP (verifica-y-consume; sin código → `422`) → `{estado:"revocado"}` (E06/E17 Inc.2). Deja asiento `signing_key.revocada` (con `revocado_at`/`motivo`) **atómico** con el UPDATE. `motivo` es un **código cerrado** (RFC 5280: `key_compromise`, `superseded`, `cessation_of_operation`, `affiliation_changed`, `privilege_withdrawn`, `unspecified`; `422` fuera de él; igual en el endpoint de admin). `404` si no hay clave.
- `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` `{motivo?}` — un **admin** (`USUA_PERM_ADMIN` nivel 3) revoca la clave de otro usuario (caso "empleado desvinculado"): el `user_id` objetivo va en el path, el actor sale del token (no confundibles). Asiento atómico con `revocado_por`.
- _(Interno, **no** enrutado por el gateway)_ `GET /internal/signing-key/revocation-status?fingerprint=<hex>` — lo consulta signature-service al verificar una firma personal (`X-Internal-Token` + `X-Tenant-Slug`): devuelve **solo** `{status, revocado_at?}` (`status ∈ vigente|revocado|desconocido`) — nunca cert ni clave. Aislado por tenant: un fingerprint de otro tenant / inexistente → `desconocido` (fail-closed, nunca `vigente`). La activación pendiente→activo persiste `user_ca_fingerprint`+`certificado_fingerprint` y valida `leaf.verify_directly_issued_by(sub_ca)` fail-closed.
- _(Interno, **no** enrutado por el gateway)_ `POST /internal/signing-key/sign-digest` `{digest_b64, alg:"RSA-SHA256", prehashed:true, totp_code}` — lo llama signature-service al firmar un acto personal de la cadena: deriva el usuario del **bearer reenviado** (sin IDOR), **verifica-y-consume el TOTP atómicamente** con la firma, descifra la clave **solo en memoria** (vida mínima), firma el digest, y devuelve `{signature_value_b64, certificado_pem, certificado_fingerprint, serial, not_after}` — la privada nunca se devuelve. Es la interfaz "firmar un digest" de un HSM (seam a firma cualificada). `X-Internal-Token` comparado con `compare_digest`. La activación pendiente→activo deja asiento `signing_key.activada` atómico.

### Gestión de usuarios RBAC (E14)

- `POST /api/v1/auth/users` · `GET /api/v1/auth/users` (paginado, `X-Total-Count`) · `GET /api/v1/auth/users/{id}/permissions` — administración de usuarios. Todo el router exige `USUA_PERM_ADMIN` (lecturas nivel 1; `POST` nivel 3). `POST` deja asiento `rbac.usuario_creado` en `audit_log`, **atómico** con el INSERT (ver nota de auditoría abajo).
  - **`POST` con `is_root: true` → `403 forbidden` si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md), cierre de hallazgo Alto de auto-escalada). Un `USUA_PERM_ADMIN` no-ROOT nunca puede crear un usuario ROOT; ROOT sí. El primer ROOT de un tenant se aprovisiona fuera de banda (`app/ops/grant_root.py`, no HTTP).
- `GET /api/v1/auth/users/pickable?size=N` — proyección mínima `[{id, username}]` de usuarios **activos**, para selectores de UI (p. ej. el `UserPicker` de cadenas de visto bueno, T-08c). Gateado con **`PERM_RADI` o `USUA_PERM_ADMIN`** (any-of, B6: el administrador de `/admin/flujos` y `/admin/grupos` elige usuario aunque no radique; no gana exposición, ya lista a todos con `GET /users`); no expone `email` ni metadatos administrativos (D-10). `size` en `[1, 500]`. Emite **`X-Total-Count` con los usuarios activos que hay**, no con los que caben en la respuesta: este endpoint **no pagina**, recorta con un `LIMIT`, así que la cabecera es lo único que permite al llamante saber que está viendo un recorte. Sin ella, quien no entrara en la ventana no existía para quien busca — y en un selector eso no se lee como una lista corta, se lee como que esa persona no está en el sistema.

### URD y contexto de dependencia (E08)

- `GET /api/v1/auth/users/{user_id}/urd` · `POST /api/v1/auth/users/{user_id}/urd` · `DELETE /api/v1/auth/users/{user_id}/urd/{urd_id}` — administra las asignaciones Usuario-Rol-Dependencia (una por (usuario, dependencia); una sola principal). Exige `USUA_PERM_ADMIN` (`POST`/`DELETE` nivel 3). `POST`/`DELETE` dejan asiento `urd.creada`/`urd.eliminada`, **atómico** con la mutación.
- `GET /api/v1/auth/context` — contexto del llamante: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — fija la dependencia activa del propio usuario. **No reemite token** (ADR-013): persiste el contexto y lo audita en `usua_historico` (Canal B, no `audit_log`). `400 no_urd_for_dependency` si no tiene URD activa allí.

### Grupos, membresías y permisos (E08)

Todo el router exige `USUA_PERM_ADMIN`: las **lecturas** con nivel 1 y toda **escritura** con **nivel 3** (`403` con el permiso a nivel de lectura: quien lo recibe solo para consultar la auditoría ya no administra RBAC).

- `POST /api/v1/auth/groups` · `GET /api/v1/auth/groups` — alta y listado de grupos. `POST` deja asiento `rbac.grupo_creado`.
- `PATCH /api/v1/auth/groups/{group_id}` `{name?, description?}` — actualiza solo los campos enviados (`400 empty_patch` sin ninguno, `409` por nombre duplicado, `404`); asiento `rbac.grupo_actualizado` con `{campos, antes, despues}`. `DELETE /api/v1/auth/groups/{group_id}` — elimina un grupo **vacío**: `409` si tiene miembros o URD (se comprueba en la misma sentencia que borra, sin ventana), `404` si no existe, `204` con asiento `rbac.grupo_eliminado` (`{name, description}` del grupo borrado). Ambos exigen `USUA_PERM_ADMIN` nivel 3.
- `GET /api/v1/auth/permissions` — catálogo de permisos (`{id, nombre, descripcion}`). **`USUA_PERM_ROOT` se omite del catálogo si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)) — no se lista una opción que el `PUT .../permissions/{id}` de abajo va a rechazar.
- `POST /api/v1/auth/groups/{group_id}/members/{user_id}` · `DELETE .../members/{user_id}` — alta/baja de un usuario en un grupo. Dejan asiento `rbac.miembro_agregado`/`rbac.miembro_removido`. **No-write-up (RF-SEG-08):** el alta de un llamante no-ROOT en un grupo cuyo techo de clearance (`role_clearance`, 1 si no tiene) supera su clearance efectivo da `403 no_write_up`, sin asiento; el gate vive en el `WHERE` del `INSERT`. Ya ser miembro no es error.
- `GET /api/v1/auth/groups/{group_id}/members` (paginado, `X-Total-Count`) — usuarios del grupo. Espejo de lectura del alta/baja anterior.
- `PUT /api/v1/auth/groups/{group_id}/permissions/{permission_id}` `{ "crud": 3 }` — fija el CRUD (0-5) de un permiso para el grupo. Deja asiento `rbac.permiso_grupo_asignado` (payload con `permission_id`/`crud`).
  - **Conceder (`crud > 0`) `USUA_PERM_ROOT` → `403 forbidden` si el llamante no es ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). Revocar (`crud = 0`) siempre está permitido, incluso a un no-ROOT — solo la concesión está bloqueada. Cierra la vía de auto-escalada vía el grupo del que el propio actor es miembro.
- `GET /api/v1/auth/groups/{group_id}/permissions` — permisos asignados al grupo: `[{permission_id, nombre, descripcion, crud}]`. Espejo de lectura del `PUT` anterior.
- `group_id` de un grupo inexistente **o de otro tenant** → `404` indistinguible (el `search_path` ya acota el schema del tenant; nunca `403`, que confirmaría la existencia del grupo ajeno).

**Auditoría de escrituras RBAC/URD (E08, cierra hueco de trazabilidad):** todo `POST`/`PUT`/`DELETE` de esta sección y de "URD" deja asiento en `public.audit_log` **dentro de la misma transacción** que la mutación (`async with conn.transaction()`) — no _best-effort_: si `audit.append` falla, la mutación se revierte; si la librería de auditoría no está instalada, la escritura responde `500 auditoria_no_disponible` **antes** de tocar ninguna fila (mismo patrón fail-closed que `signing-key`/`totp`). `tenant_slug` es obligatorio internamente (`X-Tenant-Slug`, ya exigido por `get_tenant_conn`); el actor es el `X-User-Id` resuelto por el gate.

### Clasificación de seguridad (E08, RF-SEG-08, Ley 1712/2014)

- `GET /api/v1/auth/security-levels` — catálogo de niveles (1 Pública, 2 Reservada, 3 Clasificada).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — fija el nivel máximo accesible de un grupo (exige `USUA_PERM_ADMIN` nivel 3). Deja asiento `clearance.grupo_actualizado` con `{group_id, previous_max_level, new_max_level}` (**atómico**, mismas garantías fail-closed que RBAC/URD arriba) — el nivel anterior permite distinguir una subida de una bajada.
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
- **`object_ref` por código, UUID o índice (AREA-03-07)**: un expediente se nombra en `audit_log` de tres formas (el código los asientos nuevos; el UUID del expediente y el del índice, los viejos, que no se reescriben). Filtrar por el **código** del expediente o por el **número** de un radicado trae también los asientos que lo nombran por UUID, y el del expediente los de su índice (`expediente_indice`); el recorte no-read-up se aplica después sobre la misma fila, así que ensanchar la referencia no ensancha el acceso. Cada asiento nuevo declara su `canal` (`api`/`ui`/`mcp`; `system` en jobs, workers y herramientas de línea de comandos): ya no hay valor por omisión en la librería.
- Con clearance máximo (3 = CLASIFICADA, y ROOT) **no se aplica recorte alguno**: la auditoría se entrega íntegra, que es lo que su habilitación autoriza.
- **Residuo declarado**: `id` es una secuencia monótona, así que los huecos entre ids visibles revelan _cuántas_ entradas se ocultaron y, por los `ts` vecinos, _cuándo_. `/audit/verify` ya no es un segundo canal para ese dato (ver abajo); el `id` del listado sigue crudo: cerrarlo exige un identificador opaco por entrada y migrar a quien lo consume.

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

Recalcula la cadena de hash del tenant y reporta su integridad (ADR-008). Exige `USUA_PERM_ADMIN`. La verificación **no** se recorta por clearance: la cadena solo tiene valor probatorio si se recalcula entera, y sobre un subconjunto siempre daría «rota». La respuesta es content-free, así que no revela nada del objeto: ni actor, ni acción, ni referencia. **Pero el detalle solo es para el clearance máximo (AREA-03-03):** `total` menos el `X-Total-Count` del listado recortado era el número exacto de asientos clasificados ocultos, y los `id` son una secuencia global entre entidades. Quien no tiene el clearance máximo recibe solo `ok` y `total_roturas`; `total`, `verificados` y `broken_id` van `null` y `roturas` vacío.

```json
{
  "tenant_slug": "icetex", "ok": false, "broken_id": 41004,
  "total": 2056, "verificados": 2052, "total_roturas": 4,
  "roturas": [{ "id": 41004, "motivo": "hash+prev_hash" }],
  "roturas_omitidas": 0
}
```

- **Reporta TODAS las roturas, no solo la primera.** `audit_log` es append-only por ley y una rotura no se puede reparar nunca: parando en la primera, una sola fila mala dejaba la verificación inservible para el resto de la vida del sistema —la herramienta dejaba de servir justo después de un incidente— y decía menos de lo que la entidad puede demostrar ante un juez.
- Tras una rotura **se sigue encadenando desde el hash almacenado**, no desde el recomputado: así cada fila se juzga contra su predecesor tal como está escrito y las roturas quedan localizadas, en vez de que una fila mala pinte de rotas todas las siguientes.
- `motivo` ∈ `hash` (el contenido no produce el hash guardado) · `prev_hash` (no encadena con la anterior) · `hash+prev_hash`.
- `roturas` se topa en **100** entradas enumeradas; `roturas_omitidas` cuenta el resto — un registro masivamente corrupto no puede parecer que tiene exactamente 100 problemas. `total_roturas` es la suma.
- `broken_id` (= `roturas[0].id`) se conserva por compatibilidad; los demás campos son **aditivos**.

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

Registra una institución en `public.tenants` (`provisioning_status='pending'`) y deja asiento `tenant.created` en `public.audit_log` en la misma transacción. **No crea el schema** `tenant_{slug}`: el rol de aplicación con el que opera el servicio no ejecuta DDL; la provisión (schema, migraciones, `GRANT USAGE`) es de `scripts/init_tenant.py`, que además activa la entidad.

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

Actualizar `name` o `status`. `slug` y `code` son inmutables. Deja asiento `tenant.updated` (antes/después) atómico con el cambio.

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

- **Dependencias** (organigrama): `GET /api/v1/dependencias` (autenticado) · `POST/PATCH/DELETE /api/v1/dependencias` (**`USUA_PERM_ADMIN` a nivel CRUD 3**; con nivel 1 o 2, `403`) · `GET /api/v1/dependencias/by-codigo/{codigo}` (jerárquicas, autenticado).
- **Catálogos** (lookup): `GET /api/v1/catalogos` (autenticado) — **qué catálogos hay**: devuelve la lista de nombres admitidos, para no tener que adivinarlos ni mantener una copia en el cliente. · `GET /api/v1/catalogos/{catalogo}` (autenticado) · `POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` (**`USUA_PERM_ADMIN` a nivel CRUD 3**) — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
  - **`causales` distingue el acto al que sirve** (`ambito`, migración tenant 008): `devolucion`, `anulacion`, `reasignacion`, `devolucion_postal` o `general`. `GET /api/v1/catalogos/causales?ambito=devolucion` filtra en la fuente, y `ambito` viaja en el cuerpo de `POST`/`PATCH`. Es el único catálogo que lo admite: pedirlo sobre otro responde **422 `ambito_no_aplica`** en vez de ignorarlo en silencio. Una causal sin ámbito declarado queda en `general` y la ofrecen todos los actos.
- **Parámetros**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}` (autenticados) · `PUT /api/v1/config/params/{key}` (**`USUA_PERM_ADMIN` a nivel ≥ 3**: un administrador en modo lectura o edición recibe `403`; el asiento lleva `antes`/`despues`). **Claves cerradas (B6)**: `PUT` solo admite las que siembran las migraciones de tenant (009: `entidad_nombre`, `_sigla`, `_nit`, `_direccion`, `_telefono`, `_email`, `_web`; 010: `firma_personal_require_xades`; 012: `politica_tratamiento_version`, `_url`, `_texto`); cualquier otra da `422 config_param_desconocido`, porque la lectura es abierta a todo el tenant y una clave libre sería un buzón de secretos legible por cualquiera. Cuerpo `{value, motivo?}`; `value: null` da `422 valor_nulo` para cualquier clave (el JSON `null` solo lo escribe la siembra). **`firma_personal_require_xades`** (`off`·`clasificados`·`todos`) es la **política XAdES de la entidad** que lee signature-service en cada firma de cadena; la migración `010` la siembra en JSON `null` (= «la entidad no lo define»: manda el respaldo del entorno `FIRMA_PERSONAL_REQUIRE_XADES`) y la `011` repara las entidades donde una `010` anterior la había sembrado en `"off"` sin que nadie la cambiara por la API; cualquier otro valor da `422 valor_invalido` (un valor desconocido se aplicaría como `todos`). **Relajar la política** (bajar la exigencia, p. ej. `todos`→`clasificados`, o fijar un valor menor cuando el vigente es `null`/desconocido, que se supone el más estricto) **exige `motivo`** de al menos 10 caracteres: sin él, `422 motivo_requerido`; el asiento `config.parametro_actualizado` lleva `motivo` y `relaja_politica`. Endurecer no lo pide. La pantalla genérica `/admin/parametros` ya lo lista: no hay pantalla propia. Los parámetros de numeración (`formato_numero_radicado`, `depe_digits`, `consec_digits`, `expediente_number_format`) son **inertes**: ningún servicio los lee todavía. **Inertes declaradas (B6)**: la 002 siembra además 10 claves heredadas de Orfeo que NINGÚN servicio lee (`unicoconsecutivo`, `formato_numero_radicado`, `depe_digits`, `consec_digits`, `dias_bloqueo`, `expediente_number_format`, `notificaciones_mail`, `archivado_requiere_exp`, `reasigna_requiere_exp`, `firma_digital_provider`); se conservan sembradas pero el `PUT` las rechaza con el mismo `422` (editarlas prometería un efecto inexistente); para volver a configurarlas, un servicio debe leerlas y pasar a la lista admitida (`PARAMS_INERTES` en `config.py`).
- **Dependencias — `PATCH` y `DELETE /api/v1/dependencias/{id}` (B5, ADR-032)**: `codigo` es **inmutable** (`PATCH` con otro valor → `422 dependencia_codigo_inmutable`; el mismo valor es un no-op; para corregirlo se crea la dependencia nueva y se desactiva la vieja). `nombre` (≤ 100 caracteres) **se puede cambiar libremente**, también con trámite vivo: el nombre anterior queda en `dependencia_nombre_historico` (solo inserción) en la misma transacción que el asiento. **Desactivar** (`activo=false`) con pasos de flujo `pending`/`in_progress` o reglas que la citan por código responde `409 dependencia_con_tramite_en_curso` con `pasos_vivos` y `reglas`. **Eliminar** responde `409 dependencia_con_historia` (con `referencias`) si cualquier tabla la cita —pasos, eventos, reglas, radicados, borradores, URD, expedientes—: una dependencia usada se desactiva, no se borra (Ley 594).
- **`POST /api/v1/dependencias/{id}/mapeos-nombre`** `{nombre, motivo}` (**`USUA_PERM_ADMIN` nivel 3**, `motivo` ≥ 10 caracteres, asiento `dependencia.mapeo_nombre`): decisión humana de que `nombre` (ambiguo o huérfano para el relleno de las migraciones de workflow/document) **es** esta dependencia. Es la salida de una migración que abortó con «nombres ambiguos o inexistentes»; `201 {codigo, nombre, origen:"mapeo_manual"}`. **Irreversible** (historial de solo inserción) y el mapeo gana a las coincidencias textuales del nombre: `409 dependencia_inactiva` si la dependencia está inactiva y `409 nombre_de_otra_dependencia` si el nombre es el vigente de otra dependencia activa (no si el destino también lo lleva: homónimas). `PATCH` acepta además `motivo` (10–1000 caracteres, opcional) cuando cambia el nombre: va al historial y al asiento. `POST /dependencias` con un código eliminado → `409 dependencia_codigo_retirado` (un código eliminado no se reutiliza); `DELETE` registra el código en `dependencia_codigo_retirado` y su `referencias` barre por catálogo todas las columnas de código (transferencias, índice electrónico, plantillas, usuarios activos y el propio historial de nombres).
- **Días no hábiles / festivos**: `GET /api/v1/config/holidays` (`?year=`, autenticado) · `POST /api/v1/config/holidays`, `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year_from, year_to}` → siembra los festivos colombianos calculados automáticamente, con asiento `config.festivos_sembrados` en la misma transacción) — las 3 escrituras exigen **`USUA_PERM_ADMIN` a nivel ≥ 3** (FIRMA-2: un administrador en lectura o edición recibe `403`; el calendario fija los plazos legales de la entidad).
- **Días hábiles**: `GET /api/v1/business-days/calculate?from=&days=` (autenticado) — calcula la fecha de vencimiento aplicando fines de semana + festivos + no laborables del tenant (base de RF-RAD-04).

### PINAR — Plan Institucional de Archivos (E14, RF-ADM-08, Acuerdo AGN 003/2015)

Instrumento de planeación archivística por tenant. Todas las escrituras (ciclo de
vida del plan, aspectos, priorización, objetivos, proyectos, seguimiento,
instrumentos) exigen **`USUA_PERM_ADMIN` a nivel CRUD 3** (octubre de 2026; antes bastaba el nivel 1, de lectura; las lecturas siguen a nivel 1) (`app/core/authz.py`, cierre del mismo
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

> **La interfaz consume ya el módulo completo (2026-08-29).** Hasta esta fecha la pantalla usaba **6 de los 31 endpoints**: se podía crear un plan y recorrer su ciclo de vida, pero **no había forma de crear un aspecto, un objetivo ni un proyecto desde el navegador** — así que el tablero salía siempre vacío y la propia pantalla lo declaraba como «fase posterior de PINAR». Un tablero que no puede llenarse nunca no es un estado vacío: es un falso verde. Ahora `/admin/pinar/[id]` tiene tres pestañas más —aspectos con su matriz, objetivos y proyectos con seguimiento, e instrumentos articulados— y **espeja los dos ciclos de vida del servicio**: la estructura solo se edita en `borrador` y el seguimiento solo con el plan `aprobado` o `en_ejecucion`, que nunca coinciden.

---

### Eliminación documental — lotes (RF-TRD-07 **completo**, 2026-08-29)

`POST /api/v1/eliminacion/lotes` · `GET /api/v1/eliminacion/lotes` · `GET /api/v1/eliminacion/lotes/{id}`

**Recurso propio y no un apéndice de `/trd`** (mismo criterio que la cola de anulaciones): un lote no es una lectura del instrumento, es un procedimiento con ciclo de vida `borrador → publicado → avalado → ejecutado`, o `anulado`. Proponer y listar exigen **`USUA_PERM_TRD`**; proponer además **`crud≥3`** y actor imputable.

**Nada de esto elimina nada.** Se elimina por lotes porque el acta y el aval del Comité son del lote; esta fase propone y **congela el inventario** —código, nombre, serie, disposición, fecha de vencimiento, fechas extremas, nivel y volumen—, que es lo que después se publica para que un tercero pueda oponerse y lo que el acta describirá. No se lee en vivo del expediente: el expediente va a dejar de existir.

**Cinco condiciones de admisión**, y lo rechazado **se informa con su motivo** en vez de descartarse en silencio (`sin_confirmacion`, `disposicion_no_destruye`, `instrumento_no_gobierna`, `worm_vigente`, `ya_en_otro_lote`, `no_encontrado`):

1. El vencimiento del archivo central está **confirmado** por un usuario autorizado (RF-TRD-06) — sin decisión no hay propuesta.
2. La disposición **termina en destrucción** (`E`, `S`, `M`). Conservación Total **jamás** entra.
3. El instrumento congelado la **gobierna** (ADR-025 D5).
4. El **candado WORM ya expiró** — y es el **máximo de los tres** que puede tener un expediente (índice firmado, acta de transferencia y AIP): mientras uno siga vigente, en modo COMPLIANCE no puede borrar nadie, ni el administrador.
5. No está ya en otro lote vivo.

Un expediente fuera del clearance del proponente se informa como `no_encontrado`, **sin distinguirlo de uno inexistente**. Al abrir un lote, el **inventario se recorta por clearance** pero `total_items` sale de la base y no de contar lo devuelto: recalcularlo dejaría que la resta delatara cuántos clasificados contiene. El listado de lotes **no** se recorta — un lote no es un expediente: su código, su estado y su acta no dicen nada del archivo.

**Fase 2 — publicidad previa (2026-08-28).** `POST /api/v1/eliminacion/lotes/{id}/publicar` (`USUA_PERM_TRD` `crud≥3`) transita `borrador → publicado` y **congela el inventario**: el trigger de la migración 047 deja de admitir que se quite una fila, porque lo publicado es sobre lo que un tercero decidió no oponerse. `publicado_hasta` es obligatorio y futuro — **no hay default**: no se inventa cuántos días da una entidad para oponerse a que destruyan un documento.

El inventario queda visible **sin autenticar** en `GET /api/v1/public/archive/eliminacion/{tenant}/lotes` y `GET /api/v1/public/archive/eliminacion/{tenant}/lotes/{lote_id}`. **El chokepoint es el ESTADO del lote**, no un interruptor de despliegue: deliberadamente **no** cuelga de `public_interop_enabled` (la cosecha OAI/CMIS), porque publicar el registro de correspondencia es una decisión de la entidad y publicar el inventario de lo que va a eliminar es una **obligación del procedimiento** — atarlas al mismo interruptor dejaría a una entidad sin poder eliminar por no activar una cosecha que no le interesa. El chokepoint está escrito por la **marca del acto** (`publicado_at IS NOT NULL`) y no por una lista de estados: es público **todo lote que llegó a publicarse**, y lo sigue siendo después — `avalado`, `ejecutado` y también `anulado`. La constancia de qué se eliminó no puede desaparecer al eliminarlo, y la de que algo **no** se eliminó le importa a quien leyó que iba a eliminarse. Un lote en `borrador` responde **404**, indistinguible de un identificador inventado.

**Un slug que no existe y un lote que no existe dan lo mismo (T6 / AREA-06-01-C).** `GET …/{tenant}/lotes/{lote_id}` comprueba primero que la entidad esté alojada (`public.tenants`) y responde el mismo `404 lote_no_encontrado` que para un UUID inventado; antes un slug inexistente fallaba con `UndefinedTable` (500) y uno real daba 404, así que cualquiera sin token averiguaba qué entidades aloja la instalación (el listado ya lo tenía cerrado).

**La descripción de un expediente clasificado no se publica, pero la fila sí.** El recorte va en el `SELECT` (`CASE WHEN nivel_seguridad > 1 THEN NULL`), no en quien pinta. Omitir la fila dejaría de cumplir la publicidad previa justo sobre los expedientes que más importa que alguien pueda objetar; lo que se retiene es la **denominación**, que es donde vive el contenido. Va marcada con `nombre_reservado` para que la pantalla lo **diga** en vez de dejar un hueco mudo.

Pantalla pública en `(public)/eliminacion`, enlazada desde el portal. **El canal de oposición no se inventa**: es la ventanilla única que la entidad ya tiene — un formulario aparte sería una segunda bandeja que nadie mira, y una oposición es exactamente lo que una ventanilla existe para recibir.

**Fase 3 — el aval del Comité y la retirada (2026-08-29).**

`POST /api/v1/eliminacion/lotes/{id}/avalar` (`USUA_PERM_TRD` **`crud≥4`**, actor imputable) transita `publicado → avalado` con las **tres constancias obligatorias** —`acta_comite`, `fecha_aval` (no futura) y `acto_administrativo`—, exigidas en el schema, en el servicio y en un `CHECK` de la migración 047: un aval sin ellas es una afirmación no verificada, el mismo criterio que la convalidación de una versión de TRD. El sistema **no decide**; registra que un Comité decidió y con qué acto (Ac. AGN 001/2024 art. 4.3.2.5).

**No se avala con el plazo de publicidad todavía abierto.** Es la regla de fondo de la fase: autorizar la destrucción antes de que venza el tiempo que la propia entidad dio para oponerse convierte la publicidad previa en un adorno. La guarda vive en el `WHERE` de la transición (`AND publicado_hasta < CURRENT_DATE`, con la fecha del **servidor**), no en una comprobación previa en Python que dejaría una ventana entre leer y escribir. El `409` **distingue los tres motivos**: `publicidad_en_curso` dice hasta cuándo, `lote_no_avalable` dice en qué estado está, y un identificador desconocido da **404**.

**Separación de funciones (2026-10, T6 / AREA-06-01).** `avalar` y `ejecutar` piden `crud≥4` —el nivel que habilita lo irreversible— y, sobre todo, **quien propuso el lote no lo avala** (`409 separacion_de_funciones`): subir el nivel solo acota quién puede, y una sola persona con nivel 4 lo haría todo. La guarda vive en el `WHERE` de la transición (`AND creado_por <> $5`), no en una comprobación previa. El intento deja asiento `archive.eliminacion_separacion_denegada`. `LoteEliminacionResponse` añade `avalado_por` para que la interfaz sepa a quién no ofrecerle `ejecutar`. **Atomicidad**: publicar, avalar, anular y marcar ejecutado son un solo acto con su asiento (`conn.transaction()`); antes el `UPDATE` se confirmaba solo y un fallo del asiento dejaba el lote en su estado nuevo sin traza.

`POST /api/v1/eliminacion/lotes/{id}/anular` retira el lote con un `motivo` de al menos 10 caracteres. Se puede **hasta el último momento, incluso ya avalado** —la condición se escribe por admisión de los tres estados vivos, no negando `ejecutado`—, pero nunca después de ejecutarlo: lo destruido no se des-destruye. Si el lote llegó a publicarse **sigue siendo público tras anularse**, con su motivo a la vista.

Pantalla en `/admin/eliminacion` (listado, detalle con el inventario recortado por clearance, y los tres actos). Vive fuera de `/admin/trd` por el mismo criterio que el router: un lote no es una lectura del instrumento. Lo único que se queda en TRD es **proponer**, porque se propone desde la cola de vencimientos.

**Fase 4 — el acta de eliminación (2026-08-29).**

`POST /api/v1/eliminacion/lotes/{id}/acta` (`USUA_PERM_TRD` `crud≥3`) · `GET /api/v1/eliminacion/lotes/{id}/acta` (`USUA_PERM_TRD`) · `POST /api/v1/eliminacion/lotes/{id}/acta/firmar` (`crud≥3`)

**El acta es lo que sobrevive a lo eliminado.** Cuando la fase 5 borre los bytes, será —junto con la lápida y el `audit_log`— la única constancia de qué se destruyó, bajo qué acto y quién lo autorizó. Es de **conservación permanente**: sin calendario de retención y sin disposición final. Se genera **antes** del borrado y no después, porque el inventario que describe sale de expedientes que van a dejar de existir.

Son **dos tablas** (migración `048`), por el mismo criterio que el acta de transferencia: `eliminacion_acta` guarda el **contenido congelado** y es *append-only por trigger* —ni se edita ni se borra—, y `eliminacion_acta_firma` guarda el **estado mutable** del sellado. Mezclarlas obligaría a que el contenido fuera mutable para que la firma pudiera avanzar. Consecuencia deliberada y verificada: con el acta escrita, **la fila del lote ya no se puede borrar** (el `ON DELETE CASCADE` choca con el trigger).

El XML (`urn:orpycamcp:acta-eliminacion:v1`) se serializa **canónicamente y con el inventario ordenado por código**: dos construcciones con los mismos datos dan la misma cadena byte a byte, o el `acta_sha256` no significaría nada. Ese SHA se ancla además en `audit_log`, que es indeleble.

**El inventario del acta va completo, sin recortar por clearance** — un acta a la que le faltan los clasificados no da fe de lo que se destruyó. Lo que se acota es **quién la lee**: el umbral se **deriva del contenido** (el nivel de seguridad más alto del inventario, nunca un campo declarado) y quien no lo despeja recibe **403** con el motivo, no un acta recortada: romper la firma sería peor que negarla.

`metodo_borrado` es obligatorio, de al menos 10 caracteres y **sin valor por defecto** en ninguna de las tres capas: el acta debe decir cómo se destruye (Ac. AGN 001/2024 art. 4.3.2.5), y una frase inventada por el sistema sería una afirmación que nadie hizo sobre un acto irreversible.

El sellado **XAdES** corre post-commit, best-effort y acotado, con los tres desenlaces del acta de transferencia (firmado / `sello_ausente` sin reintentar / `transient` agotado), el motivo del último fallo visible y reintento manual. `acta_eliminacion` se añadió al allow-set `XADES_ELIGIBLE_OBJETO_TIPOS` de signature-service: **sin estar en él, el acta habría caído al HMAC `nativa` sin error y sin diferencia visible**, quedando marcada «firmada» siendo estructuralmente más débil. `acreditado` viaja explícito y es siempre `false` (art. 7 de la Ley 527/1999, nunca art. 28).

> **WORM del acta: cerrado el 2026-08-29.** Era la deuda declarada al cerrar esta fase, y la razón era de **modelo**: el almacén de Object-Lock asumía que todo artefacto protegido pertenece a un expediente, y un acta de eliminación cubre **N** expedientes que además van a dejar de existir — clavarla bajo el id de uno de ellos no era incómodo, era incorrecto.
>
> La migración `storage tenant/014` añade `objeto_tipo` (`expediente` | `lote`) con un `CHECK` que **impone la pareja**: un `acta_eliminacion` pertenece siempre a un lote y todo lo demás siempre a un expediente, así que no puede existir la fila que diga que un índice electrónico pertenece a un lote. `expediente_id` **no se renombra** —era ya un id de propietario sin clave foránea, y renombrarlo tocaría 142 referencias de las que la mayoría hablan de expedientes de verdad—, pero el gate de purga y el listado por expediente ahora **exigen `objeto_tipo = 'expediente'` explícitamente**: sin eso, una fila cuyo id es de un lote podría decidir sobre la purga de un expediente. Que los dos espacios de identificadores no coincidan «en la práctica» no es un control.
>
> El acta entra además en `CONTROL_INSTRUMENT_TIPOS`: como el acta de transferencia, **sobrevive al contenido y no debe postergar su disposición** — y con más razón, porque su retención es permanente y sería el instrumento impidiendo el acto que él mismo documenta. La **conservación permanente se materializa como 100 años**, el máximo del contrato: Object-Lock exige una fecha, no un infinito; es renovable por la misma vía que las series CT.
>
> El cableado corre tras el sellado, es **best-effort y nunca revierte la firma** (que ya es un hecho consumado), anota el motivo si falla, y `/admin/eliminacion` **muestra si el acta está protegida o solo firmada** — durante una fase entera esa diferencia estuvo declarada como pendiente y no se veía.

**Fase 5 — el borrado seguro (2026-08-29). Esto destruye.**

`POST /api/v1/eliminacion/lotes/{id}/ejecutar` (`USUA_PERM_TRD` **`crud≥4`**) · `GET /api/v1/eliminacion/lotes/{id}/lapidas` (`USUA_PERM_TRD`)

Es el único camino del sistema que borra documentos de archivo, y **no acepta ningún cuerpo**: en una destrucción no hay nada que el cliente pueda parametrizar. Qué se destruye lo dice el inventario congelado al proponer; bajo qué acto, el aval. Un `dry_run` o un `forzar` serían justo los parámetros que un día alguien pasa mal.

**Seis precondiciones**, todas comprobadas **antes** de destruir el primer expediente: lote `avalado`, **ejecutor distinto del avalador** (`409 separacion_de_funciones`), acta existente, acta **firmada**, acta **protegida bajo WORM** (`409 acta_sin_worm`, T6 / AREA-06-02) e inventario congelado como única fuente. Exigir la firma y el candado puede bloquear la ejecución si signature-service o el almacén están caídos, y es deliberado — el último acto irreversible es el momento de no seguir con una constancia a medias, y sin candado el objeto firmado podría borrarse del almacén justo después de destruir lo que describe. El cableado WORM del acta es best-effort pero **ya no de un solo intento**: un fallo programa su reintento con backoff exponencial (`worm_reconcile_proximo_at`), la barrida `run_once_actas_eliminacion_worm` (mismo ciclo que el WORM del acta de transferencia) lo repite, agotado el umbral queda `worm_reconcile_agotado` con asiento de sistema, y el reintento manual `POST …/acta/firmar` también completa el candado de un acta ya firmada.

**La cadena, y su orden**: `archive-service` → `document-service` (`POST /internal/documentos/purgar`) → `storage-service` (`POST /storage/internal/purgar-disposicion`, que por fin da puerta a `purge_for_disposition`, escrito hacía tiempo y **sin ningún llamante**) → evento `document.radicado.purged` → `knowledge-service` purga el vector. Dentro de cada tramo, **los bytes van antes que las filas**: al revés quedarían objetos huérfanos en el almacén de objetos que nadie sabe ya a qué radicado pertenecían — imposibles de encontrar y por tanto de eliminar. Y **los documentos van antes que el expediente**: al revés, los vínculos a radicados caerían en cascada y ya no habría forma de saber qué había que destruir.

**Todo-o-nada por expediente.** El que falle queda **intacto y sin lápida**, y viaja en `fallidos` con su código; el lote **no** pasa a `ejecutado` mientras quede uno pendiente. Una lápida sobre bytes vivos afirmaría para siempre una destrucción que no ocurrió. El candado WORM aborta la purga **entera** del expediente, no la del archivo protegido.

**La lápida** (`expediente_lapida`, migración `049`) se escribe **antes** de borrar el expediente y en la misma transacción: al revés, un fallo entre ambos dejaría el expediente destruido y sin lápida, y el silencio es indistinguible de «nunca existió». Es **content-free**: código, serie, fechas extremas, volumen y el acto que lo autorizó — **la denominación no**, que es contenido. Append-only por trigger, y sin FK a `expedientes` a propósito.

> **La migración 049 soltó tres claves foráneas, y una de ellas evitó una pérdida silenciosa.** `eliminacion_lote_items` y `disposicion_decisiones` **bloqueaban** el borrado; `transferencias` iba en `ON DELETE CASCADE` y se habría llevado por delante las transferencias y, en cascada, `transferencia_acta` — un instrumento de control que el propio código de storage-service ya declara que debe sobrevivir al contenido (Ley 594/2000 art. 26). Criterio único: **lo que debe sobrevivir conserva el `expediente_id` sin clave foránea**, como `expediente_eventos` desde siempre. Y una al revés: `expediente_aip` pasó a `CASCADE` porque empaqueta el contenido y no describe nada sin él.

> **Por qué la purga del vector usa el centinela y no un `DELETE`**, aunque el documento ya no exista: bajo entrega *at-least-once*, un `created` reentregado después del `DELETE` reinsertaría el fragmento desde cero, sin marcador, y el texto del documento eliminado volvería a ser consultable. El `DELETE` borraría el propio centinela que hace la purga irreversible. El centinela no conserva texto, ni embedding, ni metadatos.

**Lo que sobrevive**: la lápida, el acta firmada, el inventario congelado, las decisiones de disposición, la hoja de ruta del expediente (`expediente_eventos`), las transferencias con su acta, y el `audit_log` con un asiento por radicado destruido —con su número de radicado, que es lo único que queda de cada documento—.

---

## document-service — puerto 8003

> Requiere headers `X-Tenant-Slug` y `X-User-Id` (inyectados por el gateway).

### `POST /api/v1/documents`

Registra un nuevo radicado. Asigna número de radicado atómico.

**Datos mínimos (T13 / AREA-09-02, Acuerdo AGN 060/2001 §5.2).** `doc_class` es obligatorio en los tres tipos; **Entrada e Interno** exigen además `dest_dept_code` y **Salida** exige `origin_dept_code`. Si falta alguno: `422` (`DocumentCreate` y `BatchDocumentCreate` comparten la misma regla, `app/core/datos_minimos.py`; sin consumir consecutivo). Los productores internos la cumplen así: la respuesta rápida hereda origen (el destino de la Entrada) y tipo del antecedente; el radicado desde borrador deriva el tipo de la plantilla, el destino por el nombre de `dest_dept` y el origen de la dependencia de quien radica (ver `POST /borradores/{id}/radicar`); la bandeja de correo exige `doc_class` y `dest_dept_code` en el cuerpo (`422 datos_minimos_incompletos`).

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
- `origin_dept_code` / `dest_dept_code` (opcionales; **canónicos**, ADR-032): código (`depe_codi`) de la dependencia en el organigrama. Si además llega `origin_dept`/`dest_dept` y no es el nombre de ese código: `422 dependencia_inconsistente`. Solo con el nombre (alias deprecado) y sin código, un nombre que corresponde a dos dependencias es `422 dependencia_ambigua`; uno que no es dependencia alguna se conserva como texto (la Salida puede ir a un tercero). Si se indican, document-service los valida contra `tenant-service` (`GET /api/v1/dependencias/by-codigo/{codigo}`) y **denormaliza el nombre** de la dependencia en `origin_dept`/`dest_dept` (snapshot legal). Código inexistente o dependencia inactiva → `400 invalid_dependencia`.
- Si `tenant-service` no está disponible al resolver plazo o dependencia, la creación responde `502 tenant_service_unavailable` y **no** se radica el documento.
- `metadata` (opcional): metadatos variables del tipo documental (ADR-007). Si el `doc_class` tiene una **plantilla activa**, los valores se validan contra su JSON Schema antes de radicar; si no cumplen → `422 metadata_validation_failed` y no se radica; el sobre del 422 lleva `field` (ruta con puntos de la propiedad que incumple, p. ej. `ubicacion.ciudad` o `etiquetas.1`; ausente si el fallo es de la raíz) para que la pantalla marque el control (`POST /expedientes` lo lleva igual). Sin plantilla, se almacenan tal cual. La columna está indexada con GIN. **Claves reservadas (B6)**: `from_borrador`, `canal` y `consentimiento` son procedencia del sistema (la última es la constancia de la Ley 1581): el cliente no puede aportarlas ni por `POST /documents`, ni por `PATCH` de `metadata`, ni por `respuesta`, ni por borrador (`422`); el sistema las pone por `procedencia` (ventanilla, borrador) y el `PATCH` de `metadata` **las preserva** tal cual aunque reemplace el resto. `doc_class` debe ser un código **activo** del catálogo `tipos_documentales` (`422 tipo_documental_desconocido`, antes de consumir el consecutivo; lo mismo al retipificar por `PATCH` y en cada ítem del lote). El asiento `document.radicado_created` lleva `from_borrador` cuando el radicado nace de un borrador. **Origen de la Salida (B6)**: en `doc_type=S` con actor humano, `origin_dept_code` debe ser una dependencia donde el actor tiene URD activa (`422 origen_fuera_de_sus_dependencias`); el límite vive en `DocumentService.create` y cubre `POST /documents`, respuesta rápida, borrador y cada ítem del lote (el ítem falla antes de gastar consecutivo). El `PATCH` solo exige tipo activo si el `doc_class` **cambia** (reenviar el mismo tipo, aunque esté inactivo, no da 422). La importación INT-05 no guarda `from_borrador`/`canal`/`consentimiento` del ZIP como claves de primer nivel: pasan a `metadata.procedencia_origen`.
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

### `GET /api/v1/documents/{id}/historial-acceso`

**Quién leyó y quién descargó** este radicado (RF-BUS-10; Ley 594/2000 art. 22). Paginado (`page`, `size`) con `X-Total-Count`.

Devuelve solo `ts, service, actor, actor_nombre, canal, action, object_ref`. **No** `payload` —lleva `nivel_seguridad` y el `file_id` del anexo, y no se filtra al serializar: **no se lee de la base**, que es la única forma de que no se escape el día que alguien le añada un campo— ni `id`/`prev_hash`/`hash`, que son la mecánica de la cadena y viven en `/admin/auditoria`.

- **Solo ACCESOS**, por lista de admisión: `document.radicado_consultado` (abrió la ficha) y `document.anexo_descargado` (bajó un fichero). Crear, anular o reclasificar son actos **sobre** el documento, no lecturas **de** él; mezclarlos convertiría esto en un historial de todo y enterraría lo que se viene a buscar. Es la misma lista que usa archive-service para el historial del expediente — dos listas que respondieran distinto a «¿esto es un acceso?» darían dos historiales del mismo hecho.
- **No-read-up ANTES de tocar `audit_log`**: un radicado por encima del clearance responde `404 document_not_found`, el mismo cuerpo que un id inexistente. Un 403 sería una confirmación: quien no puede ver el radicado sabría que existe y cuánta gente lo ha mirado.
- Solo se registran los canales de persona (`api`, `ui`, `mcp`); las lecturas internas del sistema no ensucian la pista.
- **`actor_nombre` es el `username`, resuelto aquí**. El asiento guarda el identificador interno y así debe seguir —un `username` cambia y un asiento no se toca nunca—, pero una respuesta a «quién ha visto esto» hecha de UUID no responde. El JOIN es `LEFT` y por texto: `actor` no siempre es un usuario del catálogo, y castearlo a `uuid` haría fallar la consulta entera por una fila que ni se estaba preguntando. Sin nombre resoluble viaja `null` y la pantalla cae al identificador — decir menos es aceptable, inventar un nombre no. `actor` sigue viajando: es lo que permite cruzar este historial con `/admin/auditoria`.

La traza existía desde siempre. Hasta 2026-09 solo se podía consultar desde `/admin/auditoria` —que exige `USUA_PERM_ADMIN` y saber por qué acción filtrar— o, si el radicado estaba en un expediente, desde el historial de **ese** expediente; un radicado suelto, que son la mayoría, no tenía respuesta.

### `GET /api/v1/documents`

Listar radicados. Filtros: `?doc_type=E&status=registered&page=1&size=20`. **Filtro por metadato** (RF-MET-02): `?meta.<campo>=valor` (uno o varios) usa contención JSONB sobre el índice GIN, p. ej. `?meta.area=juridica`. **Control de acceso (RF-SEG-08):** el listado se acota por la clasificación del radicado igual que la búsqueda — cada usuario solo ve los de `nivel_seguridad ≤` su _clearance_ (_fail-closed_ a PUBLICA).

**Ubicación actual (ADR-029).** `?ubicacion_dept_code=` (1-999999) filtra por **dónde está ahora el radicado**: la dependencia donde lo dejó el flujo (repartir, devolver, revertir, reasignar) o, si el flujo nunca lo movió, la de radicación. **No es `dest_dept_code`**, que es a quién iba dirigido el documento y no cambia al repartir. El filtro vive en la misma condición que las filas y el `COUNT` (junto al _clearance_): `X-Total-Count` nunca cuenta lo que la página no puede mostrar. Cada radicado trae `flujo_dept_code`/`flujo_dept` (lo que escribió el flujo; `null` si nunca lo movió), `ubicacion_dept_code`/`ubicacion_dept` (la que hay que leer: `flujo_*` si existe, si no la de radicación) y `dest_dept`/`dest_dept_code` (la de **radicación**, instantánea legal que no cambia). `ubicacion_dept_code` es `null` cuando el nombre del paso no casa con **una sola** dependencia activa (el nombre queda en `ubicacion_dept`). El poseedor (`flujo_usuario`) **no** se devuelve en los listados.

**`status` es repetible**: `?status=distributed&status=in_progress` trae los de cualquiera de los dos (`status = ANY(...)`). Una bandeja de trabajo pendiente no es un estado, son varios; pedirlos por separado da dos ventanas de `size` que no se pueden ordenar entre sí, y obliga a sumar dos `X-Total-Count` — un total correcto solo mientras los estados sean disjuntos.

**Plazo de respuesta** (RF-RAD-04): `?due_from=2026-01-01&due_to=2026-03-31` acota por `due_date`, con **ambos extremos inclusivos**. El endpoint **no decide qué significa «vencido»** y lo declara quien pregunta: `due_to=<ayer>` deja fuera el que vence hoy, `due_to=<hoy>` lo incluye. Es deliberado — el sistema usa hoy las dos convenciones opuestas (la TRD cuenta vencido el día en que se cumple el plazo, los préstamos no), y hornear una aquí obligaría a la otra mitad a pedir lo contrario de lo que quiere decir. Un radicado **sin** `due_date` no entra en ningún rango: la pregunta no le aplica.

**Orden**: `?order_by=registered_at|due_date&order_dir=asc|desc` (por defecto `registered_at desc`). Cualquier otro valor es `422` — el catálogo es cerrado porque un `ORDER BY` no admite parámetro enlazado. `due_date` lleva `NULLS LAST` en los dos sentidos, y todos los órdenes desempatan por `id`: sin una clave única final, dos filas con el mismo plazo pueden salir en distinto orden en dos consultas y la paginación repetiría una y perdería otra.

> Sin orden por plazo, un informe de vencimientos solo alcanzaba los radicados más recientes: el que llevaba más tiempo vencido se radicó antes, así que salía después. Era invisible por construcción.

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

**Control por anexo y exclusiones (T13 / AREA-07-12, migración `040`).** Publicar el radicado **no** publica sus soportes: el cuerpo admite `anexos_publicables: [anexos.id]` (por omisión, ninguno) y CMIS (`getContentStream`) solo sirve un anexo con `anexos.publicable = TRUE` de un radicado publicado (`oai_repository.get_public_anexo`). El acto deja el conjunto en un estado conocido (marca los indicados y desmarca el resto; un anexo ajeno al radicado → `422 anexo_no_pertenece` y revierte todo), la respuesta añade `anexos_publicados`, el asiento lleva `anexos_publicables`, retirar los desmarca todos y clasificar (nivel ≥ 2) también. **No se publica de oficio** (`404` neutro, sin asiento) lo que entró por la ventanilla pública (`metadata.canal = 'ventanilla_publica'`) ni lo tipificado con un tipo documental `admite_ventanilla_publica` (información de terceros, Ley 1581): se lee en vivo, así que sigue la configuración de la entidad. La migración conserva la difusión de los anexos de radicados que **ya** estaban publicados.

### `GET /api/v1/assistant/alcance`

Marco de alcance del llamante: **qué puede hacer, qué puede consultar y qué no puede — con su vía alternativa**. Sin permiso propio: describe las capacidades de quien pregunta, resueltas de su token.

Existe porque los permisos venían siendo un filtro silencioso: el catálogo escondía lo que el usuario no podía usar, y el límite se descubría **fallando** —o esperando una acción que nunca iba a ocurrir—. El marco lo declara antes de empezar y ofrece la salida cuando la hay («no puede enviar, pero puede trasladar el radicado a correspondencia»).

Se **deriva**, no se mantiene: sale del catálogo de tools —que declara permiso, nivel, operación de dominio y vía alternativa— cruzado con `/auth/me`, `/auth/clearance` y `/auth/context`. Añadir una tool actualiza el marco sola.

Incluye `limites_declarados`: lo que el sistema **no** distingue, para no prometer precisión inexistente — el permiso de radicación no separa por tipo de documento (`PERM_RADI_ENTRADA`/`PERM_RADI_INTERNO` existen y **ningún gate los usa**) y las lecturas se acotan por nivel de seguridad, no por dependencia.

Vive bajo `/api/v1/assistant/` porque es el único prefijo del mcp-server que el gateway enruta. No exige que el asistente esté disponible: saber qué se puede hacer no depende de que haya un modelo detrás.

### `POST /api/v1/expedientes/{id}/legal-hold`

Declara o levanta el **legal hold** (freeze de litigio) de los objetos WORM del expediente (AREA-08-07; Ley 594/2000 art. 4, Ac. AGN 001/2024 4.3.2.6). Cuerpo `{activo: bool, motivo (≥10), acta_ref}` (campos extra → `422`) → `{expediente_id, code, activo, objetos_afectados, objetos_sin_cambio}`. `USUA_PERM_ADMIN` con `crud≥3`. Motivo y acta son obligatorios **en ambos sentidos**. Expediente inexistente o por encima del clearance → `404` neutro; sin objetos WORM protegidos → `404 sin_objetos_protegidos`; storage caído o preservación apagada → `503 storage_unavailable` (no se asienta nada en archive). Asiento propio `archive.expediente_legal_hold_fijado|levantado` (con motivo, acta y recuentos) además de los de storage (ver `legal-hold-internal`).

### `POST /api/v1/expedientes/{id}/publicacion`

Acto de **publicación de oficio del expediente** (Ley 1712/2014 art. 12, migración archive `042`). Mismo contrato que el gemelo de radicados: `{publicar: bool, fundamento: str}` → `{code, publicable, publicado_at}`, `PERM_RECLASIFICAR` con `crud≥3`, fundamento obligatorio en ambos sentidos, acto y asiento en una sola transacción.

Cierra la asimetría que destapó la auditoría cruzada: la corrección del art. 12 se había aplicado a los radicados y **no aquí**, así que `oai_repository._PUBLIC` de archive-service seguía siendo `nivel_seguridad = 1` y el OAI EAD multinivel difundía de oficio todo expediente no clasificado — el valor por defecto. La asimetría era además implícita: quien leyera el recorte de document-service supondría que este servicio hace lo mismo.

Dos diferencias con radicados, **deliberadas**:

- El recorte **no lleva condición de estado**. Un radicado anulado está retirado de la circulación; un expediente `closed`/`transferred` no lo está — son estados normales de su ciclo de vida. Copiar `status <> …` por simetría habría despublicado en silencio todo el archivo cerrado, que es la mayor parte de un fondo.
- Lo que se difunde es de nivel agregado: código, nombre, descripción, fechas extremas y el **número** de radicados. Los `tracking_number` hijos no se enumeran nunca (un expediente público puede contener radicados reservados).

`publicable` y `publicado_at` viajan en `ExpedienteResponse`. **Compatibilidad**: los expedientes existentes quedan **no publicables** tras la migración `042`.

**Pantalla**: drawer «Publicación» en `/expedientes/{id}`. Sobre un expediente reservado no ofrece publicar —y lo explica— pero **sí ofrece retirar**, que es la asimetría real del backend.

### `GET /api/v1/documents/{id}`

Obtener por UUID. Incluye lista de anexos. **Control de acceso (RF-SEG-08):** si el radicado supera el _clearance_ del llamante, responde `404` (no revela su existencia). **ADR-029:** incluye la ubicación actual (`flujo_dept_code`, `flujo_dept`, `ubicacion_dept_code`, `ubicacion_dept`) y, solo aquí y en `by-tracking`, `flujo_usuario` (`auth_users.id` de quien tiene el paso; `null` si el paso es de una dependencia sin dueño). Nada de esto aparece en las superficies públicas (verificación por código, OAI-PMH, CMIS).

### `GET /api/v1/documents/by-tracking/{tracking_number}`

Obtener por número de radicado (ej: `2024-ICETEX-E-000001`). Misma acotación por clearance que el detalle por UUID (`404` si excede el nivel del llamante).

### `PATCH /api/v1/documents/{id}`

Actualizar `subject`, `dest_dept`, `observations`, `pages`, `doc_class` y `metadata`.
`tracking_number`, `doc_type`, `year`, `sequence` son inmutables.
**Corregir el destino (ADR-029, ADR-032)** se envía como `dest_dept_code` (canónico; `dest_dept` es el alias deprecado) y el par `(dest_dept, dest_dept_code)` se reescribe **entero y desde el catálogo** en el mismo `UPDATE` (cambiar solo uno dejaba el código de otra dependencia): código inexistente o inactivo y nombre desconocido → `422 dependencia_desconocida`, nombre con dos candidatas → `422 dependencia_ambigua`, código y nombre discordantes → `422 dependencia_inconsistente` (antes un nombre sin candidato único dejaba el texto con código `null` en silencio). Sobre un radicado `closed`, el destino es dato histórico: `409 dest_dept_immutable`.
**`status` ya no se acepta (ADR-028): `422 status_managed_by_workflow`.** El estado del radicado (`registered → distributed → in_progress → closed`) lo mueve el flujo de trámite en la misma transacción que el paso —asignar, trasladar, devolver, revertir, reasignar, marcar leído, finalizar—, no se edita a mano. El campo no se ignora en silencio: un cliente que lo envía recibe el error y no toca la base. El estado es de **solo lectura** en `GET /documents` y se sigue pudiendo filtrar por él (`?status=`).
**Tipificar (`doc_class`) exige `PERM_RADI` con `crud >= 3`; el resto del PATCH, `crud >= 1`.** El tipo documental fija la serie TRD, y con ella la retención y la disposición final del radicado (Ley 594/2000 art. 24): un acto con esa consecuencia no puede ejercerse con permiso de lectura. El umbral se comprueba solo cuando `doc_class` viaja en el cuerpo, así que quien corrige un asunto o un número de folios conserva el nivel que siempre le bastó.
**Retipificar revalida los metadatos.** Los metadatos efectivos —los que vengan en el cuerpo, o los que el radicado ya tuviera— se validan contra la plantilla activa del tipo **nuevo** antes de escribir nada; si no encajan, `422 metadata_validation_failed` y no se toca el radicado. Un documento no puede quedar tipificado como X arrastrando los campos variables de Y. `metadata` se **reemplaza entero**, no se fusiona, y la versión de plantilla usada se reescribe siempre que haya revalidación —incluido el `NULL` cuando el tipo nuevo no tiene plantilla activa—, para que el radicado no siga apuntando al esquema del tipo anterior. Enviar solo `metadata` también revalida: si no, la validación de la radicación sería puenteable con un PATCH posterior.
**Control de acceso (RF-SEG-08):** `404` si el radicado no existe **o** supera el _clearance_ del llamante. El gate es imprescindible porque la respuesta devuelve el radicado completo (asunto, remitente, anexos): sin él, la escritura sería también una lectura.
**`409 radicado_anulado`:** un radicado anulado no admite modificaciones. Hasta ADR-028 `status` aceptaba `registered`, así que sin este guard un PATCH deshacía una anulación aprobada por el circuito de dos pasos, sin causal ni aprobador. El desanulado, si procediera, es un acto propio — no el efecto lateral de editar un campo. Es un `409` y no un `404` porque en ese punto el radicado ya pasó el gate de _clearance_: decir que está anulado no revela nada que el llamante no pudiera leer.
**Auditoría:** deja asiento `document.radicado_actualizado` con el **antes y el después** de cada campo tocado, en la MISMA transacción que la mutación — si el asiento falla, la modificación revierte.

### `PATCH /api/v1/documents/{id}/security-level`

**Reclasificación del nivel de seguridad** de un radicado ya radicado (E08, paridad con el _Cambio Nivel de Seguridad_ de Orfeo). Cambia `nivel_seguridad` (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) sin alterar contenido ni `tracking_number` (inmutabilidad intacta). Requiere permiso **`PERM_RECLASIFICAR`**.

- **Body**: `nivel_seguridad` (1..3, obligatorio), `motivo` (string no vacío, **obligatorio** — el acto de reserva debe motivarse, Ley 1712 art.19/28), y opcionales `causal_reserva`, `fundamento_juridico`, `plazo_reserva_meses`.
- **Control de acceso (RF-SEG-08)**: _no-read-up_ — si el _clearance_ del llamante es menor que el nivel actual del radicado responde `404` neutro (no revela existencia ni nivel); _no-write-up_ — no puede fijar un nivel mayor que su propio _clearance_ (`403`). Si el nivel solicitado coincide con el actual, es _no-op_ idempotente (sin auditoría ni evento).
- **Traza**: registra en el `audit_log` inmutable (`document.radicado_reclassified`) el `nivel_anterior`, `nivel_nuevo`, `motivo` y el fundamento jurídico, de forma atómica con el cambio.
- **Outbox transaccional (T13 / AREA-09-05, migración `042`)**: el evento ya NO es best-effort. Se inserta en `eventos_salientes` (por tenant) en la **misma transacción** que el `UPDATE` del nivel y el asiento: si alguna falla, no existe ninguno de los tres. Tras el commit se publica por el camino rápido y se marca `enviado_at`; si Redis cae justo ahí, un relé del **barrido** (`barrido.py`, fase 4, `SKIP LOCKED`, pasada la gracia de 60 s) lo reenvía con el **mismo** `event_id`/`dedup_key` (entrega at-least-once; los consumidores ya deduplican). Una `reclassified` reenviada lleva el `nivel_nuevo` **vigente** de `radicados.nivel_seguridad` y no el de cuando se encoló, para que un reenvío tardío no baje el nivel que otra reclasificación ya subió. El mismo mecanismo cubre `document.radicado.created` y `document.radicado.annulled`, y emite `documento.contenido_cambiado` (E23, AREA-09-16) cuando `PATCH /borradores/{id}` cambia la **huella del texto** (no por cambiar solo el asunto): payload `{borrador_id, radicado_padre_id, cuerpo_sha256, cuerpo_sha256_anterior, version}`; es limpieza de bandejas —la garantía síncrona sigue siendo el `409 documento_modificado` de signature-service—. El doble control para desclasificar y un barrido de reconciliación en knowledge siguen pendientes.
- **Propagación**: emite `document.radicado.reclassified`; el signature-service re-sincroniza el _snapshot_ `nivel_seguridad` de las cadenas de firma del objeto (cierra el residual de _staleness_ de RF-SEG-08).

---

### `GET /api/v1/documents/{id}/anexos/{anexo_id}/download`

Descarga de un anexo **mediada por document-service**, que es la decisión de fondo: storage-service queda **sellado** (su contenido solo se alcanza con `X-Internal-Token`) y el no-read-up lo decide quien conoce el radicado y su nivel de clasificación. La alternativa —denormalizar el nivel de seguridad hacia storage— habría creado una segunda fuente de verdad que se desincroniza en silencio con cada reclasificación.

---

### Terceros y actores de la comunicación (E01, RF-RAD-05) — document-service

`GET /api/v1/terceros?q=&tipo=&limit=` · `POST /api/v1/terceros` · `GET /api/v1/terceros/radicado/{id}/actores` · `POST /api/v1/terceros/radicado/{id}/actores` · `GET /api/v1/terceros/divipola/departamentos` · `GET /api/v1/terceros/divipola/municipios?departamento=&q=` · `GET /api/v1/terceros/duplicados?limit=` · `GET /api/v1/terceros/{id}/radicados?limit=` · `POST /api/v1/terceros/{id}/fusionar`

**Los actores viajan en la radicación.** `POST /api/v1/documents` acepta `actores: [{tercero_id, rol, principal?, canal?, …contacto}]` y los congela **dentro de la misma transacción** que crea el radicado y su asiento: un `tercero_id` inexistente revierte la radicación entera (404), en vez de dejar un radicado sin remitente con el número ya consumido. `POST /terceros/radicado/{id}/actores` se conserva para añadirlos después. Hasta 2026-09 ninguna pantalla llamaba a ninguna de las dos y `radicado_actor` estaba **vacía** sobre 182 radicados — el directorio no aprendía de nadie.

**Administración del directorio** (`/admin/terceros`):

- `GET /terceros/duplicados` — fichas vivas que **probablemente** son la misma persona, por **dos ejes que no valen lo mismo**: `documento` es certeza (misma cédula = misma persona) y `nombre` —normalizado sin tildes ni mayúsculas— es **sospecha**, porque puede haber homónimos. Solo informa; el sistema nunca fusiona solo. Excluye las ya fusionadas, o la lista de pendientes no bajaría nunca.
- `GET /terceros/{id}/radicados` — dónde aparece la ficha y con qué rol. **Incluye los de las fichas fusionadas en ella**: sin ese arrastre, consolidar dos fichas haría desaparecer de la vista los radicados de la absorbida. **No-read-up (RF-SEG-08)**: solo se devuelven los radicados cuyo `nivel_seguridad` alcanza el _clearance_ del llamante (cada ítem trae `nivel_seguridad`; la barrera de residencia del asistente recorta el asunto en 2-3); la lectura deja el asiento `document.tercero_radicados_consultado`.
- `POST /terceros/{id}/fusionar` `{absorbe_id}` — consolida un duplicado; el de la ruta **sobrevive**. Exige `PERM_RADI` con **`min_crud=3`**. **No reescribe nada**: marca al absorbido con `fusionado_en` y nada más, porque `radicado_actor` es inmutable por trigger (migración 026) **incluido su `tercero_id`** — a qué ficha del catálogo se atendió a alguien también es parte de lo que ocurrió, ya que ese día existían dos. La fusión añade lo que se supo después y la resolución ocurre al leer. Devuelve `vinculos_heredados`. Errores: `422 fusion_consigo_mismo`, `404 tercero_no_encontrado`, `409 tercero_ya_fusionado` (no se encadenan: una cadena con un ciclo colgaría al lector) y **`409 documentos_distintos`** cuando las dos fichas traen número de documento — `ux_tercero_documento` es UNIQUE entre las vivas, así que dos números distintos son dos personas y consolidarlas destruiría una identidad legítima; si uno está mal capturado se corrige el documento y después se fusiona. El mensaje nombra los dos números. Asiento `document.tercero_fusionado` atómico y con los **nombres** de ambas — la absorbida deja de aparecer en los listados, y un id suelto no se podría resolver.

Gate `PERM_RADI` en el propio `APIRouter`, **por admisión**: quien radica es quien registra remitentes y destinatarios, y colgarlo del router cubre también las lecturas — un catálogo de ciudadanos con nombre, documento, dirección y teléfono no es un dato público de la entidad. Vincular actores exige además `crud≥3`.

**La regla que gobierna el modelo: *identidad por referencia, datos de contacto por copia congelada*.** Sale de dos problemas reales del Orfeo del que deriva este sistema:

1. **El mismo sujeto llegaba a aparecer treinta veces** en el catálogo, con direcciones distintas y variaciones de tildes o de escritura. La causa no era que faltara una restricción de unicidad: era **atar la identidad a los datos de contacto**, de modo que cada dirección nueva obligaba a crear un tercero nuevo.
2. **La dirección con la que se envió una correspondencia no se puede cambiar después.** Si se actualizara la ficha, todos los envíos anteriores pasarían a decir que se enviaron a otra parte: información errónea sobre un acto hecho con un dato que **era verdad entonces**, sin importar que hoy sea otro.

De ahí el modelo (migración `026`): `tercero` guarda **quién es** —con las cinco tipologías del requisito y su documento de identidad— y `radicado_actor` guarda **a dónde se envió**, con nombre, entidad, correo, teléfono y dirección **congelados** e **inmutables por trigger**. `tercero_id` viaja en el vínculo para poder responder «¿qué le hemos enviado a esta persona?» aunque su ficha cambie con los años.

**Tres defensas contra el duplicado, y las tres hacen falta:**

| Defensa | Dónde | Qué ataja |
|---|---|---|
| Índice único por documento de identidad | `ux_tercero_documento` | Dos fichas con la misma cédula, escritas distinto |
| Búsqueda **sin acentos ni mayúsculas** | `spanish_unaccent` | La mayoría: los duplicados no se crean por saltarse un índice, se crean por **no encontrar lo que ya está** |
| Único por **identidad + destino** | `ux_radicado_actor_sin_duplicado` | El mismo destinatario, en la misma dirección, dos veces en el mismo radicado |

Nótese la tercera: deduplica por `(radicado, tercero, rol, dirección, correo)` y **no solo por identidad**. El mismo señor en dos direcciones distintas es un envío legítimo —pasa, y hay que poder hacerlo—; el mismo señor en la misma dirección dos veces es un duplicado. Añadirlo repetido es un **no-op**, no un error: el resultado que se quería ya se cumple.

**Al vincular se puede corregir el contacto para ese envío** sin tocar el catálogo: al radicar se busca a alguien y se le ajusta el correo o la dirección, porque pasa. Ese ajuste es lo que se congela; actualizar la ficha es un acto aparte y explícito, y no arrastra nada hacia atrás. El asiento de auditoría anota **cuántos actores se congelaron con un contacto distinto al de la ficha**, que es la señal de que alguien cambió una dirección al radicar.

El alta es **idempotente por documento**: si ya existe un tercero con esa cédula se devuelve ese, con `creado=false`, en vez de un conflicto — de paso cierra la puerta al duplicado por reintento.

**El canal de envío es una condición material, no una preferencia.** Un destinatario se atiende por correo electrónico, en físico o por los dos, y **cada canal exige su dato**: sin correo no hay envío electrónico posible, sin dirección no hay envío físico posible. Lo imponen dos `CHECK` —**un envío imposible no se puede ni registrar**—, porque dejarlo anotado haría que el radicado prometiera algo incumplible y el fallo aparecería al despachar, cuando ya no hay a quién preguntarle el dato.

`canal = NULL` significa **todavía sin decidir**, y se admite a propósito: obligar a elegirlo al vincular empujaría a poner un canal cualquiera para poder seguir, que es peor que decir que no se sabe. Se puede fijar después, y solo si el dato está.

La comprobación va sobre la **copia congelada**, no sobre la ficha: el catálogo puede tener un correo que no se aportó a este envío, y despachar por un dato que no viaja en el radicado sería enviar a un sitio que el radicado no dice. El `422` **nombra al destinatario y el dato que falta** — uno genérico sobre una lista de veinte obliga a buscarlos de uno en uno.

#### La geografía se ELIGE, no se escribe — cascada DIVIPOLA (2026-08-30)

Las tres defensas de arriba atajan el duplicado del **sujeto**. Quedaba abierta la otra mitad, en el **destino**: mientras `municipio` y `departamento` fueron texto libre, «Bogotá», «BOGOTA», «Bogota D.C.» y «Santa Fe de Bogotá» eran **cuatro municipios distintos** para cualquier consulta, cualquier reporte y cualquier exportación. Una restricción de unicidad no lo cierra, porque las cuatro escrituras SON distintas y todas pasan. Lo único que lo cierra es que el municipio deje de ser algo que se escribe.

`GET /terceros/divipola/departamentos` devuelve los **33 departamentos**; `GET /terceros/divipola/municipios?departamento=05` los de ese departamento, con `q` opcional que **ignora acentos** (quien escribe «monteria» encuentra «Montería»). El departamento es obligatorio para pedir municipios: los 1.135 de golpe serían la lista que nadie recorre. Ambos heredan el gate `PERM_RADI` del router.

El catálogo (migraciones **globales** `003` + `004`, schema `public`) es **nacional**: no lo edita la entidad, no varía entre entidades, y replicarlo por schema sería copiar un dato que nadie puede divergir. Son **1.122 entidades vigentes** —1.103 municipios, 18 áreas no municipalizadas y 1 isla— tomadas de la **fuente oficial del DANE** (Datos Abiertos, recurso `gdxc-w37w`, consultado el 2026-08-30). Actualizarlo es un `INSERT ... ON CONFLICT` sobre las mismas tablas, y la carga es idempotente para eso. Un municipio suprimido **no se borra** —hay envíos históricos que lo nombran— sino que se marca `vigente = false`, y así desaparece de la cascada sin dejar de resolver el nombre de un envío pasado.

> **La `003` cargó el catálogo del volcado del Orfeo de origen y no era el del DANE.** Se verificó contra cuatro casos conocidos, los cuatro daban bien, y el resto no se comprobó: escondía **38 diferencias**, entre ellas **Pasto colgado de Antioquia** con el código que pertenece a Alejandría, 18 códigos que DIVIPOLA no reconoce, y 5 municipios ausentes —Tuchín y Nuevo Belén de Bajirá, este incorporado por el DANE en 2023—. La `004` corrige desde la fuente oficial; **no se reescribió la `003`** porque el libro `schema_migrations` identifica por nombre de archivo y no guarda hash, así que un despliegue que ya la aplicó se habría quedado con el catálogo malo en silencio.

**Dos garantías que impiden la incoherencia silenciosa** (migración `027`):

| Garantía | Cómo | Qué evita |
|---|---|---|
| El código de municipio **lleva dentro** el de su departamento | `CHECK (left(codigo,2) = departamento_codigo)` | Un municipio colgado de otro departamento — el error que una cascada mal programada produce sin avisar |
| El departamento se **deriva** del municipio, no se acepta del cliente | Capa de servicio | Aceptar una contradicción que luego hay que detectar |

Y **una asimetría deliberada entre las dos tablas**: en `tercero` los códigos llevan clave foránea al catálogo —la ficha viva debe apuntar a un municipio que existe—; en `radicado_actor` **no la llevan**. Esa fila es la copia congelada de a dónde se envió y tiene que sobrevivir a que el catálogo cambie, incluido que el municipio deje de existir: atarla convertiría una reorganización territorial en una corrupción del registro histórico. Es la misma razón por la que el contacto se copia en vez de leerse por referencia.

El nombre que se guarda pasa a ser el **oficial del catálogo**, no el tecleado, así que la búsqueda, los reportes, el índice electrónico y la exportación —que siguen leyendo las columnas de texto— dejan de ver cuatro escrituras del mismo municipio sin que ninguno tenga que cambiar. El código es **opcional**: una dirección en el extranjero no tiene DIVIPOLA y las fichas anteriores al catálogo siguen siendo válidas — exigirlo habría cerrado la ventanilla por un dato de presentación. Por lo mismo, el municipio entró en la deduplicación por identidad+destino: «Calle 10 #5-20» existe en medio país, y sin él dos envíos legítimos a esa dirección en dos municipios se rechazaban como duplicado.

> **`sender_name` y `sender_entity` NO se tocan.** Cuarenta y tres archivos las leen (búsqueda, reportes, exportación, índice electrónico) y arrancarlas de golpe convertiría una mejora en una migración de riesgo. El modelo nuevo es **aditivo**.

---

## storage-service — puerto 8005

> Requiere header `X-Tenant-Slug`. Archivos almacenados en bucket del almacén de objetos `orpycamcp-{slug}-documents`.

### `POST /api/v1/storage/internal/artefacto`

**Interno** (`X-Internal-Token`, no enrutado por el gateway). Sube un artefacto que produce el propio sistema —hoy, el XML del índice electrónico firmado— con su **propia** lista de formatos (`allowed_system_mime_types`, `application/xml`). Es un camino distinto del de los anexos a propósito: la lista blanca de `/upload` acota lo que entra desde fuera (RF-DIG-04) y no admite XML, así que el índice firmado no se podía almacenar.

### `POST /api/v1/storage/upload`

Sube un archivo (`multipart/form-data`, campo `file`).

- Calcula SHA-256; la **deduplicación es solo del MISMO autor** (AREA-08-09): si quien sube ya había subido ese contenido, retorna su registro con `200`. Si el contenido existe como anexo de **otro** usuario (o de un anónimo/sistema), se crea una fila nueva con **su** `filename` y **su** autor y se responde `201`: antes la respuesta `200` confirmaba que un contenido adivinado existía como anexo reservado y devolvía su `filename` original y su `id`. Sin autor identificado (ventanilla pública, artefactos del sistema) no se deduplica nunca
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

### `GET /api/v1/storage/files/{file_id}/content`

_(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token`.)_ Devuelve el flujo de bytes del archivo para que otro servicio lo procese sin pasar por el navegador (lo usa document-service al mediar la descarga de un anexo, y el empaquetado del AIP). Es la ruta que permite que la **mediación** de la descarga exista: storage queda sellado y el no-read-up se decide en quien conoce el expediente.

### `GET /api/v1/storage/files/{file_id}/download`

Retorna URL pre-firmada del almacén de objetos válida 1 hora.

```json
{
  "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...",
  "expires_in": 3600
}
```

### `GET /api/v1/storage/files/{file_id}/verify`

Reverifica la integridad (RF-DIG-02): descarga el objeto del almacén de objetos, recalcula el SHA-256 y lo compara con el registrado.

```json
{
  "file_id": "uuid",
  "expected_sha256": "...",
  "actual_sha256": "...",
  "integrity_ok": true
}
```

- `409 integrity_check_failed` si el objeto en el almacén de objetos no coincide con la huella registrada (alerta de integridad).

### `POST /api/v1/storage/files/{file_id}/replace`

Reemplaza un anexo creando una **nueva versión inmutable** (RF-DIG-01; `multipart/form-data`, campo `file`). La versión anterior se conserva.

- `201` con la nueva versión (`version` incrementado); `200` si el contenido es idéntico (no se crea versión); `409 not_current_version` si `{file_id}` no es la versión vigente; `415` si el formato no está admitido.

### `GET /api/v1/storage/files/{file_id}/versions`

Devuelve la cadena de versiones del anexo (ordenada por `version`). Cada elemento incluye `version` e `is_current`.

### `DELETE /api/v1/storage/files/{file_id}`

Elimina del almacén de objetos y de la base de datos. Responde `204`.

### Preservación digital (E10, OAIS/AGN 001/2024)

- `POST /api/v1/storage/internal/files/procedencia` `{file_id}` — **interno** (`X-Internal-Token`; B2, ADR-031): `{sha256, uploaded_by, referencias}` de un fichero vigente (`404 file_not_found` si no existe o está borrado). `referencias` son las fuentes (`tabla.columna`) que lo usan hoy (anexos, correo, borrador, acuse de envío, WORM…; una tabla ausente cuenta como `tabla(ausente)` = referenciado, fail-closed); `uploaded_by` puede ser `null` (subida anónima o de sistema). Lo consume document-service para cotejar el acuse de un envío.
- `POST /api/v1/storage/internal/files/hashes` — **interno** (`X-Internal-Token`): SHA-256 de un lote de ficheros. Lo consume `archive-service` al vincular un radicado para poblar el `valor_huella` de cada documento del índice electrónico (E15 T-08, RT-15 art. 4.3.2.3). Un id ausente de la respuesta significa «no hay huella que dar» (borrado o inexistente): quien pregunta lo declara como *sin huella*, no lo inventa.

- `GET /api/v1/preservacion/estado` → `{enabled: bool}` — **estado de instancia** (`PRESERVATION_ENABLED`), no por expediente: no consulta ni filtra nada por expediente, así que no abre un oráculo no-read-up. Gate de lectura `USUA_PERM_EXPEDIENTE` (el mismo que el mediador del AIP). La pestaña Preservación del expediente lo consulta antes de pedir el AIP y, con `enabled=false`, dice «desactivada en esta instalación» en lugar del 404 neutro («sin AIP o sin visibilidad»), que con la preservación apagada hacía pasar por normal que nada se empaqueta jamás. Admitido por subruta en el gateway.
- `GET /api/v1/preservacion/plan` (vigente) · `PUT /api/v1/preservacion/plan` (nueva versión) · `GET /api/v1/preservacion/plan/versions` — Plan de Preservación Digital versionado: `formatos_destino`, `formatos_origen`, `num_copias`, `periodicidad_fixity_dias`, `politica_migracion`, `contingencia`. `formatos_origen` (tipos MIME) es la **política de obsolescencia de la entidad** que consume el barrido de migración (RF-PRE-04): se valida la forma `tipo/subtipo` y se normaliza a minúsculas sin repetidos — un `"msword"` a secas no casaría con ningún archivo y la política aparecería como declarada sin convertir nada, así que se rechaza con `422` en vez de guardarse.
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` (**repetible**: `?object_ref=a&object_ref=b`, hasta 200 por petición; AREA-07-15) — registro inmutable PREMIS. _(Interno, sellado con `X-Internal-Token`, no enrutado por el gateway)_ `POST /api/v1/preservacion/eventos-internal` `{object_ref, tipo, resultado, detalle?, agente?, dedup_key?}` registra un evento que produce **otro servicio** sin una persona detrás de la petición (archive: el fixity-check de la recepción de una transferencia, AREA-07-05); es **idempotente por `dedup_key`** (`201` el primero, `200` con el existente después: la reconciliación reintenta sobre un registro append-only). `tipo` ∈ INGESTA/FIJACION/MIGRACION/VALIDACION_PDFA/WORM/REPLICA; `resultado` ∈ `ok`/`fallo`/**`no_evaluado`** (tercer estado desde E10 C2a, migración `008`: la validación PDF/A con el stub por defecto reporta `no_evaluado` honestamente — `"not evaluated" ≠ "passed"`). Al empaquetar el AIP, cada documento **PDF** (detectado por magic bytes `%PDF`) emite un `VALIDACION_PDFA` (**advisory** — un PDF no-PDF/A no bloquea el AIP) + un evento PREMIS `validation`. Config: `PRESERVATION_PDFA_VALIDATION_ENABLED` (default `false`), `PDFA_VALIDATOR` (`stub`|`verapdf`, default `stub`), `PDFA_PROFILE` (default `2b`). **El validador veraPDF real (subprocess) está implementado (E10 C2b)**: con `PDFA_VALIDATOR=verapdf` + `VERAPDF_BINARY` (ruta absoluta al binario, presente en la imagen de **deploy** — la de dev degrada honestamente a stub `no_evaluado`), `SubprocessVeraPdfValidator` ejecuta veraPDF con modelo de seguridad endurecido (ENV scrubbeado, `killpg`+reap garantizado, semáforo, tempdir 0700, timeout→`no_evaluado`, JSON-only). Tuning: `VERAPDF_TIMEOUT_SECONDS` (60), `VERAPDF_MAX_CONCURRENCY` (1), `VERAPDF_JVM_MAX_HEAP_MB` (512), `VERAPDF_JVM_MAX_METASPACE_MB` (256).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, file_id, nombre}], indice_file_id?, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Increment B, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** empaqueta el **AIP OAIS real**: por cada documento resuelve su objeto por `file_id`, descarga los bytes del almacén de objetos, **recalcula la fixity** (SHA-256 vs `files.sha256`; mismatch → `409` + evento `FIJACION` fallo), arma un **Bag BagIt RFC 8493** (`data/` con los bytes + `bagit.txt` + `bag-info.txt` con Payload-Oxum real + `manifest-sha256.txt` + `premis.xml` [PREMIS v3, namespace oficial, validado contra un perfil XSD local] + `indice-firmado.xml` [si `indice_file_id`] + `tagmanifest-sha256.txt`), lo **serializa ZIP**, lo sube al **bucket WORM** de preservación con `Retention(COMPLIANCE, retain_until)` derivada de la TRD , y lo registra en `preservacion_worm_objeto` (`tipo='aip'` → cubierto por el gate de disposición). **Solo `X-Internal-Token` (D-02, AREA-08-16)**: no se enruta por el gateway; la llama archive-service. `nombre` se valida (sin `/`, `..`, control chars) y debe ser **único** dentro de `documentos` (`422`). `201` con `{id, expediente_id, aip_sha256 (del ZIP), num_documentos, estado, bucket, object_key, aip_bytes, formato, retain_until, retention_mode, legal_hold, premis_xml, replicado, replica_target, replica_copias_hechas, created_at}`; `400` retención inválida; `404/422` `file_id` inexistente o `nombre` duplicado; `409` fixity mismatch o `worm_singleton_conflict` (el `(expediente, tipo)` ya está protegido con **otro** `file_id`; no sube nada); `422` si el body trae `legal_hold`; `503` si `PRESERVATION_ENABLED=false`. **REPLICA (C1b)**: si `PRESERVATION_REPLICA_ENABLED=true` y el plan tiene `num_copias≥2`, tras subir el AIP primario se crea **una réplica inmutable** en un segundo bucket WORM (`replica_target="local"` = segundo bucket del mismo almacén, _no_ copia geográfica; `="cross-site"` si `MINIO_SECONDARY_ENDPOINT`). Es **best-effort no-fatal**: un fallo de réplica **no** revierte el AIP (`replicado=false` + evento `preservacion_evento` `REPLICA` `resultado=fallo`); `num_copias≤1` → sin réplica.
### `POST /api/v1/preservacion/migrar-formato` · `GET /api/v1/preservacion/migraciones/{file_id}`

**Migración de formato contra la obsolescencia** (RF-PRE-04; Decreto 2609/2012 art. 32). Gate `USUA_PERM_EXPEDIENTE` `crud≥3`, el mismo que proteger un artefacto: produce un artefacto nuevo sobre el archivo de un expediente.

La norma pide tres cosas y las tres son invariantes del endpoint:

1. **Conservar el original.** La migración produce un artefacto **derivado** que convive con él; el de origen no se toca, no se marca y no se reemplaza. Un sistema que sustituye el original ha perdido la evidencia el día que alguien discuta si la conversión alteró el contenido.
2. **Registrar versión origen y destino** con su cadena de procedencia (`preservacion_migracion`, migración `015`) y su evento PREMIS **`MIGRACION`** — un tipo que llevaba en el vocabulario desde el principio y que hasta ahora **nadie escribía**.
3. **Verificar la integridad antes y después.** El SHA-256 del origen se **recalcula** sobre los bytes descargados y se compara con el almacenado **antes** de convertir: migrar un archivo ya corrupto propagaría la corrupción a la copia de preservación y le daría además el aspecto de ser la buena. Un desajuste responde `409` y **deja constancia del intento** — un archivo que falla la fixity es justo el que hay que ir a mirar.

Y una cuarta que la norma no dice y el oficio sí: **convertir a PDF/A y no comprobar que el resultado ES PDF/A produce una copia que nadie ha validado.** El derivado pasa por veraPDF, y hay tres desenlaces que no se confunden: `migrado` (conforme), `fallido` (no conforme — los bytes se descartan) y `no_verificado` (se convirtió pero no había validador disponible: **tampoco se promueve** a copia de preservación).

> **`503` si el despliegue no tiene convertidor, y no hay «migrador stub».** La factory gemela del validador PDF/A degrada a un stub honesto porque «no evaluado» es un resultado real; aquí no existe el equivalente — un stub tendría que **producir un archivo**, y el único que puede producir sin la herramienta es uno inventado. Es la misma asimetría deliberada que la transcripción de voz. La imagen de producción instala `ghostscript` y declara `FORMAT_MIGRATOR=ghostscript`.

El perfil se acota a las variantes **`b`** de PDF/A (`1b`/`2b`/`3b`, por defecto `2b`): las `a`/`u` exigen texto etiquetado o Unicode garantizado, y una conversión automática no puede prometerlo. `GET /migraciones/{file_id}` devuelve la cadena de procedencia **incluyendo los intentos fallidos**: «se intentó y no se pudo» es lo que dice qué documentos siguen esperando una migración que no sale.

Puerta en la interfaz: acción **«Migrar a PDF/A»** por anexo en el workspace del expediente, solo sobre PDF, y el aviso de éxito dice que se creó una **copia** — no que el anexo «se convirtió», que es lo que se entendería si no se dijera.

#### Barrido periódico, y de quién es la decisión (2026-08-30)

Hasta aquí la conversión **solo se disparaba a mano, archivo por archivo**. Un archivo institucional tiene cientos de miles de ficheros: una capacidad que hay que invocar uno a uno es una capacidad construida y no ofrecida. El barrido (`FORMAT_MIGRATION_ENABLED`, cadencia `FORMAT_MIGRATION_INTERVAL_SECONDS`, diaria por defecto) recorre los tenants activos y encola lo que falte.

**Qué formato está obsoleto lo declara la entidad, no este código.** Es un juicio archivístico que depende del parque documental de cada institución y de su PINAR (Ac. AGN 003/2015), así que el job no tiene lista propia y **sin política declarada no migra nada**.

Dónde se declara, y en qué orden se lee:

1. **El plan de preservación de la entidad** — `preservacion_plan.formatos_origen`, que se escribe desde `/admin/preservacion` (`PUT /api/v1/preservacion/plan`). Es el sitio propio: queda versionado append-only y dice quién lo declaró y cuándo.
2. **El despliegue** — `FORMAT_MIGRATION_MIME_TYPES`, solo si el plan de esa entidad no dice nada. Es un valor por defecto de la instalación: en una instalación de una sola entidad, quien despliega **es** la entidad; en una compartida, cada entidad lo sustituye declarando su plan.

Nunca al revés. Que la política viviera **solo** en una variable de entorno contradecía la propia regla: una variable de entorno es la misma para todas las entidades de la instalación, no la puede tocar el archivista, no queda versionada y no dice quién la puso. La comparación del tipo MIME es insensible a mayúsculas (RFC 2045 §5.1): el valor guardado es el que mandó quien subió el archivo, y un `Application/MSWord` fuera de la política habría dado cero candidatos sin que nada pareciera roto. Con la política sin declarar el barrido recorre los tenants y no toca un solo archivo. Es el mismo criterio que el barrido de disposición —que cuenta y avisa pero no elimina— con una diferencia que conviene tener presente: **migrar no destruye**; el original se conserva intacto y el derivado convive con él. Lo peligroso aquí no es convertir de más, es creer que se convirtió — por eso el barrido tampoco promueve nada que no haya pasado el validador.

**`fallido` y `no_verificado` no se tratan igual, y la distinción decide qué se reintenta:**

| Estado | De qué habla | Qué hace el barrido |
|---|---|---|
| `migrado` | Hay copia verificada | No vuelve a la cola |
| `fallido` | Del **archivo**: no se pudo convertir, o el resultado no era conforme | **No** se reintenta — reintentarlo cada noche cuesta un subproceso por archivo sin arreglar nada; hace falta que alguien lo mire |
| `no_verificado` | Del **despliegue**: se convirtió y no había validador con qué comprobarlo | **Sí** se reintenta — el día que veraPDF esté instalado, la barrida siguiente lo resuelve sola |

`FORMAT_MIGRATION_BATCH_SIZE` (50 por defecto) acota cada barrida: convertir es caro y sin tope la primera se comería la máquina. Lo que no entra en una barrida entra en la siguiente. Un fallo queda contenido en su archivo y una barrida fallida en su tenant; si no hay convertidor, la barrida **entera** se corta en el primer intento en vez de gastar una descarga por archivo para llegar al mismo `503`.

### `GET /api/v1/expedientes/{id}/historial-acceso`

**Quién leyó y quién descargó** (RF-BUS-10; Ley 594/2000 art. 22; Ley 1712/2014). Gate `USUA_PERM_EXPEDIENTE` + **no-read-up**: un expediente por encima del clearance del llamante responde `404` —no `403`— antes de tocar `audit_log`.

**Ampliado el 2026-08-29 con los accesos a los DOCUMENTOS del expediente.** Hasta entonces la consulta filtraba solo por el **código del expediente** (`object_type ∈ {expediente, expediente_indice}`), mientras las dos trazas de acceso a documentos —`document.radicado_consultado` y `document.anexo_descargado`, escritas por document-service con actor, canal y nivel de seguridad— usan `object_type='radicado'` y `object_ref=<número de radicado>`. El resultado era que **la pregunta «¿quién descargó los documentos de este expediente?» no tenía respuesta en la pantalla hecha para responderla, aunque el dato estuviera guardado desde el principio.**

Las acciones de radicado admitidas son una **lista de admisión y solo de lecturas**: un radicado acumula además creación, anulación y reclasificación, y volcarlas aquí convertiría el historial de *acceso* en un historial de todo. Una acción nueva no entra sola — hay que decidir si es un acceso.

No abre ninguna fuga: los números de radicado ya se listan en la vista del expediente para quien supera su gate, y de cada asiento se devuelven **actor, acción, instante y referencia**; el `payload` —que lleva el nivel de clasificación del documento— **no se pinta**.

> El recuento y la página comparten **una sola condición**. Si vivieran separadas, el total podría contar entradas que la página no muestra, y nadie se enteraría.

- `GET /api/v1/expedientes/{id}/aip` (archive-service) — el AIP del expediente tal y como lo consume la pantalla de preservación; delega en storage-service y **exige `USUA_PERM_EXPEDIENTE`**, así que respeta el no-read-up del expediente antes de exponer el PREMIS.
- `GET /api/v1/expedientes/{id}/preservacion/eventos` (archive-service) — los eventos PREMIS del expediente (validaciones PDF/A, protecciones WORM, renovaciones, empaquetados, **el fixity-check de la recepción de su transferencia**), en orden. Mismo gate. **AREA-07-15**: ya no solo los keyed por el expediente: el servidor resuelve el conjunto de `object_ref` —el expediente, el id de su AIP y los `file_id` de los anexos de sus radicados **vigentes y visibles para el llamante**— y lo pide a storage; los eventos por fichero (validación PDF/A, migración de formato) aparecen. Si document-service no responde, `503 document_service_unavailable` (nunca una lista parcial presentada como completa).
- `POST /api/v1/expedientes/{id}/aip/reintentar` (archive-service, AREA-07-14) — devuelve a `pendiente` un AIP que el job dejó **`agotado`** (el job solo lo pasa a `agotado`; no había camino de vuelta). Exige `USUA_PERM_EXPEDIENTE` **crud ≥ 3**. Un solo `UPDATE` con el estado `agotado` y el clearance del llamante **en el `WHERE`**: sobre un AIP `empaquetado`/`omitido`/`pendiente`, o un expediente que el llamante no alcanza, responde un `404 expediente_not_found` neutro. `intentos` vuelve a 0 y la siguiente barrida lo empaqueta; asiento `archive.aip_reintento_manual` (con `intentos_previos`/`error_previo`) en la misma transacción. `200 {expediente_id, estado: "pendiente"}`.
- `GET /api/v1/preservacion/aip/{expediente_id}` — AIP vigente (requiere `USUA_PERM_EXPEDIENTE` `min_crud=1` — expone `premis_xml` con nombres/hashes de documentos y ubicación WORM). Los campos de ubicación WORM son `null` en filas AIP previas al Increment B (retrocompat).
- `POST /api/v1/preservacion/proteger-indice` `{file_id, expediente_id, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Increment A, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** protege el **índice electrónico firmado** (ya subido a `files`, referenciado por `file_id`) bajo **WORM / Object-Lock (S3)**. Aprovisiona (idempotente) el bucket de preservación por tenant `orpycamcp-{slug}-preservation` (nombre **solo del claim JWT**), **verifica fixity** (recalcula el SHA-256 sobre los bytes y lo contrasta contra el hash **registrado en la subida original** — el llamador **no** declara ningún hash; mismatch → `409` + evento `FIJACION` fallo, no sube), copia el objeto con **`Retention(COMPLIANCE, retain_until)`** por-objeto , registra en `preservacion_worm_objeto` y emite eventos PREMIS `WORM`/`FIJACION` + `audit_log`. La **retención la aporta el caller** derivada de la TRD (storage no llama a archive): `retain_until` directo **o** `retencion_anios` (1–100) + `fecha_inicio` (aritmética de años calendario), nunca ambos. **Solo `X-Internal-Token` (D-02, AREA-08-16)**: ya no se autoriza con el JWT de un usuario y el gateway **no la enruta** (lista de admisión de subrutas de `/preservacion/`); la llama archive-service con la identidad firmada del usuario que cierra. **Idempotente (E10 Inc.1 wiring)**: `201` con la ubicación WORM si es la primera protección; **`200`** con la fila existente si el índice de ese expediente YA está protegido (no re-sube — corte-circuito antes del `put_object`, respaldado por el índice único parcial `WHERE tipo='indice'` y un **advisory lock de sesión por `(tenant, expediente)`** que serializa `check→put→insert` para no duplicar objetos WORM irreversibles bajo carrera). `400` retención inválida (ausente/pasada/fuera de cotas o fuentes en conflicto); `404` si el `file_id` no existe en el tenant; `409` fixity mismatch o `worm_singleton_conflict` (el `(expediente, tipo)` ya está protegido con **otro** `file_id`; no sube nada); `422` si el body trae `legal_hold`; `503` si `PRESERVATION_ENABLED=false`. **Frontera de confianza**: la correspondencia `file_id ↔ expediente_id` (que `file_id` sea el índice firmado del expediente) la garantiza **archive-service** (único invocador); storage valida que el `file_id` exista pero no modela expedientes. **Efecto en disposición**: mientras el índice esté bajo `retain_until` futuro o `legal_hold`, la disposición final del expediente queda **bloqueada** (la eliminación no puede ocurrir antes de expirar la retención — Ley 594/2000, Acuerdo AGN 060/2001).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/proteger-indice-internal` — gemelo **interno** (D-02) de `proteger-indice` con el MISMO servicio/idempotencia, autenticado **solo** por `X-Internal-Token` en vez de `require_permission(crud≥3)` y atribuido al principal de sistema `SYSTEM_RECONCILER_ID`. Existe porque el **cableado WORM del índice** (`IndexService.proteger_worm_indice`, archive-service, E10 Inc.1) tiene dos orígenes: el intento síncrono best-effort tras firmar el índice al cierre (`origen="close"`, usuario real → ruta pública) y la **3ª fase de reconciliación** (`run_once_worm`, `origen="reconciliation"`, `SYSTEM_RECONCILER_ID` sin fila `auth_users` → esta ruta interna; `require_permission` la rechazaría siempre). La retención se deriva de la TRD del expediente (`archivo_gestion_years + archivo_central_years`, `fecha_inicio`=cierre); **fail-closed si la TRD no resuelve** (no protege, marca `trd_sin_retencion`, recuperable por el barrido). Espeja `/signature/internal/sign-indice`.
- `POST /api/v1/preservacion/proteger-artefacto` `{file_id, expediente_id, tipo, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Inc.2 wiring, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** forma **generalizada** de `proteger-indice`: protege bajo WORM/Object-Lock cualquier **artefacto de preservación** del expediente, con `tipo ∈ {`indice`, `acta_transferencia`, `acta_eliminacion`, `acta_firma_elabora`, `acta_firma_entrega`, `acta_firma_recibe`}` (default `indice` si se omite; los tres `acta_firma_*` son las **firmas personales** del acta —AREA-07-10, migración `018` de storage—, un tipo por rol porque la protección es singleton por `(expediente, tipo)`; son instrumentos de control: no posponen la disposición del contenido, pero el `legal_hold` las cubre). MISMO servicio, fixity, retención derivada de la TRD por el caller, solo `X-Internal-Token`, e **idempotencia** que el `proteger-indice`, ahora **por `(expediente_id, tipo)`** (índice único parcial `WHERE tipo='acta_transferencia'` en `preservacion_worm_objeto`, migración `010`) y con el **advisory lock de sesión por `(tenant, expediente, tipo)`** — dos tipos del mismo expediente (índice y acta) no colisionan pero cada uno serializa su propio `check→put→insert`. `/proteger-indice` se conserva como **alias duro** que fuerza `tipo="indice"` sea cual sea el body (retrocompat del Inc.1). Códigos y frontera de confianza idénticos a `proteger-indice`. El **acta de transferencia firmada** (sello XAdES, `transferencia_acta_firma`) la cablea archive vía `TransferenciaService.proteger_worm_acta` (disparo síncrono best-effort tras el sello + 4ª fase de reconciliación `run_once_actas_worm`); su retención se deriva de la TRD del expediente de la transferencia con **`fecha_inicio = firmado_at` del acta** (no el cierre — un acta puede firmarse mucho después en transferencias secundarias).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)_ `POST /api/v1/preservacion/legal-hold-internal` `{expediente_id, activo, motivo (≥10), acta_ref}` — **(AREA-08-07)** único camino para **fijar o levantar** el legal hold de todos los objetos WORM de un expediente. `legal_hold` **dejó de ser un campo** de `aip`/`proteger-*`/`renovar-retencion-internal` (un freeze fijado al proteger no tenía acto inverso y bloqueaba la disposición final para siempre; `422` si llega). Llama a `enable/disable_object_legal_hold` sobre la `version_id` exacta de cada fila (fuera de la tx) y, **en una misma `conn.transaction()`**, actualiza `preservacion_worm_objeto.legal_hold` y asienta `worm.legal_hold_fijado` / `worm.legal_hold_levantado` (con `motivo` y `acta_ref`). Idempotente: las filas que ya están en el estado pedido no se tocan. `200 {expediente_id, activo, objetos_afectados, objetos_sin_cambio}`; `404 worm_object_not_found`; `422` sin motivo; `503` si `PRESERVATION_ENABLED=false`. Solo `X-Internal-Token`. Orden de los pasos: **fijar** = almacén (primaria y réplica del AIP) → fila + asiento en una tx; **levantar** = asiento `worm.legal_hold_levantamiento_solicitado` → almacén → fila + `worm.legal_hold_levantado` (`worm.legal_hold_levantamiento_fallido` si el almacén falla; la fila sigue bloqueando y el acto es reintentable). Serializado por advisory lock `(tenant, expediente)`; la ausencia de réplica se tolera. El asiento incluye `copias_alcanzadas`. Lo orquesta `POST /api/v1/expedientes/{id}/legal-hold` (archive-service).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)_ `POST /api/v1/preservacion/proteger-artefacto-internal` — gemelo **interno** de `proteger-artefacto` (con `tipo` en el body), autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Lo usa la reconciliación desatendida del acta (`run_once_actas_worm`, `origen="reconciliation"`), igual que `proteger-indice-internal` para el índice. `/proteger-indice-internal` sigue siendo el alias duro `tipo="indice"`.
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/renovar-retencion-internal` `{expediente_id, tipo, retencion_anios + fecha_inicio | retain_until}` — **(E10 debt: renovación de retención WORM para series de Conservación Total)** extiende (nunca acorta) el `retain_until` de un artefacto **ya protegido** bajo WORM, identificado por `(expediente_id, tipo)` (`tipo ∈ {indice, acta_transferencia}`). Reusa `RetentionMixin`. Semántica **monotónica**: si la fecha resuelta no supera **estrictamente** el `retain_until` ya persistido, es un **no-op idempotente** (`200`, `renovado=false`, sin tocar el almacén). Si la supera, llama a `Minio.set_object_retention` (extensión real; COMPLIANCE rechaza acortar de todos modos) y responde `200` con `renovado=true`; el UPDATE + evento PREMIS `RENOVACION` + asiento `file.worm_retencion_renovada` van en **una tx** (el `set_object_retention` queda fuera, antes). Autenticado **solo** por `X-Internal-Token` (atribuido a `SYSTEM_RECONCILER_ID`): la renovación es un **acto de sistema** conducido por la 5ª fase de reconciliación de archive (`run_once_worm_renovacion`), único que conoce qué serie es CT — **NO hay ruta pública** (se retiró en la remediación: storage no puede imponer el filtro CT, así que una ruta pública con `retain_until` directo sería superficie irreversible sin garantía CT). `404` `worm_object_not_found`; `400` retención inválida; `503` si `PRESERVATION_ENABLED=false`. `retention_mode`/`legal_hold` **no cambian**. Respuesta: `{id, expediente_id, tipo, bucket, object_key, retain_until, retention_mode, renovado, renovaciones, renovado_at}`.
- `GET /api/v1/expedientes/indices/pendientes-renovacion?page=&size=` — **(E10 debt: visibilidad de la renovación CT)** lista los artefactos WORM de series **CT** (índice + acta) cuya renovación está **agotada** (`worm_renovacion_agotado=TRUE`) o **en riesgo** (`worm_retain_until` vence dentro de `worm_renovacion_ventana_dias`) — para que un archivista vea un lock COMPLIANCE de conservación permanente a punto de expirar sin renovar (espejo de `pendientes-firma`). Gate `USUA_PERM_EXPEDIENTE` + **no-read-up** (el filtro `nivel_seguridad <= clearance` vive en el SQL de ambas consultas; `X-Total-Count` refleja el conteo **ya filtrado** por clearance — no es oráculo). Ordena agotados primero, luego vencimiento más próximo. Por fila: `{expediente_id, expediente_code, tipo, worm_retain_until, worm_renovacion_intentos, worm_renovacion_agotado, worm_renovacion_ultimo_error}`; `tipo ∈ {indice, acta_transferencia, aip, acta_firma_elabora, acta_firma_entrega, acta_firma_recibe}` — **el XML de cada firma personal del acta** (E10 debt, AREA-07-10) entra también a la renovación CT: la 5.ª fase la barre con su propio advisory lock (`worm_renovacion_firma_personal`) y backoff (`transferencia_acta_firma_personal_worm.worm_renovacion_*`, migración `060`), llama a `renovar-retencion-internal` con `tipo=acta_firma_<rol>` (storage lo acepta desde ahora; la fila se identifica por `(expediente_id, tipo)`), **monotónica** (storage solo extiende si la fecha resuelta supera la vigente; el espejo local usa `GREATEST`) y cada extensión real deja asiento `archive.acta_firma_personal_worm_retencion_renovada` (actor sistema).
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)_ `POST /api/v1/preservacion/renovar-retencion-internal` — gemelo **interno** de `renovar-retencion`, autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Único llamador en este incremento: la **5ª fase de reconciliación** de archive-service (`run_once_worm_renovacion`), que barre — por cada tenant activo — los índices y actas **de series de disposición Conservación Total (CT)** cuyo `retain_until` vence dentro de `WORM_RENOVACION_VENTANA_DIAS` (default 400 días) y llama a este endpoint con `retencion_anios=total_TRD` + `fecha_inicio=hoy` (**ventana rodante**, nunca una fecha lejana/9999). El filtro CT es **fail-closed en la consulta SQL** del lado de archive (`disposition ∈ {CT, conserve}`) — series `E`/`S`/`M` nunca entran al barrido, aunque estén próximas a vencer (expiran y se disponen legítimamente). Backoff exponencial propio (`worm_renovacion_intentos`/`worm_renovacion_agotado` en `expediente_indice`/`transferencia_acta_firma`, migraciones `031`/`032`) — un `agotado=true` en este eje es una alarma de **mayor severidad** que en el sellado/protección inicial: implica riesgo de que el lock COMPLIANCE de un registro de conservación **permanente** venza sin haberse podido extender. **AIP queda fuera de alcance** de la renovación (sin espejo en archive-service, deuda futura).

---

## workflow-service — puerto 8006

> Requiere headers `X-Tenant-Slug` y `X-User-Id`.

> **Distribución automática (RF-FLU-04):** además de los endpoints, workflow-service consume el stream `orpycamcp.document.events`. Al radicarse un documento (`document.radicado.created`) evalúa las reglas activas y, si alguna casa, **autoasigna** el radicado a su dependencia (paso + evento append-only, autor = sistema). Es idempotente (no redistribuye si el radicado ya tiene pasos).

#### Dependencias por código

(B5, ADR-032.) La dependencia se correlaciona por su **código** (`dependencias.codigo`), no por su nombre: el flujo
(bandeja, tenencia, ubicación), las reglas, los eventos y los borradores usan `*_dept_code`; `*_dept` es la
**instantánea del nombre en el momento del acto** (las respuestas llevan siempre los dos). Renombrar una
dependencia ya no afecta a ningún trámite.

- **Alias deprecado.** Mientras dura un ciclo, donde antes se enviaba el nombre (`to_dept`, `from_dept`,
  `dest_dept`, `assign_to_dept`, `?dept=`) se sigue aceptando; se resuelve a código solo entre las
  dependencias **activas** con ese nombre **actual** y la respuesta lleva `Deprecation: true` y
  `Link: </docs/es/api.md#dependencias-por-codigo>; rel="deprecation"`. El nombre que se guarda lo pone
  el servidor desde el catálogo, nunca el texto del cliente.
- **Errores `422`**: `dependencia_desconocida` (no existe o está inactiva), `dependencia_ambigua` (el
  nombre corresponde a dos o más; incluye `candidatos` — jamás se elige «la de menor código») y
  `dependencia_inconsistente` (código y nombre que no son la misma dependencia).
- **Origen derivado.** El origen de un traslado lo deriva el servidor del paso activo; el cliente solo puede
  confirmarlo (`from_dept_code`) y, si ya no coincide, `409 origen_desactualizado`.
- **Eventos.** `orpycamcp.workflow.events` (`step.created`/`completed`/`overdue`) y
  `orpycamcp.document.events` (`radicado.created`) llevan **ambos** campos (`to_dept_code`/`from_dept_code`,
  `dest_dept_code`/`origin_dept_code`); el consumidor prefiere el código y, en un evento anterior, resuelve
  el nombre **con historial** (nombres anteriores y mapeos manuales). Si no resuelve a una sola
  dependencia, el código queda ausente y las condiciones por código no casan.

### `POST /api/v1/workflows/assign`

> **Destino por código (ADR-032).** `to_dept_code` es el campo canónico y `to_dept` el alias deprecado; ver «Dependencias por código» más abajo.

Asigna un radicado a una dependencia (primer paso o reasignación). Exige `PERM_TRAMITAR`; `403 forbidden` si falta permiso o tenant (RF-SEG-03). **ADR-028 (remediación):** el *clearance* del actor se aplica **en el `WHERE`** de la lectura del radicado (RF-SEG-08, no-write-up): un radicado inexistente **o por encima de su nivel** da el mismo `404 radicado_not_found`. Con un paso activo, **solo su poseedor** puede repartirlo (`404 no_active_step` neutro para cualquier otro); **sin paso activo tampoco es de cualquiera** (R16): con historial, quien poseía el último paso (completado, radicado no terminal); sin ningún paso, quien radicó (`radicados.registered_by` = actor; un radicado con `registered_by` nulo no lo reparte nadie a mano). Un cerrado da `404 no_active_step`, ya no `409`. Redistribuir el paso de otra persona es la reasignación en cascada (`USUA_PERM_ADMIN`), no esta ruta. El reparto automático al radicar es un actor de sistema y no pasa por estos gates.

```json
// Request
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "to_dept_code": 120,
  "assigned_to": "uuid-usuario",
  "action": "assign",
  "notes": "Para concepto jurídico"
}
// Response 201 — FlowStepResponse (to_dept_code + to_dept = nombre del catálogo)
```

### `POST /api/v1/workflows/transfer`

Transfiere el radicado a otra dependencia (`to_dept_code`). El origen **ya no es obligatorio**: lo deriva el servidor del paso activo (`from_dept`/`from_dept_code` quedan en el paso y el evento); si el cliente lo envía y no coincide, `409 origen_desactualizado`. Exige `PERM_TRAMITAR` (RF-SEG-03). Mismos gates de clearance y tenencia que `assign`.

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

Bandeja tipada de pasos activos (RF-FLU-03). `?box=entrada|salida|internos` (mapea a `doc_type` E/S/I), `&dept_code=&assigned_to=&page=&size=` (`dept=<nombre>` es el alias deprecado: un nombre ambiguo es `422 dependencia_ambigua`, ya no una lista vacía). Prioriza por antigüedad; cabecera `X-Total-Count`. `400 invalid_box` si el box no es válido. Cada paso incluye `due_at` y `semaforo` (verde/amarillo/rojo/vencido, RF-FLU-07).

**Acotado por dependencia (RF-SEG-08):** sin `dept`/`assigned_to`, el llamante ve **lo suyo** — pasos asignados a él o de una dependencia a la que pertenece (URD activa). Pasar `dept` y/o `assigned_to` **amplía** el alcance a otra dependencia u otro usuario y exige `PERM_TRAMITAR` o `USUA_PERM_ADMIN`; sin ese permiso, `403 forbidden` (no se ignora el parámetro en silencio). `X-Total-Count` siempre refleja el mismo filtro que las filas devueltas.

### `POST /api/v1/workflows/overdue/scan`

Barrido de vencimientos (RF-FLU-07): detecta pasos activos vencidos no alertados, emite `workflow.step.overdue` al bus (→ notificaciones) y los marca para no duplicar. Idempotente por paso; pensado para invocación periódica por un scheduler. Responde `{ "alerted": N }`. Exige `USUA_PERM_ADMIN` (barrido transversal del tenant). La reasignación masiva `POST /api/v1/workflows/reassign/cascade` (RF-FLU-08) exige el mismo permiso de administración.

### Vistos buenos secuenciales (paridad legado)

- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — crea la cadena ordenada de revisión (1..20 revisores, sin duplicados). Exige `PERM_RADI` (D-09); `403 forbidden` si falta permiso, identidad o tenant. `409 cadena_existente` si el radicado ya tiene cadena. `422 revisor_sin_habilitacion` si algún revisor tiene un _clearance_ menor que el nivel del radicado (su turno quedaría bloqueado con `404` en `decidir`); el mensaje no revela el nivel.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — el revisor de turno (menor orden pendiente) aprueba/rechaza. Requiere `X-User-Id` válido (400 si falta). `403 not_a_reviewer` si el actor no está en la cadena de revisores de ese radicado; `403 no_es_su_turno` si está en la cadena pero no es su turno; un rechazo detiene la cadena.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — cadena + estado global (`en_revision|aprobado|rechazado`). No-read-up: un radicado por encima del _clearance_ del llamante (o sin identidad, nivel PUBLICA) da `404 sin_cadena`, igual que uno sin cadena. `decidir` y la creación aplican la misma regla (`404 sin_pendiente`/`sin_cadena`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (devolución al remitente)

Re-enruta un radicado mal asignado: `{ "to_dept_code": 120, "causal": "DEV-DEP", "comentario"? }` (`to_dept` es el alias deprecado). Cierra el paso activo como `returned` y crea uno nuevo hacia esa dependencia, con la causal en el historial. Exige **`PERM_TRAMITAR`** (como `assign`, `transfer` y `complete`; esta ruta y `rollback` eran las dos únicas escrituras de trámite del router sin ningún gate). Autorización por tenencia además del permiso: solo el responsable actual del paso activo puede devolver. Tenencia = la misma definición de poseedor de `finalizar` (asignado al actor, o paso de su dependencia sin dueño) **y** el radicado al alcance de su clearance (R15, en el `WHERE`); paso y tenencia se leen dentro de la transacción tras el cerrojo (R18). `404 no_active_step` neutro para quien no es el tenedor (ya no hay `403 not_current_holder`), para quien no alcanza el radicado y si no hay paso activo.

### `POST /api/v1/workflows/{radicado_id}/finalizar` (finalizar trámite, ADR-028)

`{ "comentario": "...", "motivo_cierre": "respondido|nrr|traslado_competencia|otro" }` (ambos **obligatorios**; comentario de 5-500 caracteres; `422` si falta o es otro el motivo). Exige **`PERM_TRAMITAR` nivel 3** (`403` con un nivel menor: el cierre es irreversible) y ser el **poseedor** del paso activo (`assigned_to` = actor, o paso de dependencia sin dueño y el actor pertenece a ella) —o, sin paso activo, haber sido el poseedor del **último paso completado** (completó el suyo y no repartió) si el radicado no es terminal—. Cierra el paso activo como `completed` y el radicado pasa a `closed`, atómicamente, con evento `tipo_tx=finalizar` (`detalles.motivo_cierre`) y asiento `workflow.finalizar` cuyo payload lleva `motivo_cierre`, `step_id`, `flow_event_id`, `tiene_respuesta` (existe una Salida con `responde_a` = este radicado), `fuera_de_plazo` (manda el plazo del paso, `due_at` vs. ahora, si existe; si no, el vencimiento legal del radicado, `radicados.due_date` < hoy; `null` solo si no consta ninguno) y `en_expediente` (`null` = no consta, p. ej. el tenant aún no tiene esa tabla). Es **el único acto que cierra un radicado** y no se deshace. Un no-poseedor, quien no alcanza el nivel del radicado (R15), o un radicado sin paso activo (incluido uno ya cerrado), recibe el mismo `404 no_active_step` (no-oráculo). `409 radicado_terminal` si el radicado está `anulado`. Responde el paso cerrado.

### Estado del radicado y actos de trámite (ADR-028)

**Ubicación actual (ADR-029).** En el mismo `UPDATE` que `radicados.status` —misma transacción, antes del asiento— el workflow-service escribe `flujo_dept`, `flujo_dept_code` y `flujo_usuario`: el paso activo (mayor `step_number`) o, sin paso activo, el último cerrado (`completed_at`, no el número de paso: tras un rollback el cancelado tiene el número más alto pero cerró antes). Cubre asignar, trasladar, devolver, revertir, completar, finalizar y la reasignación en cascada. Los radicados terminales se **congelan**: uno cerrado queda donde se finalizó, uno anulado donde estaba al anularse. El código solo se resuelve cuando el nombre del paso casa con **una** dependencia activa; si no, `flujo_dept_code` es `null` y el nombre queda como instantánea. Ver `GET /api/v1/documents` y [ADR-029](adr/ADR-029-ubicacion-actual-del-radicado.md).

`radicados.status` lo escribe el workflow-service **en la misma transacción que el paso** —antes del asiento de auditoría, así que si el asiento falla revierte todo—: paso activo `pending` → `distributed`; `in_progress` → `in_progress` (solo por `marcar_leido` del poseedor); completar sin marcar leído deja `distributed` (declarado). `closed` y `anulado` son **terminales**: `assign`, `transfer`, `complete`, `devolver`, `rollback`, `finalizar` y las transacciones que mueven el paso responden **`409 radicado_terminal`** (`El radicado está cerrado o anulado y no admite actos de trámite`) y no cambian ni un paso, evento o asiento; `informar`/`agendar` siguen permitidas. El cambio queda en el payload del asiento existente (`"estado_radicado": {"antes", "despues"}`) y, tras el commit, se publica `workflow.radicado.estado_cambiado` en `orpycamcp.workflow.events` (dedup `{radicado_id}:{despues}:{flow_event_id}`). Los `GET` nunca sincronizan. `inbox`, `pending` y los vencidos **excluyen** los pasos de radicados terminales. Las salidas (`S`) y los internos (`I`) no tienen reparto fuera: su estado solo se mueve cuando un paso los toca. **La anulación cancela el paso activo** (en la misma transacción, `flow_events.tipo_tx=anulacion`).

### Transacciones de trámite (RF-FLU-01)

- `GET /api/v1/workflows/transaction-types` — catálogo (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), con su permiso atómico, efecto de estado y `requires_holder` (ADR-028, R17: **toda** transacción —con o sin tenencia— aplica el clearance del actor en la lectura del último paso: un radicado inalcanzable da `404 no_flow`, igual que uno inexistente o sin pasos, sin devolver su `tracking_number`/`to_dept`. ADR-028: si es `true` —hoy `marcar_leido` y `anular`— únicamente el poseedor del paso activo puede ejecutarla; un no-poseedor recibe el mismo `404 no_active_step` que si no hubiera paso, **también sobre un radicado terminal** —no se le revela con un `409 radicado_terminal`—. Además, **toda transacción que mueve el paso** (`target_step_status`) exige tenencia, la declare o no su fila: el catálogo es configurable por el tenant).
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — ejecuta la transacción: aplica el efecto al paso activo (p. ej. `marcar_leido`→in_progress, `anular`→cancelled) y registra el evento append-only. **Enforcement RBAC por-tipo (RF-SEG-03):** cada tipo declara en `transaction_types.requires_permission` el permiso que exige (`anular`→`PERM_ANULAR`, `cerrar_exp`→`PERM_CERRAR_EXP`, `vobo`→`PERM_VOBO`…); sin él, `403 forbidden` y la transacción **no** se aplica. Un tipo con `requires_permission = NULL` **cae al suelo `PERM_TRAMITAR`**, no es abierto: `transaction_types` es un catálogo del tenant, y dejar la columna vacía no puede ser la manera más fácil de abrir la puerta. Hoy los 12 tipos sembrados declaran el suyo, así que el suelo no cambia nada observable. No hay gate de ruta fijo a propósito: exigir `PERM_TRAMITAR` a todas rompería a quien tiene `PERM_VOBO` para dar vistos buenos y nada más. `404 unknown_transaction` / `no_flow`. `PATCH /{step_id}/complete` exige `PERM_TRAMITAR`.

### `POST /api/v1/workflows/{radicado_id}/rollback`

`{motivo}` (**obligatorio**, 5-500 caracteres). Exige **`PERM_TRAMITAR`**. Revierte la última asignación (RF-FLU-08): cancela el paso vigente —solo si sigue `pending`— y reactiva el anterior, atómicamente, con evento `tipo_tx=rollback`. El `motivo` queda registrado en dos trazas atómicas con la mutación (una sola transacción): `flow_events.comentario` (hoja de ruta operativa) y `public.audit_log.payload` (traza legal forense, mismo patrón que la asignación). Misma tenencia y mismo clearance que `devolver` (R15, R18, R19). `409 no_active_step` (sin paso activo, no poseedor o radicado inalcanzable: neutro) o `cannot_rollback_initial` (es la radicación inicial). `422` si falta el `motivo` o no alcanza el mínimo de caracteres.

### `POST /api/v1/workflows/reassign/cascade`

Reasigna en una transacción todos los pasos activos de un usuario o dependencia a un nuevo responsable, sin dejar radicados huérfanos (RF-FLU-08).

```json
// Request (indicar from_assigned_to o from_dept_code; from_dept es el alias deprecado)
{ "from_dept_code": 120, "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12, "skipped_terminal": 0 }
```

ADR-028: reasignar equivale a que el nuevo responsable **no ha leído** el radicado (Orfeo `LEIDO=0`): los pasos vuelven a `pending` y el radicado a `distributed`, con un asiento `workflow.estado_radicado` por radicado cuyo estado cambió. Los radicados **terminales** (`closed`/`anulado`) se excluyen —su paso no se reasigna— y se cuentan en `skipped_terminal` (pasos omitidos). Concurrencia: un **único** cerrojo advisory de alcance del tenant (exclusivo aquí, compartido en cada acto de trámite) más cerrojos de fila ordenados por `id` sobre los radicados —no uno advisory por radicado, que agotaba `max_locks_per_transaction` en un barrido grande—; mientras dura, los demás actos de trámite del tenant esperan.

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

Radicados pendientes. Filtros: `?dept_code=120&assigned_to={uuid}&page=1&size=20` (`dept=<nombre>` es el alias deprecado, mismo criterio que `/inbox`).

**Acotado por dependencia (RF-SEG-08):** mismo criterio que `/inbox` — por defecto, lo propio (asignado al llamante o a una dependencia a la que pertenece por URD activa); `dept`/`assigned_to` amplían el alcance y exigen `PERM_TRAMITAR` o `USUA_PERM_ADMIN` (`403 forbidden` sin el permiso).

### `PATCH /api/v1/workflows/{step_id}/complete`

Marca un paso como completado. **ADR-028:** solo el **poseedor** (`assigned_to` = actor, o paso de dependencia sin dueño y el actor pertenece a ella; la misma definición que `finalizar` y `marcar_leido`) y solo un paso **activo**: un paso ajeno, inexistente, ya cerrado **o de un radicado por encima del clearance del actor** (R15, en el mismo `WHERE`) da el mismo `404 step_not_found`.

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
- **Fondo acumulado** (ADR-026): `origen: "fondo_acumulado"` exige `fecha_extrema_final` y `trd_serie_id` (de una TVD) desde la creación; `fecha_extrema_inicial` es opcional. **`422`** si falta alguno, si **`fecha_extrema_final` es futura** —es la base de un `retain_until` Object-Lock **COMPLIANCE irreversible** (`schemas/archive.py`): una fecha futura bloquearía la disposición del acervo por décadas— o si `fecha_extrema_inicial > fecha_extrema_final`. Con `origen: "corriente"` (el valor por defecto) las fechas extremas **no** se aceptan (`422`). La misma regla rige en la carga masiva.

#### `POST /api/v1/expediente-metadata/templates`

Define la plantilla de metadatos (JSON Schema) de una serie TRD. Una sola activa por serie. `422 invalid_json_schema` / `409 conflict`. `GET` lista (filtro `?trd_serie_id=`).

#### Transferencias documentales (E12)

> **Control de acceso (D-08):** todos los endpoints requieren `USUA_PERM_EXPEDIENTE`. Sin él: `403 {"error": "forbidden"}`.

- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, objeto, ubicacion_origen_id?, ubicacion_destino_id?, observacion?}` — crea (precondición: expediente `closed` **con el índice electrónico `firmado`**: `409 expediente_not_closed` si no está cerrado y **`409 indice_no_firmado`** —AREA-07-03— si el índice vigente no está firmado, con asiento de denegación `transferencia_preparada_failed`; antes solo `recibir` lo exigía y el defecto avanzaba hasta la última fase). Ciclo `preparada→enviada→recibida|rechazada`. Cada acto (crear/enviar/recibir/rechazar) deja asiento inmutable en `audit_log` (éxito y denegación) con el actor identificado unívocamente por `X-User-Id` (UUID; sin fallback a username). **Integridad (E12 H-L/H-M)**: a lo sumo **una transferencia activa** (`preparada`/`enviada`) por expediente — una 2ª da `409 transferencia_activa_existente` (índice único parcial; tras un `rechazada` se puede re-preparar); y si se aportan `ubicacion_origen_id`/`ubicacion_destino_id` deben ir **ambas** y ser **coherentes con el tipo** (`primaria`: origen `gestion`→destino `central`; `secundaria`: `central`→`historico`; Ley 594/2000 art. 23), con `origen != destino` — si no: `422` (`ubicaciones_par_incompleto` | `ubicaciones_origen_destino_iguales` | `ubicacion_not_found` | `ubicacion_incoherente_con_tipo`). **Obligatoriedad condicionada a la tenencia física**: si el expediente tiene ubicación física localizada (vía `expediente_unidad`, que es M2M), el par es **obligatorio** (`422 ubicaciones_requeridas_fisico`) y `ubicacion_origen_id` debe **cotejar** — estar entre las ubicaciones actuales del expediente (`422 ubicacion_origen_no_actual`; vale cualquiera de las M2M); si el expediente es puramente **electrónico** (sin unidades físicas), la transferencia es **lógica y NO lleva par**: aportarlo sin tenencia que lo respalde es **`422 ubicacion_sin_tenencia`** (AREA-07-06; antes se aceptaba sin cotejo y el acta FUID atestiguaba un movimiento que ningún vínculo sustentaba). La tenencia se lee **dentro de la transacción del acto con `FOR SHARE`** (no antes: una unidad que se movía entre el cotejo y el `INSERT` dejaba el acta cotejada contra una ubicación que ya no era la actual). Si la ubicación se borra en la carrera, la FK ya no da `500` sin asiento: es `422 ubicacion_not_found` con asiento (AREA-07-08). El destino no se coteja (solo coherencia de tipo — es a dónde va). Lectura de topología física sin clearance (custodia ortogonal). `transferencias.objeto` es obligatorio también **en la base** (`CHECK` desde la migración `055`, solo para filas nuevas: el histórico sigue operable).
- `POST /{id}/enviar`, `POST /{id}/recibir`, `POST /{id}/rechazar`. Al **recibir**, dentro de una única transacción: se **re-verifica la fixity** del acervo (SHA-256 de cada documento vs la huella del índice + re-hash del XML del índice; `409 fixity_mismatch` que revierte sin congelar si el acervo se alteró entre el cierre y la recepción — la respuesta lleva solo `num_documentos_fallidos`, el detalle por-documento queda en `audit_log`), el expediente pasa a `transferred` (congelado), y se **congela el acta de entrega** (ver `/{id}/acta`). El **rechazo** exige `{motivo}` (obligatorio, `min_length=3`, no vacío → `422`); el motivo se guarda en columna propia sin sobrescribir la observación de preparación.
  **Cabecera del Anexo FUID (migración 052, Ac. AGN 001/2024)**: `objeto` es **obligatorio** (3–500 caracteres) — encabeza el acta de entrega y lo escribe quien prepara, porque es un dato humano que ninguna derivación puede inventar. La `oficina_productora` y la `unidad_administrativa` NO se aceptan del cliente: las resuelve el servidor desde la **dependencia activa de quien prepara** (`auth_users.active_depe_id`, con caída a su URD principal) y su dependencia **padre** respectivamente; una oficina raíz es su propia unidad administrativa. Se congelan como instantánea en la preparación —no al recibir— porque entre las dos cosas pueden pasar semanas. Si los catálogos de auth/tenant no están en el esquema, la cabecera queda vacía y la preparación sigue (degradación declarada, nunca un error).

- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (FUID **en vivo** del expediente, regenerado al vuelo).
- `GET /{id}/acta` (`?verify=true`) — **acta de entrega CONGELADA** al recibir (E12 H-I, Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26). Devuelve el snapshot inmutable escrito en la recepción (nunca lo regenera): `TransferenciaActaResponse` con el XML canónico del FUID, su `acta_fuid_sha256` (fixity del acta, recomputable por el cliente), los tres responsables `elaborado_por`/`entregado_por`/`recibido_por` con sus fechas, e `indice_version` transferida. **AREA-07-04** (migración `053`): además **`indice_firma_id`** (el sello XAdES que ampara el índice entregado) e **`indice_sha256`** (la huella del XML que ese sello cubre), leídos de la **misma fila** de `expediente_indice` que usó la fixity y embebidos en el XML que se hashea y se sella (`<indiceElectronico version firmaId sha256>` en la cabecera); una versión del índice que no esté firmada impide recibir (`409 indice_no_firmado`). `null` en las actas anteriores (no se inventa un vínculo en un acta ya sellada). **AREA-07-11**: **`cabecera_completa`** (derivado al leer): `false` si el XML congelado trae vacía la unidad administrativa, la oficina productora o el objeto —las actas anteriores a la migración `052` se sellaron así y no se rehacen—; se declara el hueco en vez de esconderlo. **AREA-07-05**: la recepción registra además su fixity-check como evento PREMIS `FIJACION` en storage (post-commit, best-effort, con reconciliación). **Bloque `firma`** (E06 H-H): `{presente, valida, xades_level, acreditado, motivo}` — el acta lleva un **sello XAdES-B/T/LT/LTA + OCSP institucional del tenant** (enveloped sobre el XML canónico, cubre los bytes cuyo SHA se ancla en `audit_log`); `presente=true` cuando el sellado convergió a `firmado`; con `?verify=true` se computa `valida` on-demand (descarga el XML firmado del almacén de objetos y lo verifica contra signature-service). `acreditado` **siempre `false`** (sello no acreditado ONAC — fecha cierta **verificable, no oponible en sentido fuerte**; `xades_level` se lee junto a `acreditado`). Sellado **best-effort** (la firma es aditiva): si aún no se selló, `firma.presente=false` con `motivo` (`pendiente_firma`/`sello_ausente`/…) — el acta sigue **íntegra** por fixity + inmutabilidad + anclaje. **No-read-up** en la lectura (el acta expone asunto/serie/signatura = contenido): `404` total si el expediente supera el clearance del llamante — asimétrico con la **generación**, que ocurre con clearance sin restricción (custodia). La inmutabilidad del acta es **forzada por trigger** a nivel de motor (`BEFORE UPDATE/DELETE/TRUNCATE`, migración `023`) además de verificable por fixity y anclada en la hash-chain. **Constancias de firma inobtenible**: `excepciones_firma[]` (mismo objeto que `firma-inobtenible`); el **`fundamento` (y `revocacion_fundamento`) completo solo se devuelve a `USUA_PERM_ADMIN` nivel ≥ 3** (dato sensible: salud, fallecimiento) — al resto le llegan `motivo`, `referencia_documental` y `fundamento_sha256` con `fundamento=null`; igual en `/acta/verificacion`.
- `POST /{id}/acta/firmar` — reintento **manual** del sellado XAdES del acta `pendiente_firma` (E06 H-H). `USUA_PERM_EXPEDIENTE`, atribuido al humano que lo dispara (`origen="manual"`). `409 acta_ya_firmada` si ya está firmada (idempotente), `503` si sigue sin poder sellarse. **Re-arma** el backoff de reconciliación para que el job automático reanude. La firma del acta también converge sola vía el job de reconciliación generalizado (cadencia menor que el índice).
- `POST /{id}/acta/firmar-personal` `{totp_code, rol?}` — **firma electrónica PERSONAL PKI de un RESPONSABLE del FUID** (ELABORADOR "Elaborado por" rol `elabora`, REMITENTE "Entregado por" rol `entrega`, o RECEPTOR "Recibido por" rol `recibe`) sobre el acta de entrega (E17/F4 Inc.1-3; Ac. AGN 001/2024 Anexo FUID; Ac. AGN 042/2002; Ley 527/1999 art. 7). Paso **explícito posterior** a `recibir()` (nunca inline: preserva la tx de custodia sin 2FA); los roles se firman **en cualquier orden**, cada uno como acto atómico independiente. **El rol se DERIVA de la identidad congelada**: `actor == creada_por` → `elabora`, `actor == enviada_por` → `entrega`, `actor == decidida_por` → `recibe`. El `rol` del body es **opcional** y solo desempata cuando la misma identidad tiene varios roles (auto-traslado: `400 rol_requerido` si falta): NUNCA concede un rol — un tercero que no es ninguno de los tres responsables recibe **`403 firmante_no_es_parte`** (y también si pide vía `rol` un papel que su identidad no otorga), **antes** de consumir el TOTP. `USUA_PERM_EXPEDIENTE` + **no-read-up** real sobre el expediente para todos los roles (`404` neutro sobre-clearance); `409 transferencia_no_recibida` si aún no está `recibida`. Archive **media**: reenvía el `Authorization`/TOTP del responsable a signature-service `POST /signature/sign-personal` (verifica-y-consume el TOTP atómico y firma el `acta_fuid_xml` congelado con su cert personal). **Idempotente** sobre `(transferencia_id, rol)`: repetir un rol ya firmado devuelve `200` con la fila existente, sin re-firmar ni re-quemar TOTP. `503 canal_firma_personal_no_disponible` si signature no responde. Devuelve `TransferenciaActaFirmaPersonalResponse` (`rol`, `firmante_id`, `firma_id`, `xades_level`, `acreditado` **siempre `false`**). Registrada en `transferencia_acta_firma_personal` (**sin** reconciliación desatendida de la FIRMA — requiere TOTP interactivo). **AREA-07-10**: el XML de cada firma personal entra al **almacén WORM** (tipo `acta_firma_<rol>` en storage), post-commit y best-effort, con reconciliación con backoff propia (`transferencia_acta_firma_personal_worm`, migración `057`; antes solo el sello institucional tenía Object-Lock). Si una parte no puede firmar, ver el acto `firma-inobtenible` (más abajo). El gate `acta_firma_personal_require` (default `off`) **nunca bloquea `recibir()`** ni la firma de otro rol; solo modula el veredicto de conformidad.
- `GET /{id}/acta/verificacion` — **verificación agregada** del acta (E17/F4 Inc.1-3). Compone la verificación del **sello institucional** + la de **cada firma personal** presente (descarga los XML firmados del almacén de objetos y los verifica contra signature-service). Devuelve `{sello: {...}, firmas_personales: [{rol, firmante_id, valida, revocacion_status, advertencias}], completitud, valida, acreditado, auto_traslado, elaborador_firmado, excepciones, advertencias, constancias_sin_firma, politica_xades}`; `advertencias` son los avisos de signature `/verify` de una firma que sigue siendo válida (p. ej. `tsa_sin_esscertid_anterior_al_corte`), sin repetir; `constancias_sin_firma` cuenta las excepciones vigentes **anteriores a la migración `061`** (sin sello: no se dan por válidas y `valida` es `false`): `completitud ∈ {sellada, firma_personal_pendiente, firmada_receptor, firmada_remitente, firmada_completa, firmada_con_excepcion, sellada_con_excepcion}` (derivada, presencia factual — `firmada_completa` = **ambas PARTES DEL TRASPASO** (entrega+recibe) firmaron y **no hay ninguna excepción declarada** = **atribución bilateral reforzada**, NO conformidad jurídica plena; `firmada_con_excepcion` = cada parte firmó o tiene una excepción de firma **vigente** (`/acta/firma-inobtenible`), **al menos una de las dos firmó** y existe alguna excepción vigente de cualquier rol — **nunca** se promueve a `firmada_completa`; `sellada_con_excepcion` = las dos partes están cubiertas **solo por excepción** (ninguna firmó: el acta conserva únicamente el sello de la entidad, no se la llama «firmada»); una excepción **revocada** no cubre a su parte; la lista de constancias —las revocadas incluidas, marcadas— va en `excepciones` (mismo objeto que la respuesta de `firma-inobtenible`, con `sello_valida` y `firma_personal_valida` verificados al momento). **`firma_personal_pendiente` se calcula con la política XAdES de la ENTIDAD** (`firma_personal_require_xades` de `sgd_param_admin`; el entorno solo respalda si no la definió; valor corrupto = `todos`), que se devuelve en `politica_xades`. El **ELABORADOR es firmante ADITIVO** (no parte del traspaso): su firma NO condiciona `completitud`, se reporta en el booleano ORTOGONAL **`elaborador_firmado`**. `valida` = cripto + revocación de **todo** lo presente (incluida la firma del elaborador, fail-closed → un cert de elaborador revocado da `valida=false` aun con `firmada_completa`; los dos ejes de ortogonalidad difieren a propósito), `acreditado` **siempre `false`**, `auto_traslado=true` cuando la misma identidad envió y recibió (**observación de conformidad**: sin control dual — el FUID no exige personas naturales distintas, se señala pero no se impide). **No-read-up** idéntico a `/{id}/acta` (`404` neutro sobre-clearance; los cuerpos no enumeran radicados reservados).
- `POST /{id}/acta/firma-inobtenible` `{rol, motivo, fundamento, referencia_radicado_id, referencia_anexo_id, totp_code?}` → `201` constancia — **acto administrativo «firma inobtenible»** (AREA-07-10, ADR-030): una parte del acta (`elabora`/`entrega`/`recibe`) que **no puede firmar** (fallecimiento, retiro, incapacidad) se declara de forma explícita. Gate **`USUA_PERM_ADMIN` nivel ≥ 3** (no el permiso de transferencias con el que firman las partes) **y `PERM_FIRMA` ≥ 2** (el sello se estampa a nombre del declarante: `403 declarante_sin_permiso_de_firma`). `motivo ∈ {fallecimiento, retiro, incapacidad, otro}`; `fundamento` (20–2000 caracteres) obligatorio. **El soporte documental ya no es texto libre**: `referencia_radicado_id` (UUID de un radicado) y `referencia_anexo_id` (**obligatorio** desde FIRMA-2: `file_id` de uno de sus anexos, del que el servidor trae el SHA-256 desde storage; `503 soporte_documental_sin_huella` si no puede) los **resuelve el servidor** contra document-service con el clearance del declarante — `422 referencia_documental_invalida` (un solo cuerpo para «no existe» y «no lo puede ver»), `503 document_service_no_disponible`; se guarda en forma canónica `radicado:<uuid>[#anexo:<uuid>]`. La identidad de la parte la **deriva el servidor** del rol (`enviada_por`/`decidida_por`/`creada_por`), nunca el cliente. **La constancia es PRUEBA**: un XML en **C14N exclusiva** (`urn:orpycamcp:acta-firma-excepcion:v3`: huella del acta congelada, transferencia, rol, parte, motivo —`incapacidad` se escribe `impedimento_sobreviniente`—, compromiso del fundamento, soporte, declarante, instante) **sellado con el sello institucional XAdES-T** (mismo camino que el acta; si no hay sello → `503 sello_institucional_no_disponible`/`sellado_constancia_no_obtenido` y **no se declara nada**) y, si el declarante envía `totp_code` y tiene credencial activa, **firmado también por él** (XAdES personal sobre los mismos bytes). La firma personal es **OBLIGATORIA** cuando la política de la ENTIDAD (`firma_personal_require_xades`, parámetro de tenant) es `todos`, o `clasificados` con expediente de nivel ≥ 2 (`422 firma_personal_declarante_requerida` sin `totp_code`; `422 sin_certificado_activo` sin credencial). Ambos XML entran al **almacén WORM** (tipos `acta_firma_excepcion_<rol>` y `acta_firma_excepcion_personal_<rol>` en storage, migración `019`), post-commit y best-effort, con reconciliación con backoff y renovación CT (`transferencia_acta_firma_excepcion_worm`, migración `061`). Deja una **constancia inmutable** (`transferencia_acta_firma_excepcion`, migraciones `059`/`061`: append-only por trigger y por privilegios, una por rol) y un asiento `archive.acta_firma_inobtenible_declarada` **en la misma transacción**; el asiento lleva las huellas del fundamento y de la constancia, **no el texto** (el registro es permanente y puede aludir a salud o fallecimiento). El acta congelada y su huella **no se tocan**. **Conflicto de interés**: quien declara no puede ser **ninguna de las tres partes** de la transferencia (`enviada_por`, `decidida_por`, `creada_por`): `403 declarante_es_parte_de_la_transferencia` con asiento `archive.acta_firma_inobtenible_denegada`; lo repite un trigger en el motor. Exclusión mutua en el **motor** (trigger con advisory lock por (transferencia, rol)): una parte que ya firmó no admite excepción (`409 parte_ya_firmo`) y una parte con excepción **vigente** ya no firma (`409 firma_excepcion_declarada` en `firmar-personal`, **antes** de gastar el TOTP). **Cadena (FIRMA-2, ADR-030 addendum, migración `062`)**: sobre una excepción **vigente** es `409 excepcion_vigente`; tras **revocarla** se puede declarar de nuevo (`ordinal` n+1, `sustituye_a` = la revocada; `UNIQUE (transferencia_id, rol, ordinal)` y `UNIQUE (sustituye_a)`: cadena lineal) **solo si la revocación está sellada y todos sus artefactos protegidos WORM** (`409 revocacion_sin_proteger`; lo repite un trigger con `CONSTRAINT ck_firma_excepcion_revocacion_sin_proteger`); si la parte firmó personalmente —también durante la ventana revocada— la cadena está cerrada (`409 parte_ya_firmo`: la firma prevalece). **Constancia v3 autosuficiente** (`urn:orpycamcp:acta-firma-excepcion:v3`): lleva el número del radicado y el SHA-256 del anexo, nombre y cargo de declarante y parte como instantánea resuelta por el servidor (CN del certificado de firma activo, o `username`; `fuente` = `certificado`/`usuario`/`no_resuelta`), y el fundamento se huella con un **compromiso con sal por registro**, `SHA-256(sal_32B || texto_utf8)` (ronda de auditoría, migración `063`): la sal es aleatoria, vive en la base (no WORM) y **nunca** va al XML ni a `audit_log`; un tercero verifica si se le entregan texto y sal de ese registro. `fundamento_sha256` y `fundamento_hmac*` **desaparecen** de la respuesta y del asiento. `huella_fundamento_no_disponible` ya no existe (se calcula en local). El XML firmado es la **C14N exclusiva** y `constancia_sha256` su SHA-256: un tercero que quite la `ds:Signature` y canonicalice obtiene la misma huella. El asiento `archive.acta_firma_inobtenible_declarada` ya no lleva el número del radicado de soporte (quedan el id y la huella del anexo). **El declarante con certificado de firma activo siempre firma personalmente** (`422 firma_personal_declarante_requerida` sin `totp_code`), no solo por política. Las referencias (`referencia_*`) se recortan por el clearance del lector (`null` si no ve el radicado, indistinguible de «no existe» o de un error de document-service). Solo sobre un acta ya congelada (`409 transferencia_no_recibida`); no-read-up real sobre el expediente (`404` neutro). Respuesta: `TransferenciaActaFirmaExcepcion` `{id, transferencia_id, rol, parte_id, motivo, fundamento, ordinal, sustituye_a, constancia_version, fundamento_compromiso, fundamento_compromiso_algoritmo, fundamento_sal (solo admin ≥ 3), referencia_documental, referencia_radicado_id, referencia_radicado_numero, referencia_anexo_id, referencia_anexo_sha256, declarada_por, declarada_en, declarante_nombre/cargo/fuente, parte_nombre/cargo/fuente, firmada, constancia_sha256, xades_level, firma_personal, xades_level_personal, firma_personal_requerida, worm_protegido, worm_personal_protegido, worm_retain_until, sello_valida, firma_personal_valida, enlace_valido, enlace_motivo, advertencias, revocada, revocacion_id, revocada_por, revocada_en, revocacion_fundamento, revocacion_causa, revocacion_fundamento_compromiso, revocacion_fundamento_compromiso_algoritmo, revocacion_fundamento_sal (solo admin ≥ 3), revocante_nombre/cargo/fuente, revocacion_firmada, revocacion_sha256, revocacion_worm_protegido, revocacion_worm_retain_until, ...}`. **Retención común de la cadena**: todos los eslabones de un (transferencia, rol) se protegen hasta el mayor entre el sellado del acta y el último acto de la cadena, y al protegerse uno nuevo se alinean (monotónico, con asiento `..._retencion_renovada`, `origen=alineacion_cadena`) los anteriores. **Privacidad**: el motivo `incapacidad` viaja neutro en XML y asiento; estos artefactos no se publican por OAI-PMH, EAD ni CMIS. **Efecto sobre `acta_firma_personal_require`**: la excepción **cubre** a su parte, pero una excepción parcial no apaga el gate por sí sola.
- `POST /{id}/acta/firma-inobtenible/revocar` `{rol, causa, fundamento, totp_code?}` → `201` — **revoca la excepción vigente de un rol** (se declaró por error, o la parte sí puede firmar). Misma puerta que declarar (`USUA_PERM_ADMIN` ≥ 3) y misma regla de conflicto de interés (`403 declarante_es_parte_de_la_transferencia`, asiento `archive.acta_firma_inobtenible_revocacion_denegada`). Es una **fila nueva append-only** (`transferencia_acta_firma_excepcion_revocacion`, migración `061`): la constancia original y su Object-Lock **no se tocan**, y el asiento `archive.acta_firma_inobtenible_revocada` (con la huella del fundamento de la revocación) va en la misma transacción. Desde ese momento la excepción **deja de contar**: el trigger de exclusión mutua solo considera excepciones no revocadas, la parte puede volver a firmar (`firmar-personal`) y `completitud` la trata como no cubierta. `404 excepcion_not_found` si el rol no tiene excepción; `409 excepcion_ya_revocada`. **La revocación es prueba (FIRMA-2)**: `causa ∈ {error_de_declaracion, parte_puede_firmar, otro}` **obligatoria** (`422` si falta o no es del vocabulario; viaja dentro del XML firmado); XML en C14N exclusiva (`urn:orpycamcp:acta-firma-excepcion-revocacion:v3`) ligado a la huella de la constancia que revoca, con el **sello institucional** (sin sello: `503` y **no queda ni fila ni asiento**) y la **firma personal de quien revoca si tiene certificado de firma activo** (`422 firma_personal_revocante_requerida` sin `totp_code`); exige además `PERM_FIRMA` ≥ 2 (`403 revocante_sin_permiso_de_firma`). Sus artefactos entran al WORM (tipos `acta_firma_revocacion[_personal]_<rol>`, storage migración `020`, con reconciliación y renovación CT propias: `transferencia_acta_firma_excepcion_revocacion_worm`). Tras revocar y proteger se puede **declarar de nuevo** (ver la cadena arriba). La respuesta es la misma constancia con `revocada=true`.
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/fundamento/verificacion` → `FundamentoVerificacion` `{excepcion_id, verificable, motivo, algoritmo, columna, xml, asiento, revocacion_columna, revocacion_xml, revocacion_asiento, coincide}` — **verificación del fundamento** (FIRMA-2; `USUA_PERM_ADMIN` nivel ≥ 3, no-read-up `404`): recalcula **en local** el compromiso `SHA-256(sal || texto)` del fundamento guardado y lo coteja contra **tres copias independientes** (la columna, el XML firmado y el asiento de auditoría; y lo mismo con la revocación). Una copia ausente o no comprobable (`null`) no cuenta como coincidencia; una constancia anterior a la v3 es `verificable=false` (`constancia_sin_compromiso_salado`). No devuelve el texto ni la sal. **Deja el asiento de lectura `transferencia.acta_fundamento_leido`** (también lo dejan `GET /acta` y `GET /acta/verificacion` cuando sirven el fundamento en claro; sin texto ni sal; sin asiento no hay lectura).
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/sobre?acto=constancia|revocacion` → `SobreFundamentoResponse` `{excepcion_id, acto, sobre_sha256, xades_level, worm_protegido, xml_firmado}` — **sobre de revelación del fundamento** (FIRMA-2 ronda 3, migración archive `064`): el XML `urn:orpycamcp:acta-firma-excepcion-sobre:v1` `{id, acto, texto, salHex, compromiso, algoritmo}` sellado con el sello institucional y guardado bajo Object-Lock como objeto propio (retención alineada al ancla de la cadena). **Único camino de lectura**: `USUA_PERM_ADMIN` nivel ≥ 3, no-read-up `404` (y `404 sobre_not_found` si el acto es anterior a la `064`), `503 sobre_no_disponible` si el almacén no responde, y el asiento `transferencia.acta_fundamento_leido` con `via="sobre"` se escribe **antes** de servirlo. Nunca por OAI-PMH/EAD/CMIS ni la verificación pública. Las vistas de la constancia (`GET /acta`, `/acta/verificacion`, declarar, revocar) añaden solo metadatos: `sobre`, `sobre_sha256`, `sobre_xades_level`, `worm_sobre_protegido` y `revocacion_sobre*` (equivalentes de la revocación). La verificación del fundamento añade la cuarta copia `sobre` / `revocacion_sobre`. Declarar/revocar sellan el sobre en el mismo flujo: sin sello, `503 sellado_sobre_no_obtenido` y nada escrito. `.../worm/reintentar` también repone el artefacto `sobre`. `POST .../revocar` ya **no devuelve** `fundamento` ni `fundamento_sal` de la constancia original (`null`; la vista completa está en `GET /acta`, que asienta la lectura).
- `POST /{id}/paquete-entrega` → `application/zip` — **paquete de entrega de la transferencia secundaria** (B4, ADR-030 §5): `USUA_PERM_ADMIN` nivel ≥ 3, no-read-up con `404` neutro (el clearance va en el `WHERE` del expediente), `409 paquete_solo_secundaria_recibida` si no es secundaria o no está recibida, `409 componentes_sin_object_lock` (`componentes`: rutas sin Object-Lock confirmado, o `aip`; `componentes_clasificados`: recuento de las `clasificado/...` sin enumerarlas; fail-closed, no se descarga nada), `409 componente_integridad_discrepante` (acta, sello o componente que no reproduce su huella de captura; deja el asiento `transferencia.paquete_entrega_incidente`) y `503 componente_no_disponible`. También lleva `indice/indice-electronico.xades.xml` y la nota referencia el AIP (`<aip>`: bucket, clave y huella; no viaja en el ZIP). Contenido: `acta/acta-fuid.xml` (acta congelada, tal cual), `acta/acta-fuid.xades.xml`, `acta/firma-personal-<rol>.xml`, `clasificado/constancias|revocaciones/<rol>-<n>.xml` (cadena completa, también las revocadas, más `-personal.xml` si hay firma personal), `clasificado/sobres/<rol>-<n>-<acto>.xml`, `nota-entrega.xml` (`urn:orpycamcp:nota-entrega-transferencia:v1`: referencia `acta_fuid_sha256`, y por componente `ruta`, `sha256`, `acto`, `rol`, `ordinal`, `clasificacion="clasificada"`, `fundamento="Ley 1712/2014 art. 18 lit. a"`, `acceso="restringido a la entidad receptora"`; `sobre="ausente"` en constancias v2/legadas) y `checksums.txt`. Los asientos `transferencia.acta_fundamento_leido` (`via="paquete"`, uno por constancia cuyo sobre viaja) y `transferencia.paquete_entrega_generado` (`nota_sha256` y huellas) se escriben en una transacción **antes** del primer byte: sin asiento no hay paquete. Nunca por OAI-PMH/EAD/CMIS.
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/fundamento?acto=constancia|revocacion` → `FundamentoLectura` `{excepcion_id, acto, fundamento}` — **lectura a petición del texto del fundamento** (FIRMA-2 ronda 3; `USUA_PERM_ADMIN` nivel ≥ 3, no-read-up `404`, `404 fundamento_not_found` si no existe): deja el asiento `transferencia.acta_fundamento_leido` con `via="lectura"` **antes** de servir (fail-closed). **Cambio de contrato**: `GET /acta` y `GET /acta/verificacion` ya **no** devuelven `fundamento`, `fundamento_sal`, `revocacion_fundamento` ni `revocacion_fundamento_sal` (siempre `null`) y por tanto ya no asientan lectura al visitar la pantalla. **La sal no se devuelve por ninguna vía de lectura**: solo viaja dentro del sobre sellado. La verificación del fundamento no cambia.
- `POST /{id}/acta/firma-inobtenible/{excepcion_id}/worm/reintentar` y `POST /{id}/acta/firma-inobtenible/{excepcion_id}/revocacion/worm/reintentar` → `WormReintentoResponse` `{excepcion_id, acto, repuestos, artefactos}` — **salida de `agotado`** del Object-Lock de la constancia / de su revocación (ronda de auditoría A4; `USUA_PERM_ADMIN` nivel ≥ 3, no-read-up `404`): repone a pendiente los artefactos agotados (protección o renovación CT) con el asiento `archive.acta_firma_{excepcion|revocacion}_worm_reintento_manual` en la misma transacción, reintenta la protección y devuelve el estado real de cada artefacto (`protegido`/`pendiente`/`agotado`/`omitido`). `404 excepcion_not_found`/`revocacion_not_found`. Con la preservación apagada seguirá fallando: es lo correcto.

#### Archivo físico (E17, ADR-017)

> **Control de acceso (D-08):** todos los endpoints requieren `USUA_PERM_EXPEDIENTE`. Sin él: `403 {"error": "forbidden"}`. `GET /expedientes/{id}/unidades` también lo requiere; el frontend lo llama via `Promise.allSettled` y trata el 403 como error de faceta sin bloquear la vista.

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — topología recursiva (sede→…→gaveta); `codigo` único por tenant; `ruta` derivada (`SED-001 / DEP-001 / EST-003`), resuelta para **todas** las filas del listado con un CTE, no una consulta por fila. Filtros `?parent_id=&activo=&solo_raiz=`.
  > **`solo_raiz` no equivale a omitir `parent_id`.** Sin `parent_id` el listado **no filtra por padre** —lo natural en una API de listado, y lo que necesita quien puebla un desplegable con todo el archivo—; `solo_raiz=true` filtra `parent_id IS NULL`, que es lo que necesita quien pinta el primer nivel de un árbol. Confundirlas hacía que la pantalla listara cada gaveta también como raíz. Pedir las dos juntas responde **422 `filtros_incompatibles`**: es una petición sin significado, y elegir una mitad en silencio sería ignorar la otra.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — unidades de conservación (caja|carpeta|…); al indicar `ubicacion_id` se deriva la **signatura topográfica** (`DEP01-E05-C0124`, única). Filtros `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — vincular expediente a unidad (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — qué contiene la unidad; **filtrado por fila** por clearance (RF-SEG-08, no-read-up): enumerar el contenido de una unidad revela la _existencia_ de sus expedientes clasificados, así que un no-lector no ve los de `nivel_seguridad >` su _clearance_ (Ley 1712/2014 art. 19; _fail-closed_ a PUBLICA, `403` sin `X-User-Id`). `GET /api/v1/expedientes/{id}/unidades` — dónde está el expediente (signatura + `tipo`/`codigo` de la unidad + rango de folios; la signatura dice en qué entrepaño mirar, el código dice qué caja sacar); **sin filtro de clearance a propósito**: se consulta por un `expediente_id` ya conocido y solo devuelve ubicación física (signatura/folios, no asunto/serie) — es gestión de custodia ortogonal al clearance de lectura (mismo criterio que la transferencia).
- **Préstamos (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 si ya prestada), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` se deriva de `fecha_devolucion_esperada` con **`< CURRENT_DATE`**: el préstamo que vence hoy todavía está en plazo. (La TRD usa la convención contraria, `<=`; unificarlas es una decisión de producto abierta.) `GET /prestamos` **pagina** (`?page=1&size=20`, máx. 100) y emite `X-Total-Count` — `prestado` y `vencido` están acotados por la operación, pero `devuelto` es el histórico y crece con cada préstamo que se cierra; sin paginación el único recuento posible era `items.length`, que es el tamaño de la ventana haciéndose pasar por el de la consulta. `GET /unidades/{id}/prestamos` **también pagina** (T16 / AREA-07-19: `?page=1&size=20`, máx. 100, `X-Total-Count` con el total de esa unidad; antes devolvía todo su histórico con el argumento de que «la cota la pone el objeto», una cota supuesta y no impuesta). El BFF del frontend pide `size=100` por omisión y conserva el total. **Quién prestó/devolvió (T6 / AREA-02-08)**: `movimiento_fisico.performed_by` guarda el **UUID** de `X-User-Id`, no el login de `X-Username` (Keycloak permite renombrar a un usuario y el nombre es reasignable; la traza de custodia atribuida a un nombre deja de ser reconstruible). Igual el `generado_por` de `POST /expedientes/{id}/indice/rebuild`. Las filas anteriores conservan el texto que tenían. Las cuatro rutas devuelven `unidad_codigo` y `unidad_signatura` además del `unidad_id`: un préstamo se consulta para saber **qué falta del estante**, y en un depósito nadie identifica una caja por su UUID.
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

- `POST/GET/PATCH /api/v1/trd` — series y **subseries** documentales. Campos: `code`, `name`, `parent_id` (subserie), `archivo_gestion_years`/`archivo_central_years` (retención en dos fases), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`, `estado`, `valid_to`, `acto_administrativo`, `acta_comite`, `fecha_aprobacion_comite`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`, `fecha_publicacion`, `supersede_a`, `created_by`. `POST` crea siempre **`version=1` en `borrador`** (`estado` no es settable por el cliente). **Dos juegos de retención (AREA-06-07)**: el cálculo del calendario usa las dos fases (`archivo_gestion_years`/`archivo_central_years`); `retention_years`/`total_retention` son el juego **legado** y la respuesta los **deriva** de las fases cuando están pobladas (`retention_years` = gestión, `total_retention` = gestión + central), así que nunca quedan en desacuerdo. `PATCH` acepta ahora las dos fases; un `PATCH` solo del juego legado se traduce a ellas como en el alta, y el `UPDATE` reescribe las columnas legadas derivadas. Las fases son sustantivas: sobre una versión no `borrador` dan `409 trd_version_inmutable`. `PATCH` es **append-only** (D1): solo aplicable sobre `borrador` — cualquier otro estado responde `409 trd_version_inmutable`, salvo que el único campo enviado sea `pdfa_profile` (política de preservación técnica, editable en cualquier estado). `is_active` ya no se acepta en `PATCH` (la baja de una versión es `derogar`, abajo).
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

Vincula varios radicados de una vez `{radicados:[{radicado_id, tracking_number, content_hash?, formato?, tamano_bytes?, folios?, tipologia?, dependencia_productora_codigo?, dependencia_productora_nombre?}]}` (hasta 500; idempotente, omite los ya vinculados — a los ya vinculados no les actualiza metadatos); regenera el índice una sola vez. Los campos RT-15 (`formato`/`tamano_bytes`/`folios`) y la **dependencia productora** (código+nombre, v4) son opcionales pero **código y nombre deben ir juntos o ninguno** (validación Pydantic `422`, espejo de `RadicadoLink`). Devuelve `{vinculados, omitidos, total}`. `409` si el expediente no está `open` (para un llamante sin clearance de lectura sobre un expediente clasificado, `vinculados`/`omitidos` llegan `null` y el `409` se sustituye por el ack acotado — ver la nota de control de acceso arriba).

#### Ciclo de vida del expediente (E02)

- `POST /api/v1/expedientes/{id}/close` — cierra el expediente (`open→closed`), materializa la disposición TRD en `disposition` y **genera+firma (XAdES-B) la versión final del índice electrónico** vía signature-service (E06 Inc.1). El sellado es **best-effort y no bloquea el cierre**: si la firma se obtiene deja `expediente_indice.estado='firmado'`; si falla (sello no aprovisionado, servicio inalcanzable) deja **`estado='pendiente_firma'`** y emite el audit `archive.indice_firma_pendiente`. El audit de cierre incluye `indice_estado`/`firma_id`. **Respuesta**: `ExpedienteDisposicionResult` **acotado** (`id`, `code`, `status`, `disposition`, `closed_at`, `transferred_at`) — **no** el expediente completo: la disposición es ortogonal al clearance de lectura (RF-SEG-08 Opción B), así que la respuesta no expone `metadata`/`description`/`radicados`/`opened_by`, y un usuario con autoridad de disposición (`crud≥3`) pero sin clearance sobre un CLASIFICADA recibe **200** (antes: 404 espurio pese a cerrar con éxito).
- `POST /api/v1/expedientes/{id}/indice/firmar` — **reintenta** el sellado XAdES-B de un índice `pendiente_firma` (E06 Inc.1). Idempotente: `409` si ya `firmado`, `503` si el sellado sigue sin obtenerse. Requiere `USUA_PERM_EXPEDIENTE` **+ `PERM_FIRMA`** y `X-User-Id` (400 si el UUID es malformado).
- `POST /api/v1/expedientes/{id}/transfer` — transfiere (`closed→transferred`). `409` si la transición es inválida **o si el índice no está `firmado`** (un índice `pendiente_firma` bloquea la transferencia — Acuerdo AGN 001/2024 art. 4.3.2.4). El mismo bloqueo aplica en el `PATCH` genérico con `status=transferred` y en la recepción de transferencias (E12). **Respuesta**: `ExpedienteDisposicionResult` acotado (igual que `/close`), no el expediente completo.
- `GET /api/v1/expedientes/{id}/eventos` — **hoja de ruta append-only del expediente** (E02 §9, RF-EXP-09): lista cronológica de `{id, accion, actor, comentario, detalles, ts}` con `accion` ∈ `abierto|radicado_vinculado|radicado_excluido|cerrado|transferido|...`. **Control de acceso (Canal B / RF-SEG-08):** visible a quien pueda **leer** el expediente, acotada por su clearance (no read-up); no requiere admin. Distinta de `audit_log` (auditoría forense) y de `/unidades/{id}/movimientos` (movimientos físicos, E17).
- `GET /api/v1/expedientes/{id}/foliado` — foliado: radicados ordenados con `folio` secuencial (`{expediente_id, code, total_folios, items[]}`). (El **índice electrónico** normativo es E15, abajo.)

#### Índice electrónico (E15, Acuerdo AGN 001/2024)

> **Perfil v4 "conforme completo" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md); XSD `schemas/indice-v4.xsd`):** los índices nuevos se generan en `urn:orpycamcp:indice:v4`, que **cierra los 4 residuales de v3**. **Por documento**: metadatos RT-15 (`<formato>`/`<tamano>`/`<folios>`/`<fechaIncorporacion>`), **`<orden>` estable** (inmutable), **`<dependenciaProductora codigo="N">`** (aportada al vincular; se **omite** en filas legacy sin dato) y **`<fechaDeclaracion>`** (= `added_at`, declaración ≡ incorporación). **En cabecera**: `<contexto>` (`fechaPrimerUso` + serie/subserie por nombre), **`<listaControlAcceso>`** con `<nivelSeguridad>` + **`<politicaAcceso modelo="no-read-up" requiere="USUA_PERM_EXPEDIENTE" clearanceMinimo="N">`** (política estable/portable — la **matriz RBAC viva NO se incrusta**, se consulta por endpoint), **`<pistaAuditoria registro="public.audit_log" objetoRef cadenaVerificable>`** (atesta la pista, **no** incrusta entradas vivas) y **`<conformidad perfil="v4" productoraPresente pistaAccesoDesde>`** (marcador per-instancia; `productoraPresente="false"` sobre conjunto vacío). El **content_hash v4 canonicaliza el árbol completo** (v3 solo `<documentos>`). La **exclusión** de un radicado es **soft-delete** (`<documento excluido="true">` con ordinal + `<fechaExclusion>`/`<causalExclusion>` → **hueco trazable**, AGN 001/2024 art. 4.3.2.3). Los índices v1/v2/v3/v4 **ya firmados quedan inmutables** (`verify` agnóstico por versión). **Label**: "conforme completo — **aplicabilidad prospectiva**" (instancias legacy pueden omitir productora y su pista de acceso inicia en la instrumentación; cada índice lo auto-declara en `<conformidad>` — RF-FIR-15, no sobre-declarar). **Todas** las lecturas del índice exigen `USUA_PERM_EXPEDIENTE` + clearance (no-read-up, RF-SEG-08) y **dejan asiento de acceso** en `audit_log` (`archive.indice_consultado`, solo actor real + canal `api|ui|mcp`). **AREA-07-01**: el XML se valida contra su XSD (lxml, `app/services/index_xsd.py`, esquema elegido por el espacio de nombres v2/v3/v4) **antes de pedir el sello** —al cerrar, en el reintento manual y en la reconciliación—: si no valida, el índice queda `pendiente_firma` con motivo `xsd_invalido` (y el primer error en el asiento) y **no se firma**; un espacio de nombres sin XSD no se juzga. Cuando el sellado **se agota** (`reconcile_agotado`) se avisa por correo a quien puede repararlo (`USUA_PERM_EXPEDIENTE` + `PERM_FIRMA` y clearance del expediente; el correo no lleva códigos ni asuntos), además del asiento `archive.indice_reconcile_agotado` (AREA-07-03). Los vínculos legados de expedientes **abiertos** sin productora/formato/tamaño se completan con lo que sabe document-service (solo columnas NULL, un intento por vínculo, asiento `archive.vinculo_origen_completado`; los cerrados son probatorios y no se tocan).

- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — índice vigente o por versión: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`. Cada item incluye `formato`, `tamano_bytes`, `folios`, `fecha_incorporacion` (nulos si no se declararon al vincular).
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — cuerpo XML (`application/xml`, namespace `urn:orpycamcp:indice:v4` en índices nuevos; `v1`/`v2`/`v3` en los ya firmados).
- `GET /api/v1/expedientes/{id}/export.zip` — **exportación del expediente a ZIP** (E02/export, Ley 594/2000 art. 19). Copia de CONSULTA/ENTREGA (distinta del AIP de preservación E10 y de la transferencia FUID E12). El ZIP contiene: `indice-electronico.xml` (E15, si está firmado — best-effort), `manifiesto.csv` (radicados **vinculados y excluidos** —estos con su causal, sin bytes—, con `orden_documental`/`folios`/`formato`/`dependencia_productora`/`fecha`), `documentos/{tracking}/{idx}-{filename}` (bytes de los anexos), `LEEME.txt` (leyenda de alcance) y `checksums.txt` (SHA-256 por miembro, para validar integridad). Requiere `USUA_PERM_EXPEDIENTE`. **No-read-up POR-RADICADO** (RF-SEG-08): `404` si el expediente supera el clearance del llamante, y **los radicados individuales por encima del clearance se omiten por completo** (ni su existencia se enumera en el manifiesto) — no basta el gate a nivel de expediente. archive orquesta; el ensamblado de bytes lo hace storage-service (endpoint interno D-02, inalcanzable desde el gateway) sin persistir nada; `arcname` saneado anti zip-slip. La exportación deja una entrada de auditoría agregada (`archive.expediente_exportado`, RF-BUS-10). El ZIP se arma en un temporal en disco (sin tope de memoria; límite `EXPORT_MAX_BYTES`, 4 GiB) y se sirve por trozos con `Content-Length`; errores previos al primer byte: `413 export_demasiado_grande`, `507 espacio_insuficiente`, `502 export_no_disponible`. _Deuda: PDF combinado._
- `GET /api/v1/expedientes/{id}/historial-acceso` (`?page&size`, E15 v4 R3) — **pista de acceso** del expediente: lee `public.audit_log` (solo lectura, filtro `tenant_slug` explícito) filtrado por `object_ref = code` y `object_type ∈ (expediente, expediente_indice)`, orden `ts DESC`, con `X-Total-Count`. Devuelve `{ts, actor, canal, action, ...}`. Requiere `USUA_PERM_EXPEDIENTE` + no-read-up (**404** si el expediente supera el clearance del llamante, no 403 — no filtra existencia). **`actor_nombre`** acompaña al `actor` desde 2026-09: el asiento guarda el identificador interno —y así debe seguir, porque un `username` cambia y un asiento no se toca nunca— pero un historial que contesta con un UUID no contesta quién. Se resuelve con `LEFT JOIN` por TEXTO contra `auth_users`: el actor no siempre es una persona (hay asientos del propio sistema) y castear a `uuid` haría fallar la consulta entera por una fila que ni se preguntaba; sin nombre resoluble viaja `null` y la pantalla cae al identificador. Gemelo del de `documents/{id}/historial-acceso`, y un guardián en `shared/tests/` exige que ambos lo resuelvan.
- `GET /api/v1/expedientes/{id}/roles-autorizados` (E15 v4 R4) — **snapshot vivo** (no firmado) de la matriz de acceso: grupos que tienen `USUA_PERM_EXPEDIENTE` (`crud≥1`) **y** `role_clearance.max_level ≥ nivel_seguridad` del expediente, como `[{grupo, clearanceMax, crud}]` ordenado por nombre. Mismo gate (`USUA_PERM_EXPEDIENTE` + no-read-up). Es el dato que el índice firmado deliberadamente **no** incrusta (evita churn del artefacto probatorio y fuga de topología IAM en transferencias AGN).
- `GET /api/v1/expedientes/{id}/indice/versions` — historial de versiones (append-only) con `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifica integridad: contrasta el `valor_huella` del índice contra el hash actual de cada documento **y** re-hashea el XML almacenado contra `xml_sha256` (`huella_indice_ok` — detecta alteración del propio XML del índice, incluso no firmado). Con firma XAdES-B descarga el XML firmado del almacén de objetos y lo verifica contra signature-service — el bloque `firma` incluye `{presente, valida, nivel_conformidad, acreditado, motivo, advertencias}` (`motivo='signed_xml_unavailable'` → `valida=false`; `advertencias` propaga las de signature `/verify`, vacío si no hay). `valido` es la conjunción de fixity por documento + `huella_indice_ok` + firma (si presente). Requiere `USUA_PERM_EXPEDIENTE`.
- `GET /api/v1/expedientes/indices/pendientes-firma` — lista operacional (paginada, `X-Total-Count`) de los índices en estado `pendiente_firma` de expedientes `closed`/`transferred`, con sus contadores de reconciliación (`reconcile_intentos`, `reconcile_agotado`, `reconcile_ultimo_error`, versión, `expediente_code`) — visibilidad directa para el archivista de los índices que aún no se sellaron, **incluidos los `reconcile_agotado=true`** que exigen intervención humana (montar el sello + retry manual). **No-read-up**: filtra por `nivel_seguridad <= clearance` del llamante (en `COUNT` y filas), un clearance bajo no ve índices de expedientes clasificados. Requiere `USUA_PERM_EXPEDIENTE` (E15/E06 Inc.8).
- `POST /api/v1/expedientes/{id}/indice/firmar` — reintento **manual** del sellado XAdES del índice `pendiente_firma` (exige `USUA_PERM_EXPEDIENTE`+`PERM_FIRMA`, atribuido al humano). `409 indice_ya_firmado` si ya está firmado (idempotente), `503 index_signing_failed` si sigue sin poder sellarse. Un retry manual **re-arma** el backoff de reconciliación (`reconcile_agotado`→false) para que el job automático reanude tras corregir la causa raíz. El asiento `archive.indice_firmado` lleva `origen="manual"`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenera (nueva versión si cambió el conjunto o los metadatos; `{unchanged:true}` si idéntico). **`409 expediente_not_open`** si el expediente no está `open`. Se crea la **versión 0** automáticamente al crear el expediente. Requiere `USUA_PERM_EXPEDIENTE`. Cada regeneración deja un asiento `archive.indice_regenerated` (con `xsd_version`) en `audit_log`.
- Al vincular un radicado (`POST /api/v1/expedientes/{id}/radicados`) se pueden **declarar** (el servidor los resuelve y coteja: ver el bullet siguiente) `content_hash` (SHA-256, `valor_huella`), los metadatos RT-15 **`formato`** (MIME), **`tamano_bytes`** y **`folios`** (opcionales, `ge=0`) y, en **v4**, la **`dependencia_productora_codigo`** (INTEGER) + **`dependencia_productora_nombre`** (texto) — opcionales pero **juntos o ninguno** (`422` el caso parcial). Desde 2026-09 también **`tipologia`** (la `doc_class` del radicado, ≤100): el elemento `<tipologia>` del índice es **obligatorio en el XSD** y de tipo `xs:string` —que admite cadena vacía—, así que hasta ahora el índice se generaba, validaba contra el perfil v4 y se firmaba **declarando tipología vacía para todos sus documentos**; `IndexService._items_from` construía todos los demás campos y omitía este. Viaja con el vínculo por la misma razón que sus hermanas: archive-service **no lee la tabla `radicados`** (cero consultas, frontera intacta) y resolverla al generar metería una llamada de red dentro de un artefacto que se firma. El buscador (`SearchHit`) devuelve `doc_class` justamente para que quien vincula pueda aportarla. `NULL` sigue significando «no declarada».
- **El vínculo no sella lo que escriba quien vincula (2026-10, T6 / AREA-07-02 y AREA-06-08).** El índice se firma con XAdES, y hasta aquí «lo explícito manda»: número de radicado, tipología, dependencia productora, formato, tamaño y huella se aceptaban tal cual y ni siquiera se comprobaba que el radicado existiera (el cliente Keycloak del frontend es público —PKCE— y el MCP expone campos libres, así que el allowlist del BFF no protege el backend). Ahora archive-service **resuelve todo en el servidor** (document-service `POST /internal/documentos/metadata` + storage) y **coteja** lo declarado: radicado desconocido → `404 radicado_no_encontrado`; un valor distinto del resuelto → `422 metadato_no_coincide` con `desacuerdos: {radicado_id: [campos]}` (nombra los campos, nunca el valor resuelto: no es un oráculo de números de radicado) y asiento `archive.radicado_vinculo_denegado`; document-service sin responder (o sin `tracking_number` en la respuesta) → `503 document_service_no_disponible` —fail-closed para existencia y número—. Lo persistido es SIEMPRE lo resuelto; solo `folios` es del declarante. Mismo cotejo en `…/radicados/batch`, todo-o-nada. **No-read-up**: la resolución se hace con el clearance del llamante; un radicado por encima de su nivel da el mismo `404 radicado_no_encontrado` que uno inexistente (sin `desacuerdos`), y uno sin `doc_class` da `422 radicado_sin_tipificar` (la tipología es obligatoria en el índice y no se declara al vincular). `POST /unidades/{id}/prestamos` responde `403 clearance_insuficiente` si la unidad contiene un expediente por encima del clearance de quien presta (gate en el `WHERE` del `INSERT`). **Límite declarado**: si un radicado se retipifica DESPUÉS de vincularlo (`PATCH /documents/{id}` con `doc_class`), el índice ya generado conserva la tipología del momento del vínculo; propagar el cambio (evento `document.radicado.retyped` + marca «requiere revisión TRD») sigue abierto.

  **Desde 2026-09-09 la `dependencia_productora` y el `formato`/`tamano_bytes` NO se esperan del cliente**: los resuelve **el servidor** al vincular, preguntándoselos a document-service por su endpoint interno de metadata (`origin_dept`/`origin_dept_code` del radicado, y el MIME y el tamaño de su **primer anexo** por fecha). El motivo es el mismo por el que la huella tampoco se le pide al cliente: alimentan un índice que se **firma**, y quien vincula suele ser un archivista central y no la oficina que produjo el documento — el dato que aportaría sería el equivocado por construcción. Los campos siguen en el contrato y **lo explícito manda** (importación con metadatos propios, E11 INT-05). La resolución es de **mejor esfuerzo**: si document-service no responde, el vínculo se hace igual y los campos quedan vacíos. Se persiste un snapshot inmutable por versión del índice, de modo que `GET /indice?version=N` muestra la productora tal como estaba al generarse esa versión. Vincular/excluir regenera el índice automáticamente; sobre un expediente no `open` devuelve `409`.

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

Proveedor **enchufable** (`app/providers/`): `nativa` (HMAC hash+identidad, default para todo objeto) y **`xades_local`** (XAdES-B enveloped real vía `signxml`, activo solo para el **índice electrónico**). Selección: `xades_local` cuando `objeto_tipo=="indice"` **y** `SIGNER_PROVIDER=="xades_local"` (default; kill-switch `SIGNER_PROVIDER=nativa`); cualquier otro caso → `nativa`. Ver [ADR-016 addendum Inc.1](adr/ADR-016-firma-electronica.md#addendum-incremento-1-2026-07-02-xades-b-del-indice-electronico-sello-institucional).

- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version?}` — firma el payload y persiste la firma. Con `objeto_tipo="indice"` produce **XAdES-T** (XAdES-B con el sello institucional del tenant **+ sello de tiempo RFC 3161** de una TSA, E06 Inc.4) y, si `SIGNER_LT_ENABLED=true` **y** el sello del tenant trae material de CA (`ca.crt`+`crl.der`), escala a **XAdES-LTA** (E06 Inc.5): añade `xades:CertificateValues` (solo la CA) + `xades:RevocationValues` (CRL) + `xadesv141:ArchiveTimeStamp`. Con `SIGNER_OCSP_ENABLED=true` (default `false`) **y** el par de responder OCSP montado (`ocsp.crt`+`ocsp.key`, per-tenant junto al sello), añade además una respuesta **OCSP stapled (RFC 6960)** como `xades:OCSPValues/EncapsulatedOCSPValue` **hermana del CRL** dentro del mismo `RevocationValues` (E06 Inc.7) — firmada por un **responder delegado** emitido offline por la CA del tenant, con `certStatus` derivado de la CRL, y queda cubierta por el `ArchiveTimeStamp`. Exige `PERM_FIRMA` con **`crud≥2`** + `X-User-Id` (el firmante **nunca** sale del cuerpo, RF-FIR-11). Para los tipos de cadena (`radicado|expediente|borrador`) el `objeto_id` es obligatorio y el objeto debe existir y ser visible por clearance (en el `WHERE`); si no, `404 objeto_no_encontrado` neutro. **El sello institucional (`objeto_tipo` ∈ `indice|acta_transferencia|acta_eliminacion`) exige además `X-Internal-Token` (AREA-04-07)**: lo envía archive-service junto a la identidad firmada; un usuario con solo `PERM_FIRMA` recibe `401/403` (antes podía estampar el sello de la entidad sobre un XML arbitrario). `objeto_tipo` es un **conjunto cerrado** (`indice|acta_transferencia|acta_eliminacion|radicado|expediente|borrador`): cualquier otro → `422`. Devuelve `{firma_id, payload_sha256, provider, minio_key_firmado?, xades_level?, nivel_conformidad?, tipo_firma?, certificado_fingerprint?, acreditado, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ...}`. `xades_level` ∈ `XAdES-B|XAdES-T|XAdES-LT|XAdES-LTA` según lo alcanzado (nunca lo intencionado; OCSP **no** cambia el nivel, enriquece la evidencia de revocación); **sin material CA la firma se queda en XAdES-T** y deja audit `firma.lt_downgrade_no_ca`; **con OCSP habilitado pero sin par de responder** deja audit `firma.ocsp_downgrade_no_responder` (se queda en LT/LTA con CRL sola). `acreditado` sale de lo **persistido al firmar** (`nivel_conformidad`, que decide el sello montado: solo un sello con `acreditacion.json` `{"acreditado": true}` en un volumen de solo lectura da `xades_*_acreditado`); con la CA/TSA/responder OCSP de dev es `false` (no ONAC — material de validación **verificable, no acreditado**, RF-FIR-15; `nivel_conformidad` ∈ `xades_b|xades_lt|xades_lta_no_acreditado`, revocación de dev marcada con `revocation_provenance="dev"`). **`payload_sha256` es el SHA-256 de los BYTES que se firmaron, no del documento** (AREA-05-07): para `xades_personal` son los del envoltorio `<acto xmlns="urn:orpyca:firma-personal" …>`, para la firma `nativa` de un borrador es el hash del texto hexadecimal de la huella, y para `radicado`/`expediente` el digest de `"{objeto_tipo}:{objeto_id}"`; **solo es comparable entre firmas del mismo `provider` y no sirve para cotejar un PDF en la mano** (la huella del documento es `firma_solicitud.payload_sha256`). Desde la migración `020` la firma `nativa` se produce con **`key_version=1`**: clave HKDF-SHA256 derivada por **entidad** (`signature_secret` sigue siendo el secreto raíz, no se rota) y mensaje que ata `tenant|objeto_tipo|objeto_id` (AREA-04-11) — una fila copiada a otro schema deja de verificar; las firmas anteriores (`key_version=0`) siguen verificando con el esquema original. `503 signing_seal_unavailable` si falta el sello; **`503 timestamp_authority_unavailable`** si la TSA no está disponible; **`503 lt_material_required`** si `SIGNER_LT_REQUIRE=true` y falta material CA; **`503 ocsp_material_required`** si `SIGNER_OCSP_REQUIRE=true` y no se produjo respuesta OCSP (falta material de CA o de responder); `503 signed_index_upload_failed` si storage rechaza la subida.
- `POST /api/v1/signature/sign-personal` `{payload_xml, objeto_tipo, objeto_id?, totp_code, firmante_id?}` — **firma personal PKI de un payload XML arbitrario** con el certificado del actor autenticado (E17/F4 Inc.1; primitivo `personal_signing.sign_personal_xml` reutilizado por la cadena de radicados y por la firma del acta de transferencia). Autenticado como **usuario** (`get_actor` + `PERM_FIRMA`, `X-User-Id`/JWT — **no** `X-Internal-Token`: lleva identidad + TOTP del firmante real, a diferencia de `/internal/sign-indice`). **ASSERT `firmante_id == user(JWT)`**: si el body trae `firmante_id` y difiere del actor derivado del token → `403 firmante_no_coincide` (no suplantable por el body; la fila persistida usa siempre el actor del JWT). Arma el XAdES con clave RSA efímera + el cert público real, pide a auth-service que firme el digest de `SignedInfo` **consumiendo el TOTP atómicamente** (split firma-remota), y sube el XML firmado a el almacén de objetos con nombre determinista que incluye el `firma_id` (patrón H-H, sin sobrescritura por carrera). Devuelve el `FirmaResponse` estándar (`firma_id`, `minio_key_firmado`, `xades_level`, `provider="xades_personal"`, `acreditado` **siempre `false`**, `nivel_conformidad='xades_personal_no_acreditado'`). **El gate no-read-up ya no depende de `objeto_tipo`**: `objeto_tipo` es texto libre aquí (tiene que serlo — por esta ruta pasan las actas institucionales), y mientras el gate colgaba de `== "radicado"`, declarar cualquier otro valor sobre el identificador de un radicado CLASIFICADA lo evaluaba contra nivel 1 y lo saltaba. Ahora el nivel se resuelve por el `objeto_id` contra `radicados`/`expedientes`, sea cual sea el tipo declarado; si no resuelve, es un objeto sin nivel propio (acta, borrador) y queda en PUBLICA, y para esos el no-read-up lo sigue aplicando **el servicio mediador** (archive) **antes** de llamar — verificado que una llamada directa no es bypass (solo firma el payload que el propio caller aporta, no inyecta en las tablas de dominio). **`403 identidad_incoherente`** si el dueño del bearer reenviado (que es de quien es la credencial personal y el TOTP) no es el actor `X-User-Id` al que se atribuye la firma (AREA-04-05): se corta **antes** de pedir ningún factor y deja asiento `firma.identidad_incoherente`. `4xx` si el TOTP es inválido; `503` si el canal de auth cae.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifica. **Firmas `xades_local`**: `payload_xml` debe ser el **XML firmado completo** (el de `minio_key_firmado`); valida criptografía + cobertura de la raíz + vigencia del cert + huella del sello + coincidencia con `signed_xml_sha256`; **XAdES-T** valida además el token RFC 3161 (cubre el `ds:SignatureValue`, firma CMS, cert TSA **atado a `firma.tsa_cert_fingerprint`**); **XAdES-LT/LTA** valida además (fail-closed) la **cadena `seal→CA`** (anclada a la CA **del tenant** vía pinning `ca_fingerprint` — un sello de la CA de otro tenant falla), la **firma y no-revocación de la CRL** (serial del sello no revocado), y el **`ArchiveTimeStamp`** sobre el conjunto cubierto determinista; `crl_next_update` se expone sin hard-fail si caducó (no acreditado). Si la firma trae **`xades:OCSPValues`** (E06 Inc.7), valida además (fail-closed) la respuesta **OCSP (RFC 6960)**: `responseStatus=successful`, responder emitido por la CA del tenant + vigencia, EKU `OCSPSigning` + `id-pkix-ocsp-nocheck`, firma del `BasicOCSPResponse`, **CertID atado al sello** (serial + issuer hashes), `certStatus=good`, y **pinning** del fingerprint del responder contra `firma.ocsp_responder_fingerprint` (sin él → fail-closed); su ausencia es retrocompat (LT válida sobre CRL sola). **Firmas `xades_personal`** (firma personal PKI por-usuario, E06/E17 Inc.1-2): valida cripto + cobertura + vigencia + huella del leaf; si la firma embebe la sub-CA (`xades:CertificateValues`, Inc.2) ancla además la **cadena leaf→sub-CA de usuarios del tenant** con pinning `user_ca_fingerprint` (rama separada de la del sello — no exige CRL embebida); y **consulta la revocación en línea** (estado en BD, sin caché) → `valida=false` (`motivo=certificado_revocado`) si el cert fue revocado. El veredicto se desglosa: `firma_criptograficamente_valida` (cripto+cadena a secas) y `revocacion_status ∈ {vigente, revocado, no_verificable, desconocido}`; `valida = cripto AND revocacion_status=='vigente'` **salvo revocación POSTERIOR a la firma** (AREA-04-13): si el certificado se revocó **después** del instante de la firma (SOLO el `genTime` de una TSA verificada: el `sello_tiempo` del servidor no es fecha cierta) la firma sigue siendo válida (`valida=true`, `revocacion_status="revocado"`) y la respuesta lleva **`revocacion_posterior=true`**; revocada **antes** (o sin `revocado_at` legible) → `valida=false`. Si el motivo de la revocación es `key_compromise` invalida hacia atrás (`valida=false`, `motivo=clave_comprometida`); sin TSA verificada no hay instante cierto: `valida=false`, `motivo=instante_firma_no_cierto`, `revocacion_posterior=null`; un canal de revocación caído → `valida=false`/`motivo=revocacion_no_verificable` pero `firma_criptograficamente_valida=true` (default fail-closed, `verify_personal_revocation_require`). Firmas `xades_personal` de Inc.1 (sin `CertificateValues`) verifican como XAdES-B/T (grandfather) pero **siguen siendo revocables** por identidad del cert. **Firmas `nativa`**: `payload_xml` es el original en claro; se recomputa el HMAC con el esquema de la `key_version` de la fila, y con la entidad de la petición. **Sello de tiempo (XAdES-T, AREA-04-08)**: el `genTime` debe caer dentro de la vigencia del **certificado firmante** (`motivo` interno `gentime_fuera_de_vigencia_firmante` → `sello_tiempo_valido=false`) y, si el token lleva el atributo `signing-certificate-v2` (ESSCertID, RFC 5035), su hash debe coincidir con el certificado de la TSA; su ausencia se rechaza (`tsa_sin_esscertid`) si el `genTime` del sello es **igual o posterior a la fecha de corte** `TSA_ESSCERTID_CORTE` (ISO 8601, sin zona = UTC; **global** —la TSA es infraestructura del despliegue—; default = fecha de despliegue de este cambio, `2026-10-04T00:00:00Z`); **antes del corte** el sello sigue valiendo y la respuesta lo declara en `advertencias` (`tsa_sin_esscertid_anterior_al_corte`: los tokens de la TSA local previos a AREA-04-08 no lo llevan y están embebidos, inmutables, en firmas ya emitidas). Sustituye al booleano `TSA_ESSCERTID_OBLIGATORIO`. **`acreditado`** se deriva de lo persistido al firmar (`nivel_conformidad` ∈ `xades_{b,lt,lta}_acreditado`) y **solo si la criptografía valida** (AREA-05-02); antes era un literal `false`. Devuelve `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?, sello_tiempo_valido?, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ocsp_cert_status?, firma_criptograficamente_valida?, revocacion_status?, revocacion_posterior?, advertencias[]}` (`advertencias: list[str]` es **aditivo** y los servicios que consumen `/verify` deben conservarlo: archive-service lo propaga en su `VerifyResult`) (en `/verify` el campo **autoritativo** del nivel realmente verificado es `xades_level`, derivado del resultado). **No es público** (AREA-04-10): por el gateway exige JWT y el tenant sale del token; la verificación verdaderamente pública va por `/api/v1/public/` con código de verificación (E13). Dentro de la entidad aplica el **no-read-up**: la firma de un radicado/expediente por encima del clearance del llamante da el **mismo `404 firma_not_found`** que un `firma_id` inexistente (el nivel va en el `WHERE` de la consulta); lo mismo en `GET /api/v1/signature/firmas/{firma_id}`. Las llamadas de servicio a servicio sin `X-User-Id` (archive-service verificando el sellado de un índice) no tienen clearance que aplicar. `404` si la firma no existe.
- **Conformidad XMLDSig (ADR-034, B7).** Toda firma XAdES de `sign-xml`/`sign-personal`/cadena declara `ds:Transforms` exc-c14n en **cada** `ds:Reference` (documento, `SignedProperties`, `KeyInfo`), firma el `SignedInfo` ya parcheado y se **autoverifica** antes de sellar el tiempo con un `XAdESVerifier()` por defecto: si no la verifica un validador estándar, `503 firma_no_conforme_xmldsig` y no se persiste nada. `verify` verifica **solo** conforme al estándar —sin atajo de canonicalización—; una firma anterior a ADR-034 (referencias sin `Transforms`) responde `valida=false`, `motivo=firma_invalida: …`. Validado contra EU DSS: criptografía íntegra (`SignatureIntact` y todos los `DataIntact` de referencias y sellos), con `INDETERMINATE/NO_CERTIFICATE_CHAIN_FOUND` como mucho por la CA de desarrollo (sin confianza ni acreditación: `acreditado=false`). El `ArchiveTimeStamp` de XAdES-LTA sella la entrada de EN 319 132-1 §5.5.2 (octetos de cada referencia, `SignedInfo`, `SignatureValue`, `KeyInfo` y propiedades no firmadas previas). **Vigencia (ronda de corrección B7)**: no se firma con un certificado fuera de vigencia —`sign-xml`/`internal/sign-indice` con el sello caducado responden `503 sello_institucional_vencido`; `sign-personal` y la cadena con el certificado personal caducado, `422 certificado_personal_vencido`, antes de que auth-service verifique el TOTP—; `XmldsigNoConformeError` en las rutas personales es también `503 firma_no_conforme_xmldsig` (el TOTP ya está consumido en ese caso). En `verify`, el certificado se juzga a «ahora» salvo en una firma XAdES-LTA cuyo `ArchiveTimeStamp` valida y cuyo certificado caducó después de firmar: se juzga a la fecha del `SignatureTimeStamp` y `advertencias` incluye `certificado_vencido_vigencia_juzgada_al_sello_de_firma`; sin LTA válido sigue siendo `motivo=certificado_fuera_de_vigencia`. `SignatureTimeStamp` y `ArchiveTimeStamp` se verifican con la canonicalización que declaran.
- `GET /api/v1/signature/firmas/{firma_id}` — metadatos de una firma (mismos campos que `sign-xml`, sin `certificado_pem` completo). Público. `404` si no existe.
- _(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/signature/internal/sign-indice` `{payload_xml, objeto_id, version, objeto_tipo?, indice_id?}` — sella un objeto XAdES-elegible **atribuido al principal de sistema `SYSTEM_RECONCILER_ID`** (no a un humano), para los **jobs de reconciliación** de archive-service (índice E15/E06 Inc.8, y acta de transferencia H-H). Autenticado **solo** por `X-Internal-Token` (fail-closed: settings vacío → 503, header ausente → 401, errado → 403); **no** exige `PERM_FIRMA` ni `X-User-Id`. `objeto_tipo` (default `"indice"`) se **valida contra el allow-set `{"indice","acta_transferencia"}`** (`422` fuera de él) → solo esos dos tipos producen el sello institucional del tenant (que NO deriva del actor); **estructuralmente no puede firmar la cadena personal**. El asiento `firma.creada` lleva `origen="reconciliation"`. La firma personal (`/requests/{id}/sign`, `/sign-xml`) NUNCA acepta este token.


**Cadena de firma (bandeja del firmante, RF-FIR-04).** Todas exigen `PERM_FIRMA`; el firmante se deriva del JWT (nunca del cuerpo, RF-FIR-11). El turno activo es el de menor `orden` aún pendiente. La firma personal exige **2FA** (RF-FIR-13): **OTP-correo** (E06 Inc.2) o, más fuerte, **TOTP RFC 6238** (posesión de dispositivo, E06 Inc.6, ver [ADR-016 addendum Inc.6](adr/ADR-016-firma-electronica.md#addendum-incremento-6-2026-07-06-2fa-por-totp-rfc-6238-en-la-firma-personal)). El secreto TOTP vive en **auth-service** (cifrado AESGCM); ver `POST /api/v1/auth/me/totp/*` abajo. **Formato de la firma personal (E06/E17 F4, Inc.1)**: si el firmante tiene una **clave de firma PKI activa** (ver `/me/signing-key/*`), la cadena produce una firma **XAdES-B/T por-usuario** con su propio certificado (verificable por terceros vía `/verify`, `provider=xades_personal`); si no, degrada a la **firma HMAC nativa** (`provider=nativa`, retrocompat). Ambas son firma electrónica **art. 7 no acreditada** (`acreditado=false`) — la PKI por-usuario añade verificabilidad/formato/separación criptográfica, no valor legal reforzado (custodia servidor ⇒ sin sole-control). **Gate XAdES por nivel (Inc.3; política **de la entidad**: parámetro `firma_personal_require_xades` ∈ `off`|`clasificados`|`todos`, default `off`, editable con `PUT /config/params/firma_personal_require_xades`; el env `FIRMA_PERSONAL_REQUIRE_XADES` solo respalda a la entidad que no lo define, y un valor desconocido en la tabla se aplica como `todos`)**: con `clasificados`, un acto de `nivel_seguridad>=2` **ya no degrada** a nativa — sin cert activo → **`422 firma_xades_requerida`** (con guía de enrolamiento), auth-service caído → **`503 canal_firma_personal_no_disponible`** (fail-closed); con `todos`, aplica en cualquier nivel. La denegación deja asiento `firma.xades_requerida_denegada` atómico y **no consume** ningún factor 2FA. En batch se evalúa **por ítem**. El env legacy bool se acepta (`false`→`off`, `true`→`todos`); un valor no reconocido aborta el arranque. Habilitar `clasificados` solo tras completar el enrolamiento de los firmantes con clearance>=2 (la emisión del cert es offline).

- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…], roles?:["visto_bueno"|"firma",…], nivel_seguridad?}` — crea la cadena (turnos `orden` 1..N). `409 cadena_existente` si el objeto ya tiene una cadena abierta. **`409 radicado_anulado` si el objeto es un radicado sin efecto**: un documento anulado no recoge firmas. El mismo control corre al **firmar un turno** y al **dar visto bueno**, no solo aquí — anular puede ocurrir entre la apertura de la cadena y cada turno, y comprobarlo solo en la creación dejaría firmables todas las cadenas ya abiertas. Se evalúa **después** del no-read-up, para no confirmarle la existencia del objeto a quien no lo alcanza. `roles` describe los mismos turnos que `firmantes` y debe tener su misma longitud, con **al menos un `firma`**: un circuito de solo vistos buenos no produce documento definitivo.
  **De dónde sale `payload_sha256`, que no es lo mismo en todos los tipos.** Para `documento` es **obligatorio en el cuerpo**, porque no hay productor a quien preguntárselo. Para `borrador` lo resuelve el **servidor** contra document-service y el del cuerpo se **ignora** — una huella que el llamante elige no vincula a nadie —, así que no se le pide: exigirlo obligaba a mandar un valor destinado a la basura, es decir, a inventarse una huella. Y la de un borrador es la del **TEXTO** (`cuerpo_sha256`), no la del PDF/A: el PDF solo existe al radicar, así que anclar al documento hacía el circuito inalcanzable —solo podía abrirse después de radicar, cuando aprobar ya no sirve—. Los turnos de visto bueno aprueban un texto; el turno de firma produce el documento definitivo. `409 borrador_sin_texto` si el cuerpo está vacío.
  **Invalidación (RF-PRO-11):** al resolver cualquier turno se **relee** la huella vigente y se compara; si el texto cambió → `409 documento_modificado` y las aprobaciones anteriores no se arrastran. Es la defensa que manda, síncrona: el evento equivalente es *at-least-once* y queda como limpieza de bandejas, no como garantía. No poder comprobar **deniega** (`503`).
  _Nota:_ para firmar con 2FA la solicitud debe tener `payload_sha256` estable.
  - **Se firma el contenido, no el identificador (AREA-04-09).** Para `radicado` y `expediente` el `payload_sha256` del cuerpo se **ignora**: la huella la resuelve el SERVIDOR al abrir la cadena —SHA-256 del **documento principal** (anexo `es_principal`) del radicado (document-service) o del **índice electrónico vigente** del expediente (archive-service), ambos por endpoint interno con `X-Internal-Token`— y es la que se firma. Antes de **cada** firma y de cada visto bueno se relee: si cambió, `409 documento_modificado` (hay que abrir una cadena nueva). Fail-closed: productor caído → `503 verificacion_documento_no_disponible`; sin contenido que sellar → `409 radicado_sin_documento` / `indice_no_disponible` / `anexo_sin_huella`; objeto inexistente → el `404` neutro. **Cadenas abiertas antes de este cambio** (huella `NULL`) no se firman a ciegas: `409 cadena_sin_huella`; mientras sigan `pendiente` ocupan el índice de cadena abierta, así que el operador las invalida en lote con `python -m app.ops.invalidar_cadenas_sin_huella [--dry-run] [--tenant slug]` (estado `invalidada`, `invalidada_motivo='cadena_sin_huella'`, un asiento `firma.cadena_invalidada` por cadena en la misma transacción; idempotente) y entonces se abre la de reemplazo. El 503 `verificacion_documento_no_disponible` nombra solo al productor (`document-service`/`archive-service`) y el código HTTP: nunca reenvía el cuerpo que respondió. Los asientos `firma.solicitud_creada` y `firma.turno_firmado` llevan `huella_documento_sha256` (la fijada al abrir la cadena) y `firma.turno_firmado` además `gate_modo` (la política XAdES efectiva). Y donde la cadena no fija huella, **`/sign` rechaza `payload_xml` con `422 payload_xml_no_admitido`**: el firmante no elige los bytes al firmar.
  - **`objeto_tipo` acotado** a `radicado` · `expediente` · `borrador` (`422` fuera del conjunto). `documento` **salió del conjunto** (2026-09-12): estaba admitido sin productor, de modo que una cadena sobre él se creaba (`201`) y no se podía firmar nunca (`409 documento_desaparecido`, porque la guarda de vigencia lo buscaba como borrador). Vuelve el día que exista un artefacto canónico que firmar, con su fuente de huella. No es validación cosmética: el nivel de seguridad de la cadena se resolvía comparando este campo —texto libre del body— contra el literal `"radicado"`, así que `"Radicado"` bastaba para saltarse la fuente autoritativa (RF-SEG-08).
  - **El nivel lo decide el objeto, no el body.** Si `objeto_id` resuelve contra `radicados` o `expedientes`, manda el `nivel_seguridad` real de esa fila y el del body se ignora — se resuelve **sin mirar `objeto_tipo`**, de modo que declarar `borrador` sobre el identificador de un radicado CLASIFICADA tampoco rebaja nada. `nivel_seguridad` solo surte efecto para objetos sin registrar (hoy `borrador`), donde no hay tabla que consultar.
  - **Fail-closed**: `objeto_tipo` ∈ {`radicado`, `expediente`} cuyo `objeto_id` no resuelve → `404 solicitud_not_found`, el mismo neutro del no-read-up (no distingue «no existe» de «no lo alcanzas»). Antes se caía al nivel declarado en el body.
- `GET /api/v1/signature/requests/{id}` — estado de la cadena: cabecera + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`. **Alcance aceptado (decisión de producto, no defecto):** lo ve cualquiera con `PERM_FIRMA` cuyo clearance alcance el objeto (no-read-up), sea o no firmante de la cadena; así un jefe de área o un auditor puede seguir un trámite. No expone contenido, solo estado y turnos. Restringirlo a los participantes sería una decisión de producto aparte.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — cadena abierta de un objeto (`404` si no hay). Mismo alcance aceptado que la lectura por id (`PERM_FIRMA` + clearance).
- `GET /api/v1/signature/pending?page=&size=` — bandeja: solicitudes cuyo **turno activo** es del usuario autenticado (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** emite el reto 2FA: genera un OTP de 6 dígitos ligado a `(solicitud, firmante del turno activo, hash_documento)`, lo envía por correo al firmante y devuelve `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). El OTP **nunca** aparece en la respuesta ni en logs. **Exige las mismas precondiciones que `/sign` (B7)**, con un único código compartido (`_precondiciones_de_turno`) y un guardián de paridad que exige el mismo `error` en los dos caminos; ninguna crea un desafío, envía correo ni consume un factor. En este orden:
  1. `404 solicitud_no_encontrada` (neutro; también bajo el nivel de acceso del actor);
  2. `403 no_es_firmante` (antes que el estado: a un ajeno no se le cuenta nada);
  3. `409 solicitud_no_pendiente` (también si no hay turno activo);
  4. `403 no_es_su_turno`;
  5. `409 turno_es_de_visto_bueno` (pedir un OTP para un visto bueno es correo desperdiciado: `firmar` lo rechazaría);
  6. `409 radicado_anulado`;
  7. **gate de huella vigente** (el mismo que `/sign`): `409 cadena_sin_huella` / `documento_desaparecido` / `documento_modificado`, `503 verificacion_documento_no_disponible` (después de la pertenencia y el turno: a un ajeno no se le confirma que el texto cambió);
  8. **credencial personal** (la misma consulta que `/sign`): con certificado personal activo, **`409 totp_requerido`** («use el código de su app autenticadora; el correo no sirve para este acto») y no se envía correo; si la credencial es de otra persona que el actor, `403 identidad_incoherente` con asiento `firma.identidad_incoherente` (`via=signing_key_status`); sin certificado y bajo el gate XAdES, `422 firma_xades_requerida` o `503 canal_firma_personal_no_disponible` (sin el asiento `firma.xades_requerida_denegada`, que es del acto de firmar). Con el canal de credenciales caído y el gate en `off`, `/sign` degrada a nativa y `/challenge` sigue: el correo sí servirá.

  Después, en transacción con relectura bloqueada: `429 challenge_cooldown` si se reemite antes de 30s; `403 identidad_incoherente` si el dueño del bearer (a cuyo correo saldría el código) no es el actor (AREA-04-05; asiento `firma.identidad_incoherente`); `409 totp_requerido` si el acto es de `nivel_seguridad ≥ 2` y el actor **ya tiene TOTP activo** (AREA-04-02); `503 canal_2fa_no_disponible` si el correo o su consulta no están disponibles (fail-closed). El OTP del correo se guarda como HMAC con **clave por entidad** que ata el `challenge_id` (`key_version=1`, AREA-04-11). Se retiró `422 payload_no_fijado`: el desafío se ata al identificador cuando la cadena no fija huella.
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id?, otp?, totp_code?}` — firma el turno activo (reusa el firmante nativo HMAC) exigiendo el **2FA verificado**; avanza la cadena y, al firmar el último turno, la solicitud pasa a `firmado`. **Selección de factor**: si viene `totp_code` (6 dígitos) y el firmante tiene TOTP **activo** → se verifica contra auth-service (**no** requiere `/challenge` previo); si vienen `challenge_id`+`otp` → OTP-correo; si viene `totp_code` pero el firmante **no** tiene TOTP activo → **`422 totp_no_activo`** (no cae en silencio al correo); ambos → prevalece TOTP; ninguno → **`409 2fa_requerido`**. Errores TOTP: `401 totp_incorrecto` `{intentos_restantes}`, `423 totp_bloqueado`, `503 canal_2fa_no_disponible` si auth no responde (fail-closed → rollback). Errores OTP-correo: `401 otp_incorrecto` `{intentos_restantes}` (5 intentos → `challenge_bloqueado`), `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`, `403 challenge_ajeno`. Comunes: `403 no_es_firmante` y `403 no_es_su_turno`; `409` si la solicitud no está pendiente o se pierde la carrera del turno (lock `FOR UPDATE`). **Orden de los guardas (AREA-04-09)**: 404 neutro (no-read-up) → `403 no_es_firmante` → `409 solicitud_no_pendiente`: un ajeno a la cadena recibe el mismo 403 sea cual sea el estado y no puede sondear si está abierta, firmada o rechazada (igual en `/reject`, `/vobo`, `/challenge` y por ítem en `/batch`). **`409 turno_es_de_visto_bueno` si el turno activo es de revisión, no de firma** — el gemelo del gate que `/visto-bueno` tenía desde el principio en la dirección contraria, y que faltaba justo en la peligrosa: firmar un turno de visto bueno produce una firma que compromete a la entidad por un acto que su autor entendía como una revisión. El rechazo ocurre **antes** de consultar el estado de la credencial, así que ningún factor 2FA se consume en un acto que se va a rechazar. El 2FA es **no desactivable** para `nivel_seguridad ≥ 2` (RESERVADA/CLASIFICADA) — ambos factores lo satisfacen, **salvo que el actor ya tenga TOTP activo: entonces el correo no vale y se responde `409 totp_requerido`** (AREA-04-02; antes bastaba controlar el buzón para saltarse el autenticador). El TOTP verificado, la credencial personal y el correo deben ser **del actor** al que se atribuye la firma: si el dueño del bearer es otro → `403 identidad_incoherente` con asiento `firma.identidad_incoherente` y sin marcar el turno (AREA-04-05). Con la política de la entidad en `off`, si el canal de credenciales (`/internal/signing-key/status`) está caído y la firma cae a `nativa`, queda el asiento **`firma.fallback_nativa_canal_caido`** en la misma transacción que la firma (AREA-04-12; activar el gate `clasificados` es decisión de la entidad y exige enrolar certificados antes). El estado de la credencial personal se consulta **antes** del row-lock del turno (AREA-04-06).
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rechaza el turno activo y **detiene** la cadena (solicitud `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id?, otp?, totp_code?}, …]}` — firma en lote los turnos del usuario, **un segundo factor por documento** (RF-FIR-13): OTP-correo (`challenge_id`+`otp`) o `totp_code`; no aborta ante un fallo individual (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). _(Contrato cambiado en E06 Inc.2: antes `{solicitud_ids:[…]}`.)_

### Envíos postales (E20) — document-service

Ciclo de vida del despacho físico de un radicado de salida. Máquina de estados `registrado → en_transito → entregado/devuelto/fallido` (`entregado`/`devuelto`/`fallido` terminales). Las rutas humanas exigen **`PERM_RADI_SALIDA`** y aplican no-read-up por _clearance_.

- `POST /api/v1/documents/{document_id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado con un operador (guía autogenerada). `201`. Resuelve el _clearance_ del llamante → `404 radicado_not_found` si el radicado no existe **o** lo supera (indistinguibles): no se despacha lo que no se puede ver. **Desde T4:** exige `PERM_RADI_SALIDA` **nivel 3**; asienta `document.envio_registrado` (actor = `X-User-Id`) en la misma transacción; la guía deriva de un id propio del envío, así que caben varios envíos del mismo radicado y operador.
- `GET /api/v1/documents/{document_id}/envios` — envíos del radicado (filtrados por _clearance_).
- `GET /api/v1/envios?estado=&page=&size=` — listado transversal del tenant (`X-Total-Count`, no-read-up).
- `GET /api/v1/envios/{shipment_id}` — detalle (`404` si el radicado supera el _clearance_ del llamante).
- `PUT /api/v1/envios/operadores/{operador}/credencial` `{secreto (≥32), gracia_horas = 24 (0-168)}` — (T13 / AREA-09-10) **rota el secreto HMAC del webhook** del operador (`4-72|servientrega|otro`). Gate `USUA_PERM_ADMIN` **crud≥5**. Dos ranuras: el secreto nuevo pasa a vigente y el anterior sigue valiendo hasta `anterior_caduca_at` (`gracia_horas`; 0 = corte): el callback acepta **cualquiera** de las ranuras vigentes (`verify_any`, sin cortocircuito) y la caducidad se decide en Python con el reloj del servicio. Los secretos se guardan **cifrados** (AESGCM, AAD = entidad + operador, misma clave maestra `EMAIL_ENCRYPTION_KEY`; sin ella `503 custodia_no_configurada`: no se guarda nada en claro); el secreto **nunca** se devuelve ni se asienta (asiento `document.postal_credencial_rotada` con `gracia_horas`/`primera_vez`). Respuesta `{operador, rotada_at, anterior_vigente_hasta}`. Migración `044`; la columna en claro heredada se vacía en la primera rotación.
- `POST /api/v1/envios/{shipment_id}/estado` `{estado, descripcion?, motivo?, acuse_file_id?, acuse_sha256?}` — **`entregado` a mano exige el acuse de recibo** (T13 / AREA-09-10): `acuse_file_id` (el `file_id` subido a storage) **y** `acuse_sha256` (64 hex) → `422` si faltan; el servidor los **coteja con storage** (B2, ADR-031): el fichero debe existir en el tenant y la huella coincidir (`422 acuse_invalido`, solo si el fichero es suyo), **lo debe haber subido quien registra la entrega** (`403 acuse_ajeno`, también si el fichero no tiene autor **o no existe**: se responde igual para no revelar qué `file_id` existen; se comprueba antes que la huella) y **no puede ser ya otra cosa** —anexo de un radicado, borrador, correo, el acuse de otro envío…— (`409 acuse_ya_referenciado`; solo el reintento del MISMO envío con su propio acuse es idempotente); `503 acuse_no_verificable` si storage no responde. Storage deduplica por autor, así que volver a subir el mismo PDF da el mismo `file_id` y cae en 409: es la misma prueba. Un índice único parcial sobre `postal_shipment(acuse_file_id)` (migración `045`) cierra la carrera entre dos registros simultáneos (→ `409 acuse_ya_referenciado`) y se asienta la huella que devuelve storage; se persisten en `postal_shipment.acuse_*`, viajan en la respuesta y en el asiento `document.envio_estado`. El callback firmado del operador no los necesita (su firma es su prueba). Transición **manual** (despacho humano). `motivo` alimenta `motivo_devolucion` —la causal en su propio campo, consultable— igual que ya hacía el webhook entrante del operador; sin él la causal solo quedaba dentro del texto libre de `descripcion`. Resuelve el _clearance_ del llamante → `404` si el radicado lo supera (cierre D-14); `409 transicion_invalida` para un salto no permitido. **Desde T4:** exige `PERM_RADI_SALIDA` **nivel 3**, asienta `document.envio_estado` en la misma transacción, y el `UPDATE` compara estado previo y _clearance_: si el envío cambió mientras tanto responde `409 transicion_invalida`.
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

- `POST /api/v1/export` `{comentario?, year?, doc_type?}` — **exporta radicados a un paquete interoperable** (ZIP). **Autenticado** (no público); gate `USUA_PERM_EXPEDIENTE` + **no-read-up por-radicado** (solo se exporta lo que el llamante puede leer; los sobre-clearance se omiten y se reportan content-free en el manifiesto). Respuesta `application/zip` con cabeceras `X-Export-UUID`, `X-Export-Count`, `X-Export-Excluded` (las tres reenviadas por el gateway: están en `FORWARDED_RESPONSE_HEADERS` y un guardián de `shared/tests` censa los servicios para que ninguna cabecera propia se quede fuera de esa lista — el ZIP viaja como cuerpo, así que son lo único que dice cuánto se exportó y cuánto recortó el clearance). Contenido del ZIP: `manifiesto.json` (UUID de exportación, comentario, marcas inicio/fin, lista de radicados con SHA-256, excluidos), `esquema/radicado.schema.json` (**JSON Schema draft 2020-12** publicado), `radicados/{numero}.json` (datos por radicado que **validan contra el esquema**: metadata sistema+contextual, disposición, clasificación, anexos, historial), `anexos/{numero}/{i}-{archivo}` (binarios), `checksums.txt` (SHA-256 por miembro). El **número de radicado se preserva** como identidad (Ac. AGN 060/2001). Empaquetado físico ZIP provisional (BagIt/OAIS+WORM = E10). Filtros opcionales `year` (2000–2100) y `doc_type` (E/S/I). **Desde T4:** gate `USUA_PERM_EXPEDIENTE` **nivel 3**; un import/export a la vez por proceso (`429 operacion_ocupada` con `Retry-After`); tope de 512 MiB de anexos (`422 export_demasiado_grande`).

- **Ubicación en el paquete (ADR-029).** `metadata_sistema.ubicacion_dept` / `ubicacion_dept_code` son **informativos** (dónde estaba el radicado al exportar). `POST /api/v1/import` los **ignora**: la ubicación de lo importado la deriva el flujo propio de la entidad receptora. El poseedor (`flujo_usuario`) nunca sale del sistema en un paquete.

- `POST /api/v1/import` — **importa un paquete interoperable** de radicados. **Autenticado**; gate `USUA_PERM_EXPEDIENTE` + **no-write-up por clearance**. Recibe el ZIP de INT-01 como **cuerpo crudo** (`Content-Type: application/zip`, tope 512 MiB). **Validación TODO-o-NADA antes de ingerir**: valida cada radicado contra el JSON Schema de confianza del servicio y **verifica el SHA-256 de cada binario**; si algo falla → `422 paquete_invalido` **sin dejar estado parcial**. Reingiere **preservando el número de radicado** (una colisión se **omite**, no se sobreescribe); reconstruye la relación E↔S (`responde_a`) por número; sube los binarios a storage; audita `radicado.import`. Guardas anti zip-slip/zip-bomb. Respuesta `{import_uuid, importados, omitidos_existentes, omitidos_clearance, relaciones_reconstruidas}`. Un radicado con `nivel_seguridad` > clearance del importador se **omite** (`omitidos_clearance`). **ADR-028:** `metadata_sistema.status` solo admite `registered|distributed|in_progress|closed`: un paquete con `anulado` (o cualquier otro valor) se rechaza entero con `422` —no trae el acto de anulación que lo justifique—; el asiento `radicado.import` registra el `status_origen` de cada radicado importado. **Desde T4:** gate `USUA_PERM_EXPEDIENTE` **nivel 3**; `429 operacion_ocupada` si hay otro import/export en curso; `413` en cuanto el cuerpo supera 512 MiB (sin bufferizarlo entero); el contador de secuencias solo avanza con números de ESTA entidad, y una secuencia > 999999 o un año fuera de [1900, año actual] se rechaza con `422 paquete_invalido`. **Revisión de T4:** un número con el código de ESTA entidad y secuencia > 999998 también es `422` (el siguiente nativo saldría de 7 dígitos); el cuerpo debe llegar en 300 s o responde `408 cuerpo_demasiado_lento` (un cliente que gotea ya no retiene el cupo de todos los tenants). **Binarios huérfanos (AREA-09-14):** los binarios subidos cuyo radicado no llegó a existir (reversión total u omisión por carrera) se asientan como `document.import_binarios_sin_radicado` (`file_id` + `sha256`, content-free) y ahora además se **purgan**, no solo se asientan. Se encolan en `import_binario_huerfano` (migración 038, en la misma transacción que el asiento) y document-service pide a storage `POST /storage/internal/purgar-huerfanos` (`X-Internal-Token`): storage borra un objeto solo si lo creó esa importación (`uploaded_at >= subido_desde`), NINGÚN anexo/correo/borrador/migración/objeto WORM del tenant lo referencia (storage deduplica por SHA-256), NINGÚN `upload` lo devolvió por deduplicación (`files.dedup_hit_at`, migración storage 017: la marca la escribe un `UPDATE` que toma el bloqueo de fila, así que se serializa con el `FOR UPDATE` de la purga —sin ella la purga solo veía referencias commiteadas y podía borrar un objeto que otra operación acababa de recibir—; si la purga gana, la subida deja un objeto nuevo y no un `file_id` colgante), una tabla de referencias ausente en el tenant se trata como «no sé» (no se borra) y el almacén acepta el borrado, todo en su transacción, con el asiento `file.purgado_huerfano`; la purga confirmada deja `document.import_binarios_purgados`. Si storage no responde o un archivo da `error`, la fila queda `pendiente` y el barrido la reintenta (hasta 20 veces → `agotado`, a revisión manual, y ese paso deja `document.import_binario_agotado` en la misma transacción); el asiento `…_sin_radicado` ya está escrito. Deuda declarada: la ventana de numeración propia (no hay tope de salto sobre `last_seq`, para no impedir restaurar un tenant vacío).

### Capa MCP (E18, ADR-019) — mcp-server, puerto 8009

Expone la API como _tools_ a agentes/LLM, siendo él mismo cliente del gateway. **Protocolo MCP** disponible por **stdio** (`python -m app.mcp_stdio`) y **HTTP streamable**.

**Se enruta por el gateway** (`/api/v1/mcp/` → mcp-server), lo que antes no ocurría: el catálogo y el protocolo quedaban fuera, de modo que un cliente MCP externo no tenía por dónde entrar salvo hablando con el servicio a espaldas del gateway, sin validación JWT ni `X-Tenant-Slug`. Se puede exponer porque el gate de escritura ya no depende del cliente (propuesta firmada, ver arriba). El transporte streamable se monta en `POST /api/v1/mcp/stream` además de en `/mcp`.

El contexto de sesión son **dos cabeceras**: `Authorization` y `X-Tenant-Slug`. `X-User-Permissions` y `X-User-Roles` **ya no se leen** — la primera no la producía nadie y el gateway la descarta por forjable, así que llegaba vacía y toda tool con permiso quedaba invisible por ese transporte; los permisos salen de `GET /api/v1/auth/me` con el token del llamante, y con ellos el **nivel CRUD**. Los ajustes de entorno equivalentes (`MCP_USER_PERMISSIONS`, `MCP_USER_ROLES`) se han retirado.

**Marco de alcance**: la tool `mi_alcance` y el resource `orfeo://alcance` devuelven lo que la persona puede ejecutar, lo que puede consultar y lo que **no** puede con su motivo y su **vía alternativa**. Se ofrece siempre, aunque no alcance ninguna escritura: el conjunto de tools dice qué se puede hacer y calla lo demás, y callar es cómo un límite acaba descubriéndose fallando.

API HTTP simple del catálogo:

- `GET /api/v1/mcp/tools` — catálogo de tools que ESTE llamante puede usar, resuelto contra `/auth/me` (permiso **y** nivel). Incluye los **procedimientos** (`method: "PROCEDIMIENTO"`), listados junto a las tools y no en un catálogo aparte: para quien consume esto, «radicar un documento» y «tramitar una petición» son dos cosas que puede pedir.

#### Procedimientos: el trámite entero, no la llamada suelta

Una tool traduce a **un** endpoint. Eso alcanza para consultar y se queda corto para lo que hace un funcionario: radicar una petición son cuatro actos —radicar, repartir, expedientar, avisar—. Con tools sueltas quien los encadena es el modelo: elige el orden, inventa el enlace entre un paso y el siguiente, y si algo falla a mitad nadie sabe en qué estado quedó el trámite.

Un procedimiento declara esa secuencia **en el servidor**, con nombre y orden fijo, y sigue siendo declarativo (una entrada en una tabla, no código nuevo por procedimiento). Disponibles hoy: `tramitar_peticion_ciudadana`, `responder_radicado`, `conformar_expediente` y `estado_del_tramite` (solo lectura, compone cinco consultas en una respuesta).

Tres propiedades, y las tres importan:

1. **Cada paso viaja con el token de la persona.** Un procedimiento es un orden, no una credencial: si el usuario no puede asignar, ese paso devuelve `403` igual que si hubiera llamado a la tool a mano. No puede hacer nada que su usuario no pueda.
2. **Una sola confirmación para el acto completo.** El procedimiento de escritura devuelve una propuesta firmada con la cadena de pasos visible —quien confirma tiene derecho a saber qué está aprobando—. Pedir una confirmación por paso enseñaría a confirmar sin leer.
3. **No se finge atomicidad.** No hay transacción que abarque llamadas HTTP a servicios distintos, y **radicar consume un número inmutable que no vuelve al mostrador**. Por eso el resultado es un parte paso por paso (`pasos`, `pasos_ejecutados`, `completado`, `advertencia`) y no un booleano: quien pregunta necesita saber que tiene un radicado con número y sin repartir, no un «falló» que le llevaría a radicarlo otra vez.

Cobertura declarada: el censo del gateway son **405 operaciones**, de las cuales **63 son actos de sistema sellados** (reconciliación, rutas internas entre servicios) que ningún canal humano expone. Sobre las **342 restantes el catálogo MCP cubre 203 — el 59 %**. El desglose por flujo de trabajo, con lo que falta en cada uno, está en [API por flujos](mcp-flujos.md); se dice aquí en vez de dejar que se descubra pidiendo algo que no existe. **El censo se deriva del código en cada revisión, nunca se copia**: la cifra anterior de esta página (367 operaciones, 41 tools) se quedó ocho meses detrás del catálogo mientras el changelog sí registraba cada crecimiento — un número escrito a mano al lado de uno que se mueve.
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invoca la tool: propaga `Authorization` + `X-Tenant-Slug` al gateway. La identidad y los permisos se resuelven contra `GET /api/v1/auth/me` con el token del llamante (permiso **y** nivel CRUD; `403` si no alcanza). El catálogo completo —**207 tools**— está en la tabla de más abajo, agrupado por épica.
  **Las operaciones de escritura NO se ejecutan aquí: devuelven una propuesta.** `X-Confirm-Write` ya **no se lee** — era un booleano que cualquier cliente podía poner y que no ataba la confirmación a ningún acto concreto (confirmaba «algo», no «esto»). La respuesta es `{kind:"proposal", ejecutado:false, proposal:{id, tool, operacion, campos, expira_en, token}, aviso}`: `campos` son las etiquetas legibles del acto —redactadas por el **catálogo**, nunca por el modelo— y `token` es un valor opaco firmado con HMAC-SHA256 que lleva dentro la tool y los argumentos ya validados. Vence a los 120 s y está atado al usuario y a la entidad que lo pidieron.
- `POST /api/v1/mcp/proposals/execute` `{token}` — ejecuta el acto que va firmado en el token. **El cuerpo lleva solo el token**: los argumentos salen de él, así que lo ejecutado es exactamente lo descrito. Se **reautoriza** al confirmar (entre proponer y confirmar pueden haberle retirado el permiso: la propuesta acredita _qué_ se aprobó, no que quien la presenta siga pudiendo hacerlo) y se **consume** antes de despachar. Errores: `400 propuesta_invalida`, `403 propuesta_ajena` / `permiso_insuficiente`, `409 propuesta_consumida`, `410 propuesta_expirada`.
  El mismo acto por el **protocolo MCP**: las escrituras devuelven la propuesta como resultado estructurado y se confirman con la tool `confirmar_accion(token)`; donde el transporte tiene canal de vuelta (**stdio**) se pregunta en banda por **elicitación** con esos mismos campos. En HTTP streamable (`stateless`) no hay sesión que preguntar y queda el camino de dos tools — límite declarado. Las tools llevan `annotations` MCP (`readOnlyHint`, `destructiveHint`) como **pistas** para que el cliente ponga su propia fricción: la garantía sigue viviendo en la propuesta firmada, no en ellas.
  _Límite declarado_: el registro de consumo vive en memoria del proceso — cubre el doble clic y el reintento, no el multi-réplica. `Idempotency-Key: <jti>` se emite hacia el gateway como gancho para que cada épica lo implemente, no como garantía.

El catálogo actual: **207 tools, 75 de escritura** — de ellas **27 declaradas irreversibles** y **6 que exigen teclear un valor de la propia tarjeta** (no una frase fija: reproducir el identificador del lote, la versión o el radicado confirma *este* acto y no se puede anticipar de memoria). Toda escritura devuelve una propuesta firmada y exige confirmación explícita (ver más arriba). La tabla se **genera desde `services/mcp-server/app/tools/catalog.py`**: el permiso y el nivel CRUD de cada fila son los que el catálogo declara, y `shared/tests/test_permisos_declarados.py` comprueba en los dos sentidos que sean los que el endpoint exige.

**W** = escritura · **R** = lectura · el nivel CRUD en negrita (**3**) es el de crear/borrar.

**E01 · Radicación y correspondencia** — 27 tools, 11 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_radicado` | Corregir los datos de un radicado | `PATCH /documents/{document_id}` | `PERM_RADI` (1) | **W** |
| `aprobar_anulacion` | Aprobar la anulación de un radicado | `POST /documents/{document_id}/anulacion/aprobar` | `PERM_PANU_CODI` (1) | **W** irreversible |
| `aprobar_borrador` | Aprobar un borrador de salida | `POST /borradores/{borrador_id}/aprobar` | `PERM_RADI` (1) | **W** |
| `crear_borrador` | Preparar un borrador de salida | `POST /borradores` | `PERM_RADI` (1) | **W** |
| `crear_respuesta_vinculada` | Responder un radicado con una Salida vinculada | `POST /documents/{document_id}/respuesta` | `PERM_RESPUESTA_VINCULADA` (1) | **W** irreversible |
| `editar_borrador` | Corregir un borrador de salida | `PATCH /borradores/{borrador_id}` | `PERM_RADI` (1) | **W** |
| `radicar_borrador` | Radicar un borrador (le asigna número definitivo) | `POST /borradores/{borrador_id}/radicar` | `PERM_RADI` (1) | **W** irreversible |
| `radicar_documento` | Radicar un documento de entrada, salida o interno | `POST /documents` | `PERM_RADI` (1) | **W** irreversible |
| `rechazar_anulacion` | Rechazar la anulación de un radicado | `POST /documents/{document_id}/anulacion/rechazar` | `PERM_PANU_CODI` (1) | **W** |
| `registrar_tercero` | Registrar un tercero en el catálogo | `POST /terceros` | `PERM_RADI` (1) | **W** |
| `solicitar_anulacion` | Solicitar la anulación de un radicado | `POST /documents/{document_id}/anulacion` | `PERM_SOL_ANULAR` (1) | **W** |
| `buscar_terceros` | Buscar un tercero en el catálogo | `GET /terceros` | `PERM_RADI` (1) | R |
| `cola_anulaciones` | Consultar la cola de anulaciones pendientes | `GET /anulaciones` | `PERM_PANU_CODI` (1) | R |
| `consultar_actores` | Consultar los actores de un radicado | `GET /terceros/radicado/{radicado_id}/actores` | `PERM_RADI` (1) | R |
| `consultar_anulacion` | Consultar la anulación de un radicado | `GET /documents/{document_id}/anulacion` | _sin permiso_ | R |
| `consultar_borrador` | Consultar un borrador | `GET /borradores/{borrador_id}` | `PERM_RADI` (1) | R |
| `consultar_por_numero` | Consultar un radicado por su número | `GET /documents/by-tracking/{tracking_number}` | _sin permiso_ | R |
| `consultar_radicado` | Consultar un radicado | `GET /documents/{document_id}` | _sin permiso_ | R |
| `historial_acceso_radicado` | Ver quién ha consultado un radicado | `GET /documents/{document_id}/historial-acceso` | _sin permiso_ | R |
| `listar_borradores` | Ver los borradores de salida | `GET /borradores` | `PERM_RADI` (1) | R |
| `listar_departamentos` | Consultar los departamentos DIVIPOLA | `GET /terceros/divipola/departamentos` | `PERM_RADI` (1) | R |
| `listar_municipios` | Consultar los municipios de un departamento | `GET /terceros/divipola/municipios` | `PERM_RADI` (1) | R |
| `listar_plantillas` | Consultar las plantillas de redacción | `GET /plantillas` | `PERM_RADI` (1) | R |
| `listar_radicados` | Listar radicados | `GET /documents` | _sin permiso_ | R |
| `listar_respuestas` | Ver qué respuestas se han dado a un radicado | `GET /documents/{document_id}/respuestas` | _sin permiso_ | R |
| `radicados_de_un_tercero` | Ver en qué radicados aparece un contacto | `GET /terceros/{tercero_id}/radicados` | `PERM_RADI` (1) | R |
| `terceros_duplicados` | Ver posibles contactos duplicados | `GET /terceros/duplicados` | `PERM_RADI` (1) | R |

**E02 · Expedientes** — 9 tools, 5 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_expediente` | Corregir los datos de un expediente | `PATCH /expedientes/{expediente_id}` | `USUA_PERM_EXPEDIENTE` (2) | **W** |
| `cerrar_expediente` | Cerrar un expediente (irreversible) | `POST /expedientes/{expediente_id}/close` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `crear_expediente` | Abrir un expediente | `POST /expedientes` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `excluir_radicado` | Excluir un radicado de un expediente | `DELETE /expedientes/{expediente_id}/radicados/{radicado_id}` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `vincular_radicado` | Vincular un radicado a un expediente | `POST /expedientes/{expediente_id}/radicados` | `USUA_PERM_EXPEDIENTE` (2) | **W** |
| `consultar_expediente` | Consultar un expediente | `GET /expedientes/{expediente_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_foliado` | Consultar la foliación de un expediente | `GET /expedientes/{expediente_id}/foliado` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `hoja_ruta_expediente` | Consultar la hoja de ruta de un expediente | `GET /expedientes/{expediente_id}/eventos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_expedientes` | Listar expedientes | `GET /expedientes` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E03 · Metadatos** — 3 tools, 1 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `tipificar_radicado` | Tipificar un radicado (asignar o corregir su tipo documental) | `PATCH /documents/{document_id}` | `PERM_RADI` (**3**) | **W** |
| `listar_elementos_metadato` | Listar los elementos de metadato | `GET /metadata/elements` | _sin permiso_ | R |
| `listar_plantillas_metadato` | Listar las plantillas de metadatos | `GET /metadata/templates` | _sin permiso_ | R |

**E04 · TRD / TVD y disposición** — 38 tools, 21 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `anular_lote_eliminacion` | Retirar un lote del procedimiento de eliminación | `POST /eliminacion/lotes/{lote_id}/anular` | `USUA_PERM_TRD` (**3**) | **W** |
| `aprobar_version_trd` | Aprobar una versión de la TRD | `POST /trd/{code}/versiones/{version}/aprobar` | `USUA_PERM_TRD` (**3**) | **W** |
| `aprobar_version_tvd` | Aprobar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/aprobar` | `USUA_PERM_TRD` (**3**) | **W** |
| `avalar_lote_eliminacion` | Registrar el aval del Comité sobre un lote de eliminación | `POST /eliminacion/lotes/{lote_id}/avalar` | `USUA_PERM_TRD` (**4**) | **W** teclear `lote_id` |
| `convalidar_version_trd` | Convalidar una versión de la TRD | `POST /trd/{code}/versiones/{version}/convalidar` | `USUA_PERM_TRD` (**3**) | **W** teclear `version` |
| `convalidar_version_tvd` | Convalidar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/convalidar` | `USUA_PERM_TRD` (**3**) | **W** teclear `version` |
| `crear_agrupacion_tvd` | Crear una agrupación de la TVD | `POST /tvd` | `USUA_PERM_TRD` (1) | **W** |
| `crear_serie_trd` | Crear una serie de la TRD | `POST /trd` | `USUA_PERM_TRD` (1) | **W** |
| `crear_tipo_documental` | Crear un tipo documental | `POST /tipos-documentales` | `USUA_PERM_TRD` (1) | **W** |
| `decidir_disposicion_vencida` | Decidir sobre una disposición vencida | `POST /trd/disposicion-vencida/{expediente_id}/decidir` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `derogar_version_trd` | Derogar una versión de la TRD | `POST /trd/{code}/versiones/{version}/derogar` | `USUA_PERM_TRD` (**3**) | **W** teclear `version` |
| `derogar_version_tvd` | Derogar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/derogar` | `USUA_PERM_TRD` (**3**) | **W** teclear `version` |
| `devolver_version_trd` | Devolver una versión de la TRD con observaciones | `POST /trd/{code}/versiones/{version}/devolver` | `USUA_PERM_TRD` (**3**) | **W** |
| `devolver_version_tvd` | Devolver una versión de la TVD con observaciones | `POST /tvd/{code}/versiones/{version}/devolver` | `USUA_PERM_TRD` (**3**) | **W** |
| `generar_acta_eliminacion` | Generar el acta de eliminación de un lote | `POST /eliminacion/lotes/{lote_id}/acta` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `nueva_version_agrupacion_tvd` | Crear una versión nueva de una agrupación TVD | `POST /tvd/{code}/versiones` | `USUA_PERM_TRD` (**3**) | **W** |
| `nueva_version_serie_trd` | Crear una versión nueva de una serie TRD | `POST /trd/{code}/versiones` | `USUA_PERM_TRD` (**3**) | **W** |
| `proponer_lote_eliminacion` | Proponer un lote de eliminación | `POST /eliminacion/lotes` | `USUA_PERM_TRD` (**3**) | **W** |
| `publicar_inventario_lote` | Publicar el inventario de un lote de eliminación | `POST /eliminacion/lotes/{lote_id}/publicar` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `registrar_rusd_trd` | Registrar la inscripción RUSD de una versión de la TRD | `POST /trd/{code}/versiones/{version}/registrar-rusd` | `USUA_PERM_TRD` (**3**) | **W** |
| `reintentar_firma_acta_eliminacion` | Reintentar la firma del acta de eliminación | `POST /eliminacion/lotes/{lote_id}/acta/firmar` | `USUA_PERM_TRD` (**3**) | **W** |
| `consultar_acta_eliminacion` | Consultar el acta de eliminación | `GET /eliminacion/lotes/{lote_id}/acta` | `USUA_PERM_TRD` (1) | R |
| `consultar_disposicion` | Consultar la disposición final de un radicado | `GET /documents/{document_id}/disposition` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_lote_eliminacion` | Consultar un lote de eliminación | `GET /eliminacion/lotes/{lote_id}` | `USUA_PERM_TRD` (1) | R |
| `consultar_retencion` | Calcular el calendario de retención de una serie | `GET /trd/{serie_id}/retention` | _sin permiso_ | R |
| `consultar_serie` | Consultar una serie de la TRD | `GET /trd/{serie_id}` | _sin permiso_ | R |
| `consultar_tipo_documental` | Consultar un tipo documental | `GET /tipos-documentales/{tipo_id}` | _sin permiso_ | R |
| `consultar_tvd` | Consultar una agrupación de la TVD | `GET /tvd/{agrupacion_id}` | _sin permiso_ | R |
| `disposicion_vencida` | Consultar los expedientes con disposición vencida | `GET /trd/disposicion-vencida` | `USUA_PERM_TRD` (1) | R |
| `lapidas_lote` | Consultar las lápidas de un lote eliminado | `GET /eliminacion/lotes/{lote_id}/lapidas` | `USUA_PERM_TRD` (1) | R |
| `listar_lotes_eliminacion` | Listar los lotes de eliminación | `GET /eliminacion/lotes` | `USUA_PERM_TRD` (1) | R |
| `listar_tipos_documentales` | Consultar los tipos documentales de la TRD | `GET /tipos-documentales` | _sin permiso_ | R |
| `listar_trd` | Consultar la Tabla de Retención Documental | `GET /trd` | _sin permiso_ | R |
| `listar_tvd` | Listar las agrupaciones de la TVD | `GET /tvd` | _sin permiso_ | R |
| `retencion_tvd` | Consultar la retención de una agrupación TVD | `GET /tvd/{agrupacion_id}/retention` | _sin permiso_ | R |
| `revision_trd_pendiente` | Consultar los expedientes pendientes de revisión de TRD | `GET /trd/revision-pendiente` | `USUA_PERM_TRD` (1) | R |
| `versiones_agrupacion_tvd` | Consultar el historial de versiones de una agrupación TVD | `GET /tvd/{code}/versiones` | `USUA_PERM_TRD` (1) | R |
| `versiones_serie_trd` | Consultar el historial de versiones de una serie TRD | `GET /trd/{code}/versiones` | `USUA_PERM_TRD` (1) | R |

**E05 · Flujos de trabajo** — 16 tools, 9 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `asignar_radicado` | Asignar un radicado a una dependencia | `POST /workflows/assign` | `PERM_TRAMITAR` (1) | **W** |
| `completar_paso` | Completar un paso de trámite | `PATCH /workflows/{step_id}/complete` | `PERM_TRAMITAR` (1) | **W** |
| `crear_cadena_vobo` | Crear la cadena de vistos buenos | `POST /workflows/{radicado_id}/vistos-buenos` | `PERM_RADI` (1) | **W** |
| `decidir_visto_bueno` | Dar o negar el visto bueno | `POST /workflows/{radicado_id}/vistos-buenos/decidir` | `PERM_VOBO` (1) | **W** |
| `devolver_radicado` | Devolver un radicado con causal | `POST /workflows/{radicado_id}/devolver` | `PERM_TRAMITAR` (1) | **W** |
| `ejecutar_transaccion` | Ejecutar una transacción de trámite | `POST /workflows/{radicado_id}/transactions` | `PERM_TRAMITAR` (1) | **W** |
| `finalizar_tramite` | Finalizar el trámite de un radicado (irreversible; `motivo_cierre` obligatorio: `respondido`, `nrr`, `traslado_competencia` u `otro`) | `POST /workflows/{radicado_id}/finalizar` | `PERM_TRAMITAR` (3) | **W** irreversible (se confirma tecleando el radicado) |
| `revertir_asignacion` | Revertir la última asignación | `POST /workflows/{radicado_id}/rollback` | `PERM_TRAMITAR` (1) | **W** |
| `trasladar_radicado` | Trasladar un radicado a otra dependencia | `POST /workflows/transfer` | `PERM_TRAMITAR` (1) | **W** |
| `bandeja_tramite` | Consultar la bandeja de trámite | `GET /workflows/inbox` | _sin permiso_ | R |
| `consultar_cadena_vobo` | Consultar la cadena de vistos buenos | `GET /workflows/{radicado_id}/vistos-buenos` | _sin permiso_ | R |
| `eventos_radicado` | Consultar los eventos de flujo de un radicado | `GET /workflows/{radicado_id}/events` | _sin permiso_ | R |
| `hoja_ruta_radicado` | Consultar la hoja de ruta de un radicado | `GET /workflows/{radicado_id}/history` | _sin permiso_ | R |
| `paso_actual` | Consultar el paso de trámite activo de un radicado | `GET /workflows/{radicado_id}/current` | _sin permiso_ | R |
| `pendientes_equipo` | Consultar los pendientes de trámite del equipo | `GET /workflows/pending` | _sin permiso_ | R |
| `tipos_transaccion` | Consultar los tipos de transacción de trámite | `GET /workflows/transaction-types` | _sin permiso_ | R |

**E06 · Firma electrónica** — 10 tools, 2 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `abrir_cadena_firma` | Abrir un circuito de firmas | `POST /signature/requests` | `PERM_FIRMA` (1) | **W** |
| `firmar_indice` | Firmar el índice electrónico | `POST /expedientes/{expediente_id}/indice/firmar` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `cadena_firma_de_un_objeto` | Ver si un documento ya está en un circuito de firma | `GET /signature/requests` | `PERM_FIRMA` (1) | R |
| `consultar_cadena_firma` | Consultar una cadena de firmas | `GET /signature/requests/{solicitud_id}` | `PERM_FIRMA` (1) | R |
| `consultar_comprobante_firma` | Ver el comprobante de una firma | `GET /signature/firmas/{firma_id}` | _sin permiso_ | R |
| `estado_mi_credencial_de_firma` | Consultar el estado de mi credencial de firma | `GET /auth/me/signing-key` | _sin permiso_ | R |
| `estado_mi_segundo_factor` | Consultar el estado de mi segundo factor | `GET /auth/me/totp` | _sin permiso_ | R |
| `firmas_de_un_radicado` | Ver las firmas de un radicado | `GET /documents/{document_id}/signatures` | `PERM_FIRMA` (1) | R |
| `firmas_pendientes` | Ver qué tengo pendiente de firmar | `GET /signature/pending` | `PERM_FIRMA` (1) | R |
| `indices_pendientes_firma` | Consultar los índices pendientes de firma | `GET /expedientes/indices/pendientes-firma` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E07 · Anexos** — 1 tools

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `listar_anexos` | Consultar los anexos de un radicado | `GET /documents/{document_id}/anexos` | _sin permiso_ | R |

**E08 · Seguridad, RBAC y auditoría** — 15 tools, 1 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `reclasificar_radicado` | Reclasificar el nivel de seguridad de un radicado | `PATCH /documents/{document_id}/security-level` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |
| `clearance_de_grupo` | Consultar el nivel de acceso de un grupo | `GET /auth/groups/{group_id}/clearance` | `USUA_PERM_ADMIN` (1) | R |
| `consultar_auditoria` | Consultar la traza de auditoría | `GET /audit` | `USUA_PERM_ADMIN` (1) | R |
| `listar_grupos` | Listar los grupos de la entidad | `GET /auth/groups` | `USUA_PERM_ADMIN` (1) | R |
| `listar_niveles_seguridad` | Consultar los niveles de clasificación | `GET /auth/security-levels` | _sin permiso_ | R |
| `listar_permisos_del_sistema` | Listar los permisos del sistema | `GET /auth/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `listar_urd_usuario` | Consultar las unidades de reparto de una persona | `GET /auth/users/{user_id}/urd` | `USUA_PERM_ADMIN` (1) | R |
| `listar_usuarios` | Listar los usuarios de la entidad | `GET /auth/users` | `USUA_PERM_ADMIN` (1) | R |
| `listar_usuarios_asignables` | Listar usuarios a los que asignar | `GET /auth/users/pickable` | `PERM_RADI` (1) | R |
| `mi_clearance` | Consultar mi nivel de acceso | `GET /auth/clearance` | _sin permiso_ | R |
| `mi_contexto` | Consultar mi contexto de trabajo | `GET /auth/context` | _sin permiso_ | R |
| `miembros_de_grupo` | Consultar los miembros de un grupo | `GET /auth/groups/{group_id}/members` | `USUA_PERM_ADMIN` (1) | R |
| `permisos_de_grupo` | Consultar los permisos de un grupo | `GET /auth/groups/{group_id}/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `permisos_de_usuario` | Consultar los permisos efectivos de una persona | `GET /auth/users/{user_id}/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `verificar_cadena_auditoria` | Verificar la integridad de la auditoría | `GET /audit/verify` | `USUA_PERM_ADMIN` (1) | R |

**E09 · Búsqueda y reportes** — 3 tools

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `buscar_expedientes` | Buscar expedientes | `GET /expedientes/search` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `buscar_radicados` | Buscar radicados | `GET /search` | _sin permiso_ | R |
| `reporte_radicacion` | Ver el resumen estadístico de radicación | `GET /reports/radicados` | `SGD_PERM_ESTADISTICA` (1) | R |

**E10 · Preservación (WORM/PREMIS)** — 9 tools, 3 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `declarar_plan_preservacion` | Declarar una versión del plan de preservación | `PUT /preservacion/plan` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `migrar_formato` | Migrar un archivo a PDF/A | `POST /preservacion/migrar-formato` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `registrar_evento_preservacion` | Registrar un evento de preservación | `POST /preservacion/eventos` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `artefactos_pendientes_renovacion` | Consultar los artefactos pendientes de renovación WORM | `GET /expedientes/indices/pendientes-renovacion` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `cadena_migraciones` | Consultar la cadena de migraciones de un archivo | `GET /preservacion/migraciones/{file_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_aip` | Consultar el AIP de preservación de un expediente | `GET /expedientes/{expediente_id}/aip` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_plan_preservacion` | Consultar el plan de preservación digital | `GET /preservacion/plan` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `eventos_preservacion` | Consultar los eventos de preservación de un expediente | `GET /expedientes/{expediente_id}/preservacion/eventos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `versiones_plan_preservacion` | Consultar las versiones del plan de preservación | `GET /preservacion/plan/versions` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E11 · Interoperabilidad** — 3 tools

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `consultar_envio` | Consultar un envío | `GET /envios/{shipment_id}` | `PERM_RADI_SALIDA` (1) | R |
| `listar_webhooks` | Listar los canales de salida configurados | `GET /webhooks` | `USUA_PERM_ADMIN` (1) | R |
| `rastrear_envio` | Rastrear un envío con el operador | `GET /envios/{shipment_id}/tracking` | `PERM_RADI_SALIDA` (1) | R |

**E12 · Transferencias** — 9 tools, 4 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `crear_transferencia` | Crear una transferencia | `POST /transferencias` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `enviar_transferencia` | Enviar una transferencia al receptor | `POST /transferencias/{transferencia_id}/enviar` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `rechazar_transferencia` | Rechazar una transferencia | `POST /transferencias/{transferencia_id}/rechazar` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `recibir_transferencia` | Recibir una transferencia | `POST /transferencias/{transferencia_id}/recibir` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `consultar_acta_transferencia` | Consultar el acta de una transferencia | `GET /transferencias/{transferencia_id}/acta` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_fuid` | Consultar el inventario documental (FUID) de una transferencia | `GET /transferencias/{transferencia_id}/fuid` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_transferencia` | Consultar una transferencia | `GET /transferencias/{transferencia_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_transferencias` | Listar las transferencias | `GET /transferencias` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `verificar_acta_transferencia` | Verificar las firmas del acta | `GET /transferencias/{transferencia_id}/acta/verificacion` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E13 · Ventanilla única pública** — 2 tools, 2 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `publicar_de_oficio` | Publicar de oficio un radicado | `POST /documents/{document_id}/publicacion` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |
| `publicar_expediente` | Publicar de oficio un expediente | `POST /expedientes/{expediente_id}/publicacion` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |

**E14 · Administración y PINAR** — 19 tools, 1 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `registrar_avance_pinar` | Registrar el avance de un proyecto PINAR | `POST /pinar/proyectos/{proyecto_id}/seguimiento` | `USUA_PERM_ADMIN` (3) | **W** |
| `arbol_dependencias` | Consultar el árbol de dependencias | `GET /dependencias/tree` | _sin permiso_ | R |
| `calcular_dias_habiles` | Calcular el vencimiento en días hábiles | `GET /business-days/calculate` | _sin permiso_ | R |
| `consultar_dependencia` | Consultar una dependencia | `GET /dependencias/{dependencia_id}` | _sin permiso_ | R |
| `consultar_dependencia_por_codigo` | Consultar una dependencia por su código | `GET /dependencias/by-codigo/{codigo}` | _sin permiso_ | R |
| `consultar_parametro` | Consultar un parámetro de la entidad | `GET /config/params/{key}` | _sin permiso_ | R |
| `consultar_plan_pinar` | Consultar un plan PINAR | `GET /pinar/planes/{plan_id}` | `USUA_PERM_ADMIN` (1) | R |
| `listar_catalogos` | Listar los catálogos de vocabulario | `GET /catalogos` | _sin permiso_ | R |
| `listar_dependencias` | Listar las dependencias de la entidad | `GET /dependencias` | _sin permiso_ | R |
| `listar_festivos` | Consultar los festivos de la entidad | `GET /config/holidays` | _sin permiso_ | R |
| `listar_items_catalogo` | Consultar los valores de un catálogo | `GET /catalogos/{catalogo}` | _sin permiso_ | R |
| `listar_parametros` | Consultar los parámetros de la entidad | `GET /config/params` | _sin permiso_ | R |
| `listar_planes_pinar` | Listar los planes PINAR | `GET /pinar/planes` | `USUA_PERM_ADMIN` (1) | R |
| `mapa_ruta_pinar` | Consultar el mapa de ruta de un plan PINAR | `GET /pinar/planes/{plan_id}/mapa-ruta` | `USUA_PERM_ADMIN` (1) | R |
| `objetivos_pinar` | Consultar los objetivos de un plan PINAR | `GET /pinar/planes/{plan_id}/objetivos` | `USUA_PERM_ADMIN` (1) | R |
| `priorizacion_pinar` | Consultar la priorización de un plan PINAR | `GET /pinar/planes/{plan_id}/priorizacion` | `USUA_PERM_ADMIN` (1) | R |
| `proyectos_pinar` | Consultar los proyectos de un plan PINAR | `GET /pinar/planes/{plan_id}/proyectos` | `USUA_PERM_ADMIN` (1) | R |
| `seguimiento_proyecto_pinar` | Consultar el seguimiento de un proyecto PINAR | `GET /pinar/proyectos/{proyecto_id}/seguimiento` | `USUA_PERM_ADMIN` (1) | R |
| `tablero_pinar` | Consultar el tablero de un plan PINAR | `GET /pinar/planes/{plan_id}/tablero` | `USUA_PERM_ADMIN` (1) | R |

**E15 · Índice electrónico** — 6 tools

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `historial_acceso_expediente` | Consultar el historial de acceso al expediente | `GET /expedientes/{expediente_id}/historial-acceso` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `indice_expediente` | Consultar el índice electrónico de un expediente | `GET /expedientes/{expediente_id}/indice` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `indice_xml` | Obtener el índice electrónico en XML | `GET /expedientes/{expediente_id}/indice.xml` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `roles_autorizados_expediente` | Consultar los grupos autorizados a leer el expediente | `GET /expedientes/{expediente_id}/roles-autorizados` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `verificar_indice` | Verificar la integridad del índice electrónico | `GET /expedientes/{expediente_id}/indice/verify` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `versiones_indice` | Listar las versiones del índice electrónico | `GET /expedientes/{expediente_id}/indice/versions` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E16 · Notificaciones** — 6 tools, 2 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `descartar_evento_atascado` | Descartar un evento fallido | `POST /notification/admin/deadletter/{event_id}/discard` | `PERM_DLQ_ADMIN` (1) | **W** irreversible |
| `reintentar_evento_atascado` | Reintentar un evento fallido | `POST /notification/admin/deadletter/{event_id}/replay` | `PERM_DLQ_ADMIN` (1) | **W** irreversible |
| `consultar_evento_atascado` | Consultar un evento fallido | `GET /notification/admin/deadletter/{event_id}` | `PERM_DLQ_ADMIN` (1) | R |
| `consultar_notificacion` | Consultar una notificación | `GET /notifications/{notification_id}` | `USUA_PERM_ADMIN` (1) | R |
| `historial_notificaciones` | Ver el historial de notificaciones | `GET /notifications/history` | `USUA_PERM_ADMIN` (1) | R |
| `listar_eventos_atascados` | Ver la cola de eventos fallidos | `GET /notification/admin/deadletter/` | `PERM_DLQ_ADMIN` (1) | R |

**E17 · Archivo físico** — 13 tools, 5 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `crear_ubicacion` | Crear una ubicación en el archivo físico | `POST /ubicaciones` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `crear_unidad` | Crear una unidad de conservación | `POST /unidades` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `devolver_prestamo` | Registrar la devolución de un préstamo | `POST /prestamos/{prestamo_id}/devolver` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `registrar_prestamo` | Registrar el préstamo de una unidad | `POST /unidades/{unidad_id}/prestamos` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `ubicar_expediente_en_unidad` | Ubicar un expediente en una unidad | `POST /unidades/{unidad_id}/expedientes` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `consultar_ubicacion` | Consultar una ubicación | `GET /ubicaciones/{ubicacion_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `contenido_unidad` | Consultar el contenido de una unidad | `GET /unidades/{unidad_id}/expedientes` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `inventario_documental` | Consultar el inventario documental | `GET /fuid` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_prestamos` | Consultar los préstamos del archivo físico | `GET /prestamos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_ubicaciones` | Listar las ubicaciones del archivo físico | `GET /ubicaciones` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_unidades` | Listar las unidades de conservación | `GET /unidades` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `movimientos_unidad` | Consultar los movimientos de una unidad | `GET /unidades/{unidad_id}/movimientos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `ubicacion_expediente` | Localizar físicamente un expediente | `GET /expedientes/{expediente_id}/unidades` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E19 · Ingesta de correo** — 8 tools, 5 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `descartar_correo` | Descartar un correo sin radicarlo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/descartar` | `PERM_RADI` (1) | **W** irreversible |
| `liberar_correo` | Liberar la reserva de un correo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/liberar` | `PERM_RADI` (1) | **W** |
| `radicar_correo` | Radicar un correo como documento de entrada | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/radicar` | `PERM_RADI` (1) | **W** irreversible |
| `reservar_correo` | Reservar un correo para trabajarlo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/reservar` | `PERM_RADI` (1) | **W** |
| `sincronizar_buzon` | Traer los correos nuevos de un buzón | `POST /email/cuentas/{cuenta_id}/bandeja/sincronizar` | `PERM_RADI` (1) | **W** |
| `estado_bandeja_correo` | Consultar el resumen de un buzón | `GET /email/cuentas/{cuenta_id}/bandeja/estado` | `PERM_RADI` (1) | R |
| `listar_buzones_correo` | Listar los buzones de correo que puedo trabajar | `GET /email/cuentas` | `PERM_RADI` (1) | R |
| `listar_correos_pendientes` | Ver los correos de un buzón | `GET /email/cuentas/{cuenta_id}/bandeja` | `PERM_RADI` (1) | R |

**E20 · Operador postal** — 4 tools, 2 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_estado_envio` | Actualizar el estado de un envío | `POST /envios/{shipment_id}/estado` | `PERM_RADI_SALIDA` (3) | **W** |
| `despachar_envio` | Despachar un radicado por correo postal | `POST /documents/{document_id}/envios` | `PERM_RADI_SALIDA` (3) | **W** |
| `bandeja_envios` | Consultar la bandeja de envíos | `GET /envios` | `PERM_RADI_SALIDA` (1) | R |
| `consultar_envios_radicado` | Consultar los envíos de un radicado | `GET /documents/{document_id}/envios` | `PERM_RADI_SALIDA` (1) | R |

**E21 · Conocimiento y RAG** — 3 tools

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `buscar_antecedentes` | Buscar antecedentes de un asunto o de un radicado | `POST /knowledge/antecedentes` | _sin permiso_ | R |
| `buscar_conocimiento` | Buscar en la base de conocimiento documental | `POST /knowledge/search` | _sin permiso_ | R |
| `redactar_con_antecedentes` | Redactar un texto apoyado en antecedentes, con citas | `POST /knowledge/rag` | _sin permiso_ | R |

**E23 · Producción documental** — 3 tools, 1 de escritura

| Tool | Qué hace | Endpoint | Permiso (crud) | |
| --- | --- | --- | --- | --- |
| `devolver_borrador_a_edicion` | Devolver un borrador aprobado a edición | `POST /borradores/{borrador_id}/devolver` | `PERM_RADI` (1) | **W** irreversible |
| `campos_de_plantilla` | Ver los campos disponibles de una plantilla | `GET /plantillas/campos` | `PERM_RADI` (1) | R |
| `combinar_plantilla` | Previsualizar una plantilla combinada | `POST /plantillas/{plantilla_id}/combinar` | `PERM_RADI` (1) | R |

**Exclusión mutua declarada, no descubierta con un 422.** `buscar_antecedentes` acepta el asunto (`query`) o el radicado de referencia (`radicado_ref`), nunca ambos ni ninguno — lo impone `AntecedentesRequest` con un `model_validator`, y JSON Schema no lo expresa con las piezas del catálogo. El catálogo lo declara con `exactly_one_of` y `coerce_args` responde `parametros_excluyentes` **antes** de que salga la petición: el 422 del backend dice lo mismo, pero solo después de hacer esperar a la persona.

**`aprobar_borrador` SÍ es requisito** (corregido el 2026-08-17). El ciclo del borrador es crear → editar → aprobar → radicar, y ahora lo exige el backend: `POST /borradores/{id}/radicar` responde `409 borrador_sin_aprobar` si el borrador no está aprobado. Antes solo lo exigía la pantalla (`canRadicar = estado === "aprobado"`), de modo que un `POST` directo saltaba la aprobación y consumía el número igualmente — un control que vive solo en el navegador no es un control.

**Actos irreversibles: frase tecleada, exigida por el backend.** Una propuesta cuya tool declara `destructive` lleva `irreversible: true` y `frase_requerida: "CONFIRMO"`, y ejecutarla exige enviar esa frase (`{token, frase}`). Quién dice que el acto es irreversible **no es el cliente**: es el token firmado, del que sale la tool — así no hay manera de confirmarlo declarando que no lo era. La interfaz deshabilita el botón por cortesía; la barrera vive en `write_gate`, porque una frase validada solo en el navegador es teatro.

**El asistente conversacional también propone.** `POST /api/v1/assistant/message` devuelve `propuestas: []` (campo aditivo) con los actos que el modelo preparó. El `tool_result` que vuelve al modelo dice explícitamente `ejecutado: false` para que no afirme haber hecho algo que no hizo, y el token no entra en su contexto. La confirmación es `POST /api/v1/assistant/confirmar` con `{token, frase?}`.

**Trazabilidad del canal (AREA-11-04).** Todo lo que el mcp-server hace en nombre de una persona —el asistente, el MCP, la confirmación de una propuesta— llega al gateway por `/api/v1/<dominio>` y antes se asentaba como canal `api`, igual que una llamada directa. Ahora cada llamada saliente lleva `X-Origen-Canal: mcp.<ts>.<hmac>`: una marca firmada con `MCP_ORIGIN_SECRET` (secreto dedicado, solo del gateway y del mcp-server, distinto del maestro `IDENTITY_SIGNING_SECRET`), **ligada al tenant y al token** (sha256 del `Authorization`) y con vigencia de 120 s. El gateway la verifica y solo entonces deriva `X-Canal: mcp` (`derive_canal`); sin marca, con firma mala, vencida o de otro token/entidad, el canal es `api`, y sin secreto configurado nunca se atribuye `mcp`. La cabecera no se reenvía aguas abajo. Además, los asientos de canal `mcp` llevan `payload.correlacion = {request_id, idempotency_key}` (el `X-Request-Id` del turno y el `jti` de la propuesta emitido como `Idempotency-Key`), para atar el asiento al turno y a la propuesta que lo autorizó; el resto de canales no cambia de contenido. **Lo que el modelo no puede inventar:** `POST /assistant/message` devuelve además `no_verificados: string[]` (aditivo, vacío casi siempre): todo número con forma `{YYYY}-{TENANT}-{E|S|I}-{NNNNNN}` del texto final que **no** aparezca en algún `tool_result` de la conversación —lo que el sistema devolvió; lo que escribió la persona no cuenta— se lista ahí y el texto añade un aviso «No verificado». No se elimina del texto: la persona debe ver qué se le dijo.

**IP del cliente (AREA-11-08).** El mcp-server reenvía al gateway la IP real del cliente en `X-Forwarded-For` —solo la que el gateway firmó con el secreto de origen `MCP_ORIGIN_SECRET` (`X-Real-Ip` + `X-Real-Ip-Signature`, ligada a IP, persona y entidad; el mcp-server NO posee el secreto maestro de identidad); sin firma o sin secreto no reenvía nada—, de modo que las llamadas del asistente de todas las entidades ya no caen en el cubo por IP del contenedor del mcp-server. Cada turno registra en el log del servicio `iteraciones`, `llamadas_gateway` (lo que gasta del cubo de la persona), `propuestas` y `no_verificados`.

**Fuera del catálogo por decisión, no por olvido — 15 actos declarados.** No están simplemente ausentes: el servidor los publica con su motivo y su vía alternativa en `mi_alcance` y en el recurso `orfeo://alcance`, porque un catálogo recortado en silencio hace que el límite se descubra pidiendo algo que nunca va a ocurrir. Los criterios son cuatro, y protegen cosas distintas:

1. **Actos cuya garantía es que haya una persona presente.** La tarjeta de confirmación encarece el descuido; no convierte a un modelo en una persona. Aquí entran **ejecutar el borrado de un lote de eliminación** (se ofrecen las cuatro fases previas —proponer, publicar, avalar y el acta—, nunca la ejecución), **firmar un turno de una cadena, darle el visto bueno o rechazarlo**, **firmar personalmente el acta de una transferencia** y **comprobar la integridad de una firma**.
2. **Actos que mueven la propia frontera de autorización.** Todo el marco consiste en trabajar *dentro* de los límites de quien pregunta, y un acto que los ensancha los deja de tener: **conceder o retirar permisos, cambiar el nivel de acceso de un grupo, crear personas y grupos**.
3. **Actos que sacan un secreto de un solo uso a un canal que no lo custodia**: **dar de alta o revocar credenciales personales** —TOTP, códigos de recuperación, llave de firma—. Se revelan exactamente una vez por diseño; entregarlos aquí los dejaría escritos en el historial de una conversación.
4. **La regla con la que se mide todo lo demás**, que no es un acto peligroso sino el patrón: **cambiar el calendario de festivos o los parámetros de la entidad** (un festivo añadido hoy mueve vencimientos que ya están corriendo) y **añadir valores a los catálogos o cambiar la estructura de dependencias** (un catálogo al que se le añaden entradas desde una conversación deja de ser cerrado, que era lo único que lo hacía servir para algo).

Completan la lista los actos que **este canal no puede presentar honestamente**: **sellar un artefacto en WORM** y **empaquetar el AIP** (exigen un identificador interno que quien lo pidiera tendría que inventarse, y un candado de diez años no se acorta), **armar la estructura de un plan PINAR** (es planeación con el Comité delante, no una secuencia de altas), **ver el PDF de un borrador y crear o borrar plantillas** (un PDF no cabe en una conversación), **configurar los buzones de correo**, **trabajar un buzón personal** y **dar de alta o retirar un webhook**. Y la vinculación **masiva** (`/radicados/batch`, hasta 500) queda fuera mientras su tarjeta de confirmación no pueda leerse: una tarjeta que dice «500 radicados» no permite confirmar con conocimiento de causa, que es justo para lo que existe.

**Dos invariantes que cruzan servicios** viven en `shared/tests/`, el único carril de CI que ve el repositorio entero: `test_rutas_mcp_alcanzables.py` (toda ruta del catálogo está en la tabla de ruteo del gateway — el hueco que ya se coló con la carga masiva) y `test_permisos_declarados.py` (lo que el catálogo declara es lo que el endpoint exige, en los dos sentidos: declarar de menos ofrece la tool a quien recibirá un 403; declarar de más la esconde a quien sí puede).

### Asistente conversacional (E18, ADR-019/ADR-020) — vía gateway

A diferencia del catálogo/protocolo MCP (que **no** se expone por el gateway), el asistente conversacional **sí** se enruta por el `api-gateway` (`/api/v1/assistant/` → mcp-server), de modo que hereda su validación JWT y la inyección de `X-Tenant-Slug`. El mcp-server orquesta un **loop LLM (Claude)** que ejecuta las tools de **solo lectura** del catálogo como llamadas al gateway **re-propagando el token del usuario** — el asistente corre _as-the-user_, nunca con credenciales propias, y cada tool revalida su RBAC/clearance en el backend.

- `POST /api/v1/assistant/message` `{mensaje: string(1..4000), conversation_id?: string}` — envía un turno del usuario. Devuelve `{conversation_id, message: {id, role:"assistant", texto, formato:"markdown", ts}}`. El `conversation_id` (generado si se omite) mantiene el hilo; el historial es **efímero por proceso** y **aislado por tenant** (un `conversation_id` nunca cruza tenants). **El asistente ofrece las 207 tools, y las 75 de escritura NO se despachan**: al recibir una `tool_use` de escritura el servidor firma una propuesta y devuelve al modelo un `tool_result` que dice `ejecutado: false` (`assistant.py`). Ese anclaje importa: sin él, el modelo lee su propia `tool_use` como si hubiera surtido efecto y redacta «he radicado el documento», afirmando un hecho falso sobre un registro oficial — la regla de no-invención aplicada a sus propios actos. El token firmado **no entra en su contexto**; quien lo confirma es la persona, en `ProposalCard`. Los argumentos se recortan además al esquema declarado antes de despachar (una clave no declarada en el body no viaja, aunque el techo de clearance ya la impediría). El contenido devuelto por las tools (p. ej. radicados de Entrada redactados por terceros) es **dato, nunca instrucción** (frontera anti-inyección en el system prompt). `503 assistant_unavailable` si el asistente no está configurado (sin `ANTHROPIC_API_KEY`) o el SDK falla — **degradación honesta**, nunca una respuesta simulada (RF-FIR-15); `401` sin token; `409 conversation_limit` al agotar el tope de turnos del hilo. Respuestas de tool `403`/`404` se comunican de forma **genérica** ("no encontré ese registro"), sin revelar la existencia de registros clasificados (no-oráculo, RF-SEG-08 / Ley 1712/2014).

### Capa de conocimiento (E21, ADR-006) — knowledge-service, puerto 8011

Capa **advisoria/derivada y opt-in** (no es fuente de verdad). Embeddings con proveedor pluggable (stub local por defecto).

- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — calcula el embedding y persiste el fragmento (pgvector) con su ACL. Para proteger contenido clasificado, el ingestor debe incluir el nivel en `acl.nivel_seguridad` (1–3); ausente = PUBLICA (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — recuperación semántica (kNN coseno). **Control de acceso resuelto en el servidor (RF-SEG-08 / RF-BUS-04):** se devuelven **siempre** solo fragmentos con `nivel_seguridad ≤` el _clearance_ del llamante (derivado del JWT/BD, no del cuerpo de la petición); el `acl` opcional del cliente es solo _narrow_ adicional (p. ej. por dependencia), nunca amplía el acceso. Devuelve `[{id, source_type, source_ref, texto, metadata, score}]`. Los _tombstones_ (fragmentos purgados por reclasificación) quedan **excluidos** de la búsqueda.
- `POST /api/v1/knowledge/antecedentes` `{query|radicado_ref, source_type?, top_k?}` (XOR `query`/`radicado_ref`, E21 Inc.2, Patrón A) — recuperación pura ANCLADA y CITADA, sin LLM: reusa `search` (mismo pre-filtro ACL), devuelve `{antecedentes:[{source_type, source_ref, snippet, score}], advisory}` con un aviso **fijo** (CC-01/CC-02). `radicado_ref` inexistente **o** que excede el clearance del solicitante colapsan al mismo `404 antecedente_source_not_found` (anti-oráculo, sin 403 diferenciado).
- `POST /api/v1/knowledge/rag` `{query, top_k?}` (E21 Inc.3, Patrón B) — RAG generativo con citas. Recupera con el mismo pre-filtro ACL de `search` y, si `AI_PROVIDER` es un proveedor de generación real (`ollama_local` default soberano, `ollama_cloud`/`openai_compatible` externos, `anthropic` con Citations API nativa), sintetiza una respuesta ANCLADA. **Barrera dura de soberanía (sin override)**: con proveedor externo, excluye del contexto enviado al LLM los fragmentos `nivel_seguridad ≥ RESERVADA` — visibles por ACL, pero nunca egresados a un tercero; si no queda contexto generable, cae a recuperación pura (Patrón A) sobre todo lo recuperado. `AI_PROVIDER=disabled` (o no configurado) también cae siempre a Patrón A. Devuelve `{answer, citations:[{cited_text, source_type, source_ref, score}], proveedor, modelo, confianza, suggestion_id, advisory}` — `answer`/`modelo`/`suggestion_id` son `null` cuando no hubo generación real. Cada generación real se registra en `kb_suggestions` (CC-03: `proveedor`/`modelo` en columnas separadas) y en `audit_log`. `503 rag_unavailable` ante cualquier fallo del proveedor (SDK ausente, credencial ausente, error de red/API) — degradación honesta (RF-FIR-15), nunca una respuesta simulada. **`ollama_local` es soberano por su destino, no por su nombre (AREA-11-12)**: solo se trata como local si el host de `OLLAMA_URL` es loopback, de red privada, de contenedor o `*.local`; con un host público (`https://ollama.ejemplo.com`) el RAG excluye del contexto todo chunk `nivel_seguridad ≥ RESERVADA`, igual que con un proveedor externo.
- `POST /api/v1/knowledge/suggestions/{id}/aceptar` (AREA-11-11, CC-02) — **aceptación humana** de una sugerencia del RAG: la capa sugiere, la persona decide. Solo la acepta **quien la generó** (`kb_suggestions.creada_por`, migración `012`; la condición va en el `WHERE`): `404 suggestion_not_found` neutro si no existe, es de otra persona o es anterior a la migración (sin creador, no aceptable), `409 suggestion_already_accepted` si ya estaba aceptada, `401` sin identidad. Devuelve `{id, aceptada_por, aceptada_en}` —nunca el contenido— y deja el asiento `rag.aceptada` en `audit_log` en la **misma transacción** que la aceptación, con `payload = {aceptada_en, sha256_texto, proveedor, modelo, fuentes}`: la huella del texto aceptado, el proveedor/modelo y los `source_ref` citados (sin su contenido), para que el registro inmutable pruebe qué se aceptó.

> **Ingesta event-driven (E21, MVP):** además del `POST /ingest` manual, el knowledge-service **consume `orpycamcp.document.events`** (consumer group `knowledge-ingest` + DLQ, ADR-021) y auto-indexa cada radicado al crearse (`document.radicado.created` → `subject`/`sender`, `source_type="radicado"`, `source_ref=tracking_number`). El `nivel_seguridad` se **replica del evento** (fail-closed a CLASIFICADA si falta) y se **mantiene consistente ante reclasificación** (`document.radicado.reclassified`): una subida a RESERVADA+ con proveedor de embeddings externo **purga el contenido** (tombstone), y el nivel de un fragmento **nunca baja** salvo por una reclasificación explícita, sea cual sea el orden de llegada de los eventos. Material RESERVADA+ **no se embebe** con un proveedor externo (queda registrado en `audit_log`). Un radicado **anulado** (`document.radicado.annulled`) se marca _voided_ de forma **permanente** y queda excluido de la búsqueda a cualquier clearance (no se re-indexa aunque un evento de creación se reprocese después). Es una capa **derivada/advisoria** (ADR-006): no es fuente de verdad; el registro legal vive en document/archive.

### Webhooks salientes (E11, ADR-018)

- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — suscribe un endpoint externo (`event_types` vacío = todos los eventos). El `secret` se **guarda cifrado en reposo** (AES-256-GCM, KEK propia de notification-service, AAD de tenant+webhook) y **nunca se devuelve** (ni al crear ni en `GET /webhooks`); la firma `X-OrpycaMCP-Signature` de cada entrega se calcula descifrándolo solo en memoria. `503 webhook_secret_kek_unavailable` si el servicio no tiene una KEK utilizable al crear. Una suscripción cuyo secreto no se puede descifrar (KEK perdida, fila heredada aún sin cifrar) **no se entrega** hasta repararla. **Exige `USUA_PERM_ADMIN` en nivel 3** (crear/borrar; listar basta con nivel 1) y deja el asiento `notification.webhook.created` con el actor (`X-User-Id`, `401` si falta) en la misma transacción — el asiento guarda solo `scheme://host:port` más `url_sha256` (nunca credenciales, query ni **path**, donde Slack/Teams/Discord llevan el token) y nunca el `secret`; sin módulo de auditoría responde `503 audit_unavailable` y no se crea nada: registrar una suscripción entrega cada evento del tenant a la URL indicada, así que es config de administración — sin el gate sería un canal de exfiltración (y SSRF). **`422` nuevo** si `url` apunta a un destino interno/no enrutable (loopback `127.0.0.0/8`/`::1`/`localhost`, link-local `169.254.0.0/16`/`fe80::/10` — incluye el endpoint de metadata cloud, privado `10.0.0.0/8`/`172.16.0.0/12`/`192.168.0.0/16`/`fc00::/7`, un nombre de servicio de la red Docker interna de OrpycaMCP, o un esquema distinto de `http`/`https`): el `detail` explica el motivo concreto del rechazo, no un "URL inválida" genérico. Estricto por defecto; relajable solo en desarrollo con `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` en notification-service (kill-switch, nunca en producción).
- `GET /api/v1/webhooks` — lista las suscripciones (`X-Total-Count`). Exige `USUA_PERM_ADMIN`.
- `GET /api/v1/webhooks/{id}/entregas?page=1&size=20` — constancia de entregas de una suscripción (más recientes primero; `X-Total-Count`; `size` ≤ 200). Exige `USUA_PERM_ADMIN`. Cada intento deja **dos filas** inmutables (`webhook_entregas`, append-only): `fase=envio` —escrita ANTES del POST; si no puede escribirse, no se envía— y `fase=resultado` con `entregado` / `http_error` / `error_red` / `destino_rechazado` / `secreto_inutilizable` (estos dos últimos no llegan a enviar y solo dejan `resultado`). Campos: `entrega_id`, `webhook_id`, `url_destino` (solo `scheme://host:port`) + `url_sha256`, `event_id`, `event_type`, `intento`, `body_sha256` (nunca el cuerpo), `recortado`, `firmado`, `http_status`, `error` (≤ 500, sin URLs), `at`. No responde `404` si la suscripción ya no existe: la constancia sobrevive al borrado. Retención/purga: pendiente (ADR-018).
- `DELETE /api/v1/webhooks/{id}` — elimina una suscripción. Exige `USUA_PERM_ADMIN` nivel 3 y deja `notification.webhook.deleted` con el actor (misma URL reducida + `url_sha256`). `404 webhook_not_found` si no existe.
- **Contenido del webhook**: el sobre se recorta por el nivel efectivo (máximo de `nivel_seguridad`, `nivel_nuevo`, `nivel_anterior`): desde RESERVADA (2) se quitan asunto, remitente, observaciones y la ruta de dependencias (`from_dept`, `to_dept`, `assigned_to`, `dest_dept`, `dest_dept_code`, `origin_dept`); `causal_reserva`, `fundamento_juridico` y `plazo_reserva_meses` no salen nunca. Un evento `document.*`/`workflow.*` que no declara nivel sale mínimo (tipo, id, fechas). La entrega conecta a la IP validada (con el `Host` y el SNI originales) y no sigue redirecciones.
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

Los eventos que no se pueden procesar tras `max_deliveries` reintentos quedan en la tabla `evento_dead_letter` (por tenant) con `status='pending'`. Cada servicio consumidor expone una superficie admin para inspeccionarlos y resolverlos, gateada por el permiso **`PERM_DLQ_ADMIN`** (rol de plataforma; asignación restringida). Implementado en **signature-service, workflow-service, notification-service y knowledge-service** (este último, desde la tanda T9 / AREA-10-10: `/api/v1/knowledge/admin/deadletter`, con `replay`/`discard` a nivel 3 y replay de `document.radicado.created|reclassified|annulled|purged` en la misma transacción que la transición y el asiento; un tipo que knowledge no aplica da `422 unsupported_event_type`). `evento_dead_letter` es una **tabla física compartida**: cada servicio filtra por su `origin_group` para no operar sobre los dead-letters de otro, y su clave primaria es **`(origin_group, event_id)`** (T9 / AREA-10-07): el mismo evento aparcado por dos grupos deja una fila y un asiento `evento.dead_letter` por grupo; repetir el mismo par sigue siendo idempotente.

| Método | Ruta                                                                                                      | Descripción                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------ | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/v1/{signature\| Lista paginada (`X-Total-Count`). Solo cabeceras (sin payload de negocio) y **filtrada por clearance del llamante en el `WHERE`** (nivel VIVO del radicado referenciado; duda = nivel 3): una fila por encima de su nivel no aparece ni cuenta en el total, y el detalle/replay/descarte de esa fila responde el mismo `404` que un `event_id` inexistente. Página y total exactos, sin tope de candidatas; la cabecera `X-Truncated` se retiró. `503` si falta la tabla `radicados`. |notification\|knowledge}/admin/deadletter?status=pending&origin_stream=&page=&size=` | Lista paginada (`X-Total-Count`). Solo cabeceras (sin payload de negocio) y **filtrada por clearance del llamante** (no-read-up fila-a-fila sobre el nivel vivo del radicado referenciado, misma resolución que el detalle): una fila cuyo objeto excede el nivel del admin se omite por completo (no aparece ni cuenta en `X-Total-Count`), consistente con el `404` neutro del detalle. Fail-closed ante cualquier error de resolución. La paginación se aplica en la capa de aplicación, después del filtrado. El conjunto de candidatas se acota a `1000` antes de filtrar; si hay MÁS de `1000` (el repositorio pide `1001` para distinguirlo de «exactamente `1000`») la respuesta incluye el header **`X-Truncated: true`** (advierte que `X-Total-Count` puede sub-reportar); con exactamente `1000` no se emite. |
| `GET`  | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}`                                | Detalle (sobre íntegro + fallo). Gateado por clearance (no-read-up sobre el nivel vivo del radicado; `404` **neutro** si insuficiente o inexistente —cuerpo byte-idéntico—). Una vista autorizada deja un asiento `evento.dead_letter.viewed` en `audit_log` (una vista bloqueada NO audita, para no confirmar existencia).                                                                                                                                                                                                                                                                                                                                                                                      |
| `POST` | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}/replay`                         | Reprocesa el evento. `200` → `status='resolved'`; `409` si no está `pending`; `404` si no existe o sin clearance. **knowledge**: ancla `reclassified`/`created` al nivel vivo del radicado; `409 stale_event` si ya no existe. **signature/workflow**: reproceso idempotente y atómico; `502` si falla (queda `pending`). **notification** (correo no transaccional): _best-effort claim-then-send_ —limpia el dedup y reenvía—; `422` si el `origin_stream` es desconocido o el tenant no cuadra, `502` si el envío falla (queda `pending`).                                                                                                                                                                                                                                                                                 |
| `POST` | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}/discard`                        | Cierra como `discarded`. Body `{ "reason": "..." }` (obligatorio, ≥10 caracteres tras `strip`). Mismos códigos.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

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

**Cada ítem exige los datos mínimos del radicado (T13 / AREA-09-02):** `doc_class` y, según el tipo, `dest_dept_code` (E, I) u `origin_dept_code` (S). Un ítem que no los trae rechaza **toda la petición** con `422`; el nombre de la dependencia se resuelve contra la tabla `dependencias` del tenant (una inexistente o inactiva deja el ítem en `failed` **antes** de gastar consecutivo). Un lote encolado antes de este cambio, sin códigos, termina con esos ítems `failed` (reprocesables solo tras corregirlos).

El procesamiento corre en una tarea de fondo **con su propia conexión del pool** — nunca la de la petición, que se devuelve al pool en cuanto se responde (ver `acquire_tenant` en `app/core/database.py`). Un job que no puede procesarse queda en `failed` con su motivo en `error_message`: `pending` significa que sigue en cola, no que algo salió mal. `pages` es opcional en la petición y se registra como `0` cuando no se envía, igual que en el camino síncrono. **Desde T4** cada ítem se valida contra la plantilla de metadatos activa de su `doc_class` (si no encaja, el ítem queda `failed` con `error_tipo: MetadataValidationError`), el radicado guarda `metadata_template_id` y el año del número es el de la zona horaria de radicación, no el UTC.

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

Monitorea el progreso del job y expone el **detalle de errores por ítem**. Requiere `PERM_RADI`. **Alcance (auditoría 2026-10):** solo lo ve **quien creó el lote** o quien tiene `PERM_RADI` con nivel CRUD >= 3; para cualquier otro el lote «no existe»: `404 {"error": "lote_no_encontrado", "detail": "No existe el trabajo solicitado", "status": 404}` (el mismo cuerpo que un id inventado; **antes `detail` era una cadena**). Pide `X-User-Id` (`400` sin él). **No-read-up del `subject`:** el asunto de un ítem cuyo radicado quedó por encima del nivel de acceso del llamante (p. ej. se reclasificó a reservado después) viene `null` (igual que su `nivel_seguridad`, campo nuevo por ítem: `null` si no hay radicado o no se alcanza); el de un ítem sin radicado (pendiente o fallido) es el que envió el propio llamante.

```json
// Response 200
{
  "job_id": "uuid",
  "job_type": "documents",
  "status": "completado_con_errores", // pending | processing | completed | completado_con_errores | failed
  "total_items": 3,
  "processed_items": 2,   // ítems en 'success' (recontados desde los ítems)
  "failed_items": 1,      // ítems en 'failed'
  "pending_items": 0,     // faltantes: aún sin procesar (lote interrumpido)
  "reprocesable": true,   // hay fallidos/faltantes Y el lote ya terminó -> ofrecer «Procesar los faltantes»
  "reanudaciones": 0,     // veces que el barrido retomó este lote tras un reinicio
  "reprocesado_at": null,
  "items": [
    { "item_index": 0, "status": "success", "result_id": "uuid-radicado", "subject": "Solicitud 1", "intentos": 1 },
    { "item_index": 1, "status": "failed", "subject": "Solicitud 2", "intentos": 1,
      "error_message": "Falta el campo obligatorio expediente_ref", "error_tipo": "MetadataValidationError" },
    { "item_index": 2, "status": "success", "result_id": "uuid-radicado", "subject": "Solicitud 3", "intentos": 1 }
  ],
  "errores": [ /* subconjunto de `items` con status = 'failed', mismas claves */ ]
}
```

Estados del lote: `pending` (en cola o interrumpido; lo retoma el barrido), `processing`, `completed` (sin fallos), **`completado_con_errores`** (terminó, hay ítems `failed`: ver `errores`), `failed` (reservado al lote que reventó **fuera de los ítems**, con su motivo en `error_message`). Estados del ítem: `pending` | `success` | `failed`. **Contrato para la UI**: mostrar `errores[]` (asunto + `error_message`), y cuando `reprocesable` sea `true` ofrecer «Procesar los faltantes» → `POST .../reprocesar`; mientras el lote esté `pending`/`processing` no se ofrece (lo retoma el barrido solo).

### `POST /api/v1/batch/documents/{job_id}/reprocesar`

Reprocesa **solo** los ítems `failed` (y los `pending` faltantes) del lote. Exige `PERM_RADI` **y** ser quien creó el lote, o tener `PERM_RADI` con nivel CRUD >= 3 (auditoría 2026-10: antes bastaba cualquier `PERM_RADI`). Para cualquier otro el lote no existe: `404 lote_no_encontrado`, y se comprueba **antes** de tomar el lock, así que un tercero no distingue «existe» por un `409 lote_en_proceso`. Retorna `202`:

```json
{ "job_id": "uuid", "status": "pending", "items_a_procesar": 1 }
```

**Idempotente y sin doble numeración**: los ítems ya radicados (`success`) no se tocan y conservan su número; cada ítem se procesa en su propia transacción (el consecutivo, el radicado, la marca de éxito y el asiento van juntos, o ninguno), de modo que un ítem que falla no deja hueco y uno interrumpido no deja nada. Deja el asiento `document.lote_reprocesado` (actor = `X-User-Id`; los radicados nuevos de ese reproceso se registran a su nombre) con, por cada ítem fallido, `item_index`, `error_tipo`, **`error_message`** (truncado a 500) e **`intentos`**: la fila los limpia al volver a `pending` y el asiento es lo único que conserva por qué falló. Errores: `404 lote_no_encontrado`, `409 lote_en_proceso` (otro proceso lo está procesando ahora), `409 nada_que_reprocesar` (sin fallidos ni faltantes). _(La ruta va bajo `/documents/` para que el api-gateway la reenvíe a document-service por prefijo, como el `status`.)_

**Reanudación y varias réplicas (AREA-09-15).** El lote ya no depende del proceso que lo encoló: un **barrido** (al arrancar, tras 20 s, y cada 5 min) retoma los lotes `pending`/`processing` con más de 120 s que nadie procesa (`document.lote_reanudado`; **el asiento va a nombre del sistema** —`actor` = `00000000-0000-0000-0000-000000005ec1`, `canal` = `system`— con `en_nombre_de` = quien encoló el lote en el payload, y lo mismo vale para los `document.radicado_created` de los ítems que radica esa reanudación; el `registered_by` del radicado sigue siendo el creador del lote), y retira los `completed` sin errores con más de 90 días (`BATCH_RETENCION_DIAS`; los `completado_con_errores` y `failed` se conservan para revisarlos). Cada lote se procesa bajo un **advisory lock de sesión** por (tenant, lote): dos réplicas no lo procesan a la vez, y si el proceso muere el lock cae con su conexión. Configuración: `BARRIDO_ENABLED`, `BARRIDO_INTERVALO_SEGUNDOS`, `BATCH_REANUDAR_DESPUES_SEGUNDOS`, `BATCH_RETENCION_DIAS`.

> **Auditoría (cerrado 2026-08).** La creación masiva de radicados deja un
> asiento `document.radicado_created` en `public.audit_log` **por cada
> radicado creado con éxito** (no uno por lote) — espejo de
> `POST /api/v1/documents/` (creación individual) y de `batch/expedientes`
> en archive-service. `actor` es el `X-User-Id` que validó el router al
> encolar el job, nunca un campo del body. El payload lleva `origen: "lote"`, `job_id` e `item_index`
> (el lote y sus ítems se retiran a los 90 días; el asiento no, y sin esto un radicado de lote era
> indistinguible de uno radicado a mano). Cada ítem se procesa en su propia
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

- `GET/POST/DELETE /api/v1/plantillas` — plantillas reutilizables (`nombre`, `cuerpo`, `tipo_documental`). **`POST` valida los marcadores de combinación**: un cuerpo con un marcador que no existe en el vocabulario se rechaza con `422 marcador_desconocido` y la lista de los que sobran (E23, RF-PRO-01). El rechazo ocurre **al guardar**, no al usar: en el legado un marcador mal escrito no fallaba nunca y acababa impreso en el oficio que recibía el ciudadano.
- `GET /api/v1/plantillas` lleva gate any-of `PERM_RADI` o `USUA_PERM_ADMIN` (B6) y acepta `?todas=true`, que **exige `USUA_PERM_ADMIN`** (`403` si falta). Sin él, el listado se recorta a las plantillas que el llamante alcanza desde sus dependencias (E23, RF-PRO-06): globales, más las asignadas a alguna de sus URD activas. No es una restricción de seguridad —una plantilla no contiene datos de nadie— sino de uso: con cuarenta en la lista, la que hace falta no se encuentra. Un usuario **sin URD activa ve solo las globales**, no todas: el vacío no es un atajo a «sin restricción». Un recorte que el propio llamante pudiera apagar con un parámetro no sería un recorte, de ahí el permiso.
- `PUT /api/v1/plantillas/{id}/alcance` `{es_global, dependencias[]}` — a qué dependencias alcanza una plantilla (paridad con `sgd_plan_plantillas.depe_codi`). Requiere `USUA_PERM_ADMIN`. Poner `es_global=true` **borra** la lista: mantenerla guardada dejaría una asignación invisible que reaparecería al volver a restringir. Restringir a **ninguna** dependencia es un estado válido — retira la plantilla de circulación sin borrar los documentos ya producidos con ella.
- `POST /api/v1/plantillas/importar` (multipart, `file`) → `{cuerpo, perdido[], marcadores_desconocidos[]}` — importa un `.docx`/`.odt` como cuerpo de plantilla (RF-PRO-07, paridad con `import_docx_editor.php`). Requiere `USUA_PERM_ADMIN`. **No guarda nada**: devuelve el cuerpo para revisarlo antes de crear la plantilla, y con él los marcadores que rechazarían el guardado — dichos antes, no después.
  **Mejor esfuerzo declarado**: conserva párrafos, negritas, listas y tablas del `.docx`; del `.odt` solo el texto por párrafos. `perdido[]` dice qué no llegó — encabezados y pies de página **no se importan a propósito**: los pone la plantilla de página del sistema (RF-PRO-04), y traérselos duplicaría el membrete en cada oficio. Una importación que promete fidelidad total y no la da es peor que una que avisa.
  Se acota el tamaño del fichero **y el descomprimido**: unos pocos KB comprimidos pueden expandirse a gigabytes, y esa segunda defensa es la que importa. `.doc` binario y `.rtf` no se admiten (exigirían LibreOffice, decisión D-1).
- `GET /api/v1/plantillas/campos` — **vocabulario de combinación**: los marcadores admitidos con `nombre`, `marcador` (`*NOM_R*`), `alias` (`{{nom_r}}`), `descripcion`, `grupo` y `diferido`. Requiere `PERM_RADI` **o** `USUA_PERM_ADMIN` (any-of, B6: el administrador de plantillas no necesita radicar). Los dos campos `diferido` (`RAD_S`, `F_RAD_S`) no existen hasta consumir el número y se resuelven en una segunda pasada al radicar.
- `POST /api/v1/plantillas/{id}/combinar` `{radicado_padre_id?, dest_dept_code?}` — devuelve `{cuerpo, faltantes[]}`: el cuerpo de la plantilla con los campos **ya resueltos en el servidor** y la lista de marcadores que quedaron sin valor. Requiere `PERM_RADI`. **No-read-up (RF-SEG-08):** el radicado padre se lee con el _clearance_ del llamante; si lo supera, sus campos salen en `faltantes` igual que si no existiera — sin ese gate, combinar sería una vía de exfiltración, porque el asunto del padre acaba escrito dentro del cuerpo.
- `POST /api/v1/borradores` `{doc_type, subject, dest_dept_code?, dest_dept?, cuerpo?, plantilla_id?, radicado_padre_id?}` — crea un borrador. El destino se guarda por **código** (`borrador.dest_dept_code`, migración document `046`; `dest_dept` es el alias deprecado y el nombre guardado es el del catálogo; `PATCH` reescribe el par entero). Un nombre ambiguo es `422 dependencia_ambigua`; un destino que no es dependencia (Salida a un tercero) se conserva como texto sin código. Si se da `plantilla_id` y no `cuerpo`, el cuerpo se **combina** contra el contexto (radicado padre, remitente, dependencia, fechas) en vez de copiarse literal. Con `plantilla_id` la combinación se ejecuta **siempre**, aunque el `cuerpo` venga puesto: es de ahí de donde salen los `faltantes[]`, que se **guardan** en el borrador. El cliente NO los envía — es un dato que acabaría impreso en un documento que da fe, y no puede depender de quien llama. El `cuerpo` que se almacena sigue siendo el del cliente, que ha podido editarlo.
- `GET /api/v1/borradores` (`?estado=`), `GET /{id}`, `PATCH /{id}` (editar solo si no radicado). **No-read-up (RF-SEG-08)**: el borrador **hereda `nivel_seguridad`** del radicado padre (sin padre, 1; `BorradorResponse.nivel_seguridad`). Listar, leer, editar, aprobar, devolver, previsualizar y radicar filtran por nivel en el `WHERE` (nivel efectivo = el mayor entre el heredado y el vigente del padre, de modo que reclasificar el padre cierra también sus borradores); `404` neutro si no se alcanza. Crear sobre un padre inalcanzable da `404` (antes `201` con contexto vacío). `PATCH` asienta `borrador.editado` en la misma transacción.
  **Quién puede EDITAR (`PATCH /{id}`, AREA-01-N2, 2026-10-02)**: `PERM_RADI` (gate de la ruta) ya **no basta**; además hay que **tener el radicado**. Si el borrador responde a un radicado padre, lo edita quien lo tiene según la tenencia de ADR-028 (el paso de flujo activo asignado al actor, o sin asignar y de una dependencia suya); sin paso activo, quien poseía el último paso completado; sin ningún paso, quien radicó el padre (R16). Sin padre, su creador. Quien no lo tiene recibe el mismo `404` neutro que un borrador inexistente (sin tocar la fila) y la regla va en el `WHERE` del `UPDATE`. **`aprobar` y `devolver` NO exigen tenencia** a propósito: son actos de revisión (aprobar suele ser de otra persona y está atado al circuito de firma); siguen con `PERM_RADI` + _clearance_ + estado + circuito. **`radicar` SÍ exige la misma tenencia** (auditoría 2026-10, Acuerdo AGN 060/2001): es el acto irreversible —consume un número inmutable—, así que quien no tiene el borrador recibe el `404` neutro, sin consumir número ni tocar la fila (la tenencia va en el `WHERE` de la lectura). Y el `UPDATE` de `PATCH` lleva `estado <> 'radicado'`: si el borrador se radica entre la guarda y la escritura, responde `409 borrador_radicado` y no edita.
- `POST /api/v1/borradores/{id}/aprobar` (`borrador → aprobado`) — **el circuito de aprobación ata** (RF-PRO-09). Antes de marcar el estado se consulta a signature-service si hay un circuito de visto bueno abierto sobre este borrador; si lo hay, `409 circuito_pendiente`. El gate es **vinculante si existe, no obligatorio**: sin circuito abierto se aprueba directo como siempre — hay entidades donde una salida no pasa por visto bueno, y exigirlo a todas convertiría en requisito un paso que nadie pidió. Lo que no puede ocurrir es abrir un circuito deliberadamente y luego saltárselo: sin este gate el visto bueno de los funcionarios era decorativo, y el gate `documento_modificado` que protege cada turno no protegía nada, porque nadie estaba obligado a pasar por él.
  **`503 circuito_no_verificable` si signature-service no responde, y no se aprueba.** Suponer «no hay circuito» reabriría el agujero por la puerta de atrás, justo cuando el sistema está degradado; la única consecuencia de no saber es no poder aprobar.
  Resuelto el último turno, signature-service deja el borrador aprobado por su cuenta (`POST /api/v1/internal/documentos/borradores/{id}/aprobar`, sellado con `X-Internal-Token`, idempotente y solo desde `borrador`). Ese aviso es **best-effort a propósito** y puede serlo sin abrir ninguna ventana: si se pierde, el botón vuelve a estar disponible porque la consulta ve el circuito ya cerrado. Se pierde un clic, no la garantía — la autoridad es la consulta, no el aviso.
- `POST /api/v1/borradores/{id}/previsualizar` → **`application/pdf`** con la cabecera `X-Faltantes` (lista separada por comas): los campos **guardados** al combinar la plantilla más los que sigan sin resolver en el texto de ahora. Sin los guardados esta cabecera salía siempre vacía — el cuerpo se almacena ya combinado, así que los huecos son cadenas vacías y no quedan marcadores que reportar. Combina y renderiza **sin persistir nada**: es lo que permite ver el documento antes de gastar el número. `*RAD_S*` se queda **a la vista** a propósito — sustituirlo por un número de ejemplo enseñaría un documento que no es el que se va a producir. `503 render_no_disponible` / `422 render_fallido`.
- `POST /api/v1/borradores/{id}/devolver` → devuelve un borrador **aprobado** a edición (`aprobado → borrador`). `409` si ya está radicado o si no estaba aprobado. Sin esta vuelta el ciclo era de una sola dirección y el texto quedaba congelado al aprobar, así que un circuito de firmas no se podía revertir por cambios en el documento. **No toca el circuito**: reabrir no invalida nada por sí mismo — devolver y volver a aprobar sin tocar una letra deja las aprobaciones intactas. Lo que las tira es **editar el texto**, porque el disparador recalcula `cuerpo_sha256` y la cadena deja de casar.
- **El circuito abierto también bloquea aquí** (RF-PRO-09): `409 circuito_pendiente` si quedan turnos sin resolver, y el gate va **antes** de combinar, renderizar y pedir el consecutivo. Poner el gate solo en `/aprobar` no bastaba: el circuito se abre sobre un borrador **ya aprobado** —ese es el orden del trámite—, así que la vía real para saltárselo era radicar, no aprobar dos veces. Y radicar es peor: aprobar de más se deshace devolviendo a edición; un número consumido no se devuelve nunca.
- `POST /api/v1/borradores/{id}/radicar` `{tipo_documental?, origin_dept_code?, dest_dept_code?, metadata?}` (cuerpo **opcional**, T13 / AREA-09-02; `metadata` B6/23: valores del tipo, validados contra su plantilla activa de metadatos; un incumplimiento es `422 metadata_validation_failed` con `field` —antes era un 500— y **no** consume número; `from_borrador` lo pone el sistema y el cuerpo que lo traiga se rechaza) — el borrador no guarda tipo ni dependencias, y el radicado los exige (Ac. AGN 060/2001 §5.2): lo que no se envíe se **deriva** (tipo de la plantilla; destino por el `dest_dept_code` del borrador (o, en uno anterior a la `046`, por el único candidato de `dest_dept`: **dos homónimas activas son `422 dependencia_ambigua`**, ya no «la de menor código»); origen de la Salida = la dependencia de quien radica: la que recibió el padre si es suya, si no la de menor código entre sus URD activas) y lo que se envíe **manda**. Sin dato derivable → `422 datos_minimos_incompletos` con los `campos` que faltan, **antes** de consumir el número; una dependencia suprimida → `400 invalid_dependencia`. Genera el radicado real **con su PDF/A anexado** como soporte principal (`tipo_anexo='documento_principal'`, `es_principal=true`) y marca el borrador `radicado`. **Nivel heredado (T3):** el radicado nuevo toma el nivel **vigente** del padre (el mayor entre la instantánea del borrador y el del padre), su fundamento, causal y plazo de reserva —si el padre no tiene fundamento se motiva por herencia: nunca cae a PUBLICA— y el vínculo `responde_a`. `aprobar` (por persona, por circuito completado y por la ruta interna) y `devolver` dejan los asientos `borrador.aprobado` / `borrador.devuelto` en la misma transacción que el cambio de estado. **B6**: en una Salida, `origin_dept_code` debe ser una dependencia donde el usuario tiene URD activa (`422 origen_fuera_de_sus_dependencias`; sin URD activa tampoco se admite); el tipo y los metadatos se validan **antes** de renderizar y subir el documento (un 422 no deja binario huérfano en el almacén).
  **El orden importa y es la garantía del endpoint**: combinar → renderizar → subir → **número** → segunda pasada `*RAD_S*` → anexar. El render va **antes** de pedir el número para que un fallo no consuma un consecutivo: un número de radicado es inmutable por ley y un hueco en la secuencia es permanente. Sin renderizador se responde `503` y **no se radica** — degradar a «radicar sin documento» dejaría a la entidad creyendo que produjo un oficio que no existe.
  Solo se renderiza dos veces si el cuerpo usa `*RAD_S*`/`*F_RAD_S*`, que no existen hasta consumir el número.
  El contexto con que se combinó queda **congelado** en `borrador.combinacion`, y la huella del documento en `borrador.documento_sha256`: cambiar después la plantilla o la ficha de un tercero **no** reescribe lo ya producido (mismo criterio que la dirección de un envío).

### Generación del documento de salida — storage-service (E23)

- `POST /api/v1/render/documento-internal` `{cuerpo_html, pagina{}, perfil?}` → `{formato, herramienta, contenido_b64, bytes}` — genera el documento en **PDF/A** a partir del cuerpo **ya combinado y ya saneado**. **Ruta interna sellada con `X-Internal-Token`** (D-02), no gateada por permiso: el único llamante es document-service, que ya autorizó al funcionario antes de componer el cuerpo — un endpoint inter-servicio se sella con token, no se gatea por permiso.
- El documento **nace en formato de conservación** (D-2): se genera PDF/A-2b directamente, no PDF más una migración posterior. El barrido de preservación existe para el fondo heredado, no para lo que la entidad produce hoy.
- El membrete, el pie con los datos de la entidad y la numeración `Pagina N de M` los pone la **plantilla de página** del renderizador, no el editor (RF-PRO-04; Ac. AGN 060/2001 art. 10; GTC 185): no pueden quedar a merced de que el funcionario se acuerde de escribirlos.
- `503 render_no_disponible` cuando el despliegue no declara `RENDER_ENGINE`. **No hay stub y es deliberado**: un validador puede decir honestamente «no evaluado», pero un renderizador falso tendría que inventarse el documento. Un oficio que la entidad cree haber producido y no existe es peor que un error visible, porque el fallo aparece cuando el ciudadano reclama.
- `422 render_fallido` si el perfil no es producible (las variantes `a` exigen texto etiquetado que un render automático no puede prometer). Un resultado fallido **nunca lleva bytes dentro**.

### Respuesta rápida (paridad legado)

- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, dest_dept_code?, origin_dept_code?, doc_class?, observations?, response_days?}` — (T13 / AREA-09-02: la Salida exige origen y tipo; si el cuerpo no los trae **hereda** del antecedente `origin_dept_code = dest_dept_code de la Entrada` y `doc_class`; sin dato heredable → `422 datos_minimos_incompletos`; una dependencia heredada ya inactiva → `400 invalid_dependencia`, no `500`) crea un radicado de **Salida** en respuesta a este (hereda asunto `RE: …` y dependencia si se omiten), enlazado al antecedente (`responde_a`). Requiere permiso `PERM_RESPUESTA_VINCULADA`. `403 forbidden` si falta. Devuelve el nuevo radicado. **No-read-up (RF-SEG-08):** `404` neutro si el antecedente no existe o supera el _clearance_ del llamante — precisamente porque la Salida **hereda el asunto** del antecedente, responder sin ese gate exfiltraría el asunto de una Entrada reservada a un documento legible.
- `GET /api/v1/documents/{id}/respuestas` — salidas que responden a este radicado. **No-read-up (RF-SEG-08):** el _clearance_ del llamante acota qué salidas se listan (_fail-closed_ a PUBLICA sin identidad) y cada ítem trae `nivel_seguridad`, para que la barrera de residencia del asistente recorte el asunto en niveles 2-3.

### Circuito de visto bueno y firma (E23 Inc.4)

En una entidad varias personas dan visto bueno y **una** firma: la que compromete a la entidad y produce el documento definitivo. Los dos actos viven en **una sola cadena ordenada** (`firma_cadena.rol` ∈ `visto_bueno` | `firma`) y tienen endpoints distintos — presentarlos igual atribuiría a un revisor un compromiso que no adquirió.

- `POST /api/v1/signature/requests` acepta ahora `roles[]`, paralelo a `firmantes[]`. Si se omite, todos los turnos son de `firma` (así eran todas las cadenas antes de E23). `422` si las dos listas no casan —un desajuste repartiría los roles corridos y el revisor quedaría de firmante— o si no hay ningún turno de `firma`.
- **`payload_sha256` es obligatorio** para los `objeto_tipo` con fichero propio —hoy solo `borrador`—. Sin huella, la firma recaería sobre la cadena `"tipo:id"` —el **nombre** del objeto— y **seguiría siendo válida después de que el texto cambie**. En la práctica **no se le pide al llamante ninguna**: la del `borrador` la resuelve su productor y la del cuerpo se ignora, así que el `422` por huella ausente ya no es alcanzable — y ese es el estado correcto, porque pedírsela al llamante es pedirle que se la invente. Todo tipo que exija huella debe tener quién se la resuelva; los dos conjuntos están atados por guardián.
- Para `borrador`, además, **la huella la resuelve el servidor** contra document-service y la del cuerpo se **ignora**: una huella que el llamante elige no vincula a nadie — bastaría con enviar la del documento anterior para que la cadena siguiera pareciendo válida tras editar. Es el gemelo exacto de la resolución autoritativa del nivel de seguridad. `409 documento_sin_producir` si el borrador todavía no tiene PDF/A; `503` si no se puede resolver (una cadena con la huella equivocada es peor que ninguna: parece que protege).
- `POST /api/v1/signature/requests/{id}/vobo` `{comentario?}` — resuelve un turno de `rol='visto_bueno'`. No produce `firma` ni genera el documento definitivo. El comentario es **opcional**, al revés que el motivo de un rechazo: aprobar no exige explicación, detener el trámite de otro sí. `409 turno_es_de_firma` sobre un turno de firma — resolverlo por aquí sería la vía para comprometer a la entidad sin firmar.
- **El segundo factor no se exige al visto bueno** (D-6): lo obligatorio siempre es el **anclaje a la huella**, que es lo que convierte «aprobó» en «aprobó *este* texto». Exigir credencial de firma y 2FA a cada revisor es viable en una entidad de tres personas y no en una de cuarenta; la huella más `audit_log` ya prueban qué vio cada uno. El firmante final **sí** firma con PKI: es quien compromete a la entidad.
- **Firmar o aprobar sobre un texto modificado da `409 documento_modificado`.** El gate relee la huella vigente y compara. Corre **después** de las guardas de pertenencia y turno y **antes** de la transacción y del segundo factor: así ningún 2FA se consume en un acto que se va a rechazar igual —que es la razón de fondo de dónde va— y a la vez un actor ajeno a la cadena recibe `403 no_es_firmante` y no el estado del documento con su número de versión. **`POST /challenge` corre este mismo gate** (B7): antes emitía y enviaba el OTP sobre un texto ya modificado. No poder comprobar **deniega** (`503`): si un fallo de red se leyera como «no ha cambiado», bastaría con que document-service estuviera caído para poder firmar sobre un texto ya editado.
- `GET /api/v1/internal/documentos/borradores/{id}/huella` (document-service, `X-Internal-Token`) — huella vigente del cuerpo y del documento. **Content-free** (dos huellas, una versión y un booleano), por eso no lleva gate de clearance: no hay nada que recortar por nivel de seguridad.
- `POST /api/v1/signature/internal/circuitos/{objeto_tipo}/{objeto_id}/invalidar` (signature-service, `X-Internal-Token`) — deja sin efecto los circuitos vivos de un objeto **cuyo texto cambió**. Lo llama document-service al editar un borrador ya sometido a firma. **Rehacer un documento firmado es el trámite, no un abuso**: se recogen los vistos buenos, alguien recuerda un anexo o corrige una cifra, y hay que volver a empezar; lo que no puede quedar en pie son las firmas anteriores, dadas sobre otro texto.
  **La garantía dura no vive aquí**: es la comparación de huellas al radicar (`documento_modificado`), que se niega aunque este aviso se pierda. Esto es lo que hace **humano** el trámite: sin ello, los turnos de un circuito superado seguían apareciendo en la bandeja de quien los tenía, pidiéndole firmar un documento que ya no existe, y desaparecían después sin explicación — «se esfumó» sin motivo es lo que hace que la gente desconfíe de la herramienta. Por eso es **best-effort** e **idempotente**: sobre un objeto sin circuitos vivos no hace nada y devuelve `{invalidadas: 0}`. Busca **por objeto, no por el tipo que declare quien pregunta** — el UUID identifica solo, y filtrar por ambos dejaba invisible una cadena declarada con otra etiqueta sobre el mismo borrador.

### Anulación de radicado en dos pasos (paridad legado)

La ley prohíbe borrar radicados: se anulan con aprobación supervisora.

- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — solicita (paso 1). Requiere permiso `PERM_SOL_ANULAR`. `403 forbidden` si falta. `409` si ya está anulado o hay solicitud pendiente. **Las cuatro rutas de anulación** aplican no-read-up (RF-SEG-08): un radicado que supera el _clearance_ del llamante devuelve el mismo `404` que uno inexistente — la causal y el motivo describen el documento.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — aprueba (paso 2; nivel jefe): el radicado pasa a `anulado` (el número se conserva) y **su paso de flujo activo pasa a `cancelled`** en la misma transacción (evento `flow_events.tipo_tx=anulacion`; sin ello el paso quedaba como un zombi en bandeja y vencidos). Requiere `PERM_PANU_CODI`. `403 forbidden` si falta.
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rechaza. Requiere `PERM_PANU_CODI`. `403 forbidden` si falta. `GET /api/v1/documents/{id}/anulacion` — última solicitud.
- `GET /api/v1/anulaciones?page&size` — **cola del aprobador**: solicitudes en estado `solicitada`, la más antigua primero (FIFO — una anulación pendiente bloquea el ciclo de vida del documento). Requiere `PERM_PANU_CODI`, el **mismo** permiso que decidir. Cada fila trae los datos de la solicitud y los del radicado (`tracking_number`, `subject`, `doc_type`, `radicado_status`). `X-Total-Count` con el total. **No-read-up (RF-SEG-08) aplicado por igual a las filas y al recuento**: contar sin filtrar delataría cuántas solicitudes hay sobre radicados que el llamante no puede ver.

**Ventanilla única pública** (sin autenticar):

- `GET /api/v1/public/{tenant}/ventanilla/politica` — (B2, ADR-031) **política de tratamiento de datos vigente de la entidad**: `{publicada, version, url, texto, huella}`. La política es un parámetro de la entidad (`politica_tratamiento_version`/`_url`/`_texto`, migración tenant `012`, editable por `PUT /config/params/{key}`, administrador nivel 3). `url` solo se devuelve si es `http(s)://` (el `PUT` de `politica_tratamiento_url` rechaza otro esquema con `422 url_invalida`). Un tenant inexistente responde `publicada=false` igual que `/estado`. `publicada=false` si falta la versión o si hay versión pero ni URL ni texto (un aviso que no apunta a nada no informa). `huella = sha256(version ␞ texto ␞ url)` la calcula el **servidor**. **Lectura estricta**: si tenant-service no responde, **`503 politica_no_verificable`** (un error no es «sin política»).
- `GET /api/v1/public/{tenant}/ventanilla/estado` — (T13 / AREA-09-07) **verificación previa**: `{habilitada, dependencia_receptora, tipos_admitidos, politica_publicada}`. `politica_publicada` (B2) **no** forma parte de `habilitada`, que sigue siendo organigrama + catálogo; sin política, `POST` responde 409. `503 politica_no_verificable` si tenant-service no responde. `habilitada` es la MISMA condición que `radicar` aplica por dentro (una dependencia activa con `depe_rad_tp2` **y** al menos un tipo admitido), calculada sin radicar nada; un tenant inexistente responde `habilitada=false`. `scripts/verificar-ventanilla.sh` la recorre por entidad y sale ≠ 0 si alguna no está habilitada **o no tiene política publicada** (para engancharlo al despliegue).
- `GET /api/v1/public/{tenant}/ventanilla/tipos` — qué tipos de trámite declara recibir la entidad (`tipos_documentales.admite_ventanilla_publica`, **opt-in**). No expone el catálogo interno.
- `POST /api/v1/public/{tenant}/ventanilla` — radicación externa. El tipo debe estar declarado admisible (**422 `tipo_no_admitido_en_ventanilla`** si no; nunca se reclasifica en silencio). Se enruta a la dependencia con `depe_rad_tp2` («habilita radicación web»); si la entidad no ha declarado ninguna, **503 `ventanilla_no_habilitada`** — entrar sin destino dejaría el documento fuera de toda bandeja. El remitente puede ser persona **o entidad** (`sender_entity`). Quien se identifica debe indicar un medio de respuesta; la radicación **anónima** es legítima. Devuelve número de radicado, código de verificación y dependencia destino.
  **Multipart**: los campos van en la parte `datos` (JSON) y los anexos en partes `anexos`. Una sola puerta para el caso simple y para la tutela con soportes. Límites: **5 archivos, 10 MB cada uno, 25 MB el conjunto**; solo PDF, JPG, PNG y TIFF, **comprobados por el contenido** —no por la extensión ni por el `Content-Type`, que los escribe el cliente— y el MIME que se guarda es el deducido. **No hay antivirus.** Los anexos se trasladan ANTES de asignar número: si el almacén falla, **503 `anexo_no_almacenado`** y no se radica nada, porque un número de radicado es inmutable y no puede quedar emitido sin sus soportes. El fichero se guarda sin actor (`uploaded_by = NULL`).
- **Autorización de tratamiento, honeypot e idempotencia de la radicación pública (T13 / AREA-09-08, AREA-02-07).** `POST /ventanilla` y `POST /pqrs` exigen `acepta_tratamiento: true` (`Literal[True]`: no hay valor «falso» válido → `422`) y `politica_version` (1-50 caracteres): **la versión que el cliente mostró** (la de `GET /ventanilla/politica`). **El servidor relee la política vigente** (B2, ADR-031): sin política publicada → **`409 politica_no_publicada`** (fail-closed: sin aviso de privacidad no hay autorización informada, Ley 1581 art. 12); `politica_version` distinta de la vigente → **`409 politica_desactualizada`** (el cuerpo trae `politica_vigente`); tenant-service caído → **`503 politica_no_verificable`**. Todo se valida **antes** de subir anexos, de reclamar la `Idempotency-Key` y de consumir el consecutivo (un 409/503 no deja rastro); un reintento idempotente de un radicado ya hecho devuelve lo ya radicado sin releer la política. El servidor persiste la **constancia v2** en `metadata.consentimiento = {norma: "Ley 1581/2012", version_constancia: 2, politica_version, politica_huella, politica_url, instante, ip, ip_verificada}` **y** en el payload del asiento `document.radicado_created` (el que tiene valor probatorio). **Versión, huella y URL salen del servidor**: lo que el cliente mande como huella o URL se ignora. **`ip` es la que el gateway FIRMÓ** (`X-Real-Ip` + `X-Real-Ip-Ts` + `X-Public-Ip-Signature`, HMAC con `IDENTITY_SIGNING_SECRET` ligado a la ruta y válido 120 s; el gateway la deriva por saltos de confianza con `TRUSTED_PROXIES`, y document-service **nunca** lee `X-Forwarded-For`). Sin firma válida (llamada directa, firma vencida o de otra ruta, sin secreto) → `ip = null`, `ip_verificada = false`, y **la radicación no se bloquea**: la IP es evidencia complementaria, no requisito de la autorización. Las constancias v1 (`ip_declarada`) se conservan tal cual. Además, un campo **honeypot** `sitio_web` (invisible en el formulario) debe llegar vacío: con contenido → `422` genérico, validado en el esquema, antes de resolver el tipo, la dependencia o consumir número. Y la cabecera opcional **`Idempotency-Key`** (16-128 caracteres `[A-Za-z0-9_-]`; mal formada → `422 idempotency_key_invalida`, no se ignora en silencio) hace que un reintento devuelva el **mismo** radicado: la clave se reclama (`public_submission_keys`, migración `043`) con `ON CONFLICT DO NOTHING` **dentro de la transacción del radicado y antes del consecutivo**, de modo que dos envíos simultáneos se serializan en el índice único (el segundo espera el commit y devuelve el existente, `201` con el mismo número; un solo consecutivo gastado). La misma clave con **otro contenido** → `409 idempotency_key_reutilizada`. El proveedor de CAPTCHA (reCAPTCHA contradice la soberanía; la alternativa autoalojada es una prueba de trabajo tipo ALTCHA) sigue siendo decisión de la entidad; el texto de la política, ahora, lo publica la entidad en sus parámetros.
- `POST /api/v1/public/{tenant}/pqrs` — **legado**, se conserva por ser superficie pública ya publicada; delega en la ventanilla y no se amplía.

### Firma electrónica (E06, ADR-016)

- `POST /api/v1/documents/{id}/signatures` `{ "anexo_id", "reason"? }` — firma **declarativa** de un anexo (firma electrónica simple; la probatoria vive en signature-service): registra identidad (claims del gateway), huella y sello de tiempo. **Exige `PERM_FIRMA` nivel >= 2** (escritura). **El servidor resuelve la huella** del SHA-256 que storage calculó al subir: un `content_hash` en el cuerpo, si llega, **se ignora** (una huella elegida por el cliente no prueba nada). `422 anexo_requerido` sin `anexo_id`; `404` neutro si el radicado no existe, supera el _clearance_ o el anexo no es suyo; `503 huella_no_verificable` si storage no responde; `409 anexo_sin_huella`. El registro y el asiento `document.signature_created` en `public.audit_log` son **un solo acto** (misma transacción). *Pendiente de decisión del usuario: retirar el endpoint con `410` (no tiene consumidores).* **Las tres rutas de firma** exigen `PERM_FIRMA` (el mismo permiso que gobierna signature-service) y aplican no-read-up: `404` neutro si el radicado no existe o supera el _clearance_.
- `GET /api/v1/documents/{id}/signatures` — lista las firmas (`X-Total-Count`). `404` si el radicado no es visible; `[]` (200) si es visible y aún no tiene firmas — un sub-_clearance_ recibe siempre `404`, nunca `[]`, así que el par no es oráculo.
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` si alguna firma del radicado coincide con ese hash (si el contenido cambió, deja de verificar). Gateado igual que el listado: sin el filtro, el `valid` true/false sería un **oráculo de contenido** sobre documentos reservados. Solo cuentan las firmas con `origen='servidor'`: las heredadas con huella declarada por el cliente (`cliente_no_verificado`, migración 036) no verifican.

### Envíos postales (E20)

- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado por operador postal (4-72/servientrega/otro); genera la guía y estado `registrado`.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — envíos del radicado / detalle con historial de eventos.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — actualiza el estado de entrega (callback/sondeo del operador). Máquina de estados `registrado→en_transito→entregado|devuelto|fallido`; `409` si la transición es inválida.

### Cuentas de correo (E19, `USUA_PERM_ADMIN`)

Sustituyen a la cuenta única de las antiguas variables `IMAP_*` (ya retiradas), que no podía expresar lo que exige el Acuerdo AGN 060/2001 art. 13 —«las entidades […] asignarán responsabilidades de acuerdo con la cantidad de cuentas habilitadas»— y guardaba la contraseña en claro en el entorno.

- `POST|GET /api/v1/email/apps-oauth` — **aplicación OAuth que la ENTIDAD registró** en su propio Google Cloud o Azure AD. `{proveedor, client_id, client_secret, azure_directory_id?, redirect_uri, notas?}`; una fila por proveedor (`409 app_ya_registrada`), y la misma sirve para todas las cuentas de ese proveedor. Microsoft exige `azure_directory_id`; Google no lo usa (`422` en ambos sentidos). El `client_secret` se cifra en reposo y **nunca se devuelve**: la respuesta solo dice `tiene_secreto`.

  > **Por qué la registra la entidad y no el despliegue.** Google exige verificación y una **evaluación de seguridad CASA anual** por un tercero acreditado a cualquier aplicación que pida el scope `https://mail.google.com/` (el que cubre IMAP), pero **exime a las internas**: «_the app is only used by people in your Google Workspace or Cloud Identity organization […] its OAuth Consent Screen must be configured for internal use_». Con una aplicación central de OrpycaMCP habría que sostener esa acreditación, y OrpycaMCP es software que cada institución despliega. **No se pide en el alta de la entidad**: al crearla nadie sabe aún qué correo usará, quien la da de alta rara vez tiene los permisos de Azure o Google Cloud a mano, y muchas entidades usarán IMAP de hosting sin necesitarlo.

- `POST|GET /api/v1/email/cuentas` — buzones. **Los dos verbos NO piden lo mismo**: el alta exige `USUA_PERM_ADMIN`, el listado `PERM_RADI` **o** `USUA_PERM_ADMIN` (any-of, B6: `/admin/correo` ya no da 403 al administrador que no radica) — `PERM_RADI` es el mismo permiso que la bandeja, porque es la única forma de que un funcionario de correspondencia sepa qué buzones tiene y con qué identificador; con el gate de administrador, la pantalla a la que la bandeja está dirigida era inalcanzable para su público. Aflojarlo no afloja nada: **qué cuentas devuelve no lo decide el permiso, lo decide el `WHERE`** (personal para su dueño, institucional para los asignados, y el administrador ve las institucionales pero nunca las personales de nadie). Ningún secreto sale en la lectura: solo `tiene_credencial`. Tres tipos: **institucional** compartida (con funcionarios asignados), **personal** del funcionario y varias de recepción a la vez. `carpeta_origen` es el **filtro humano** —la carpeta a la que alguien arrastra lo que sí se radica, dejando fuera publicidad y spam— y `carpeta_radicados` a dónde se mueve el correo tras radicarlo; no pueden ser la misma. `modo` es `asistida` (un funcionario revisa y radica, con lo que existe el «funcionario responsable del trámite» del art. 2) o `automatica`. `direccion` y `usuario_login` son campos distintos: el login puede ser `DOMINIO\usuario` o solo la parte local.

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

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/radicar` — crea la Entrada. Orden por reversibilidad: primero los binarios (anexos y el `.eml` original, que Ley 527/1999 art. 12 obliga a conservar en su formato con origen, destino, fecha y hora), después el radicado, después el movimiento a la carpeta «Radicados», y por último la constancia al remitente si el buzón la tiene activada. Vuelve a comprobar la reserva antes de cerrar. **La dependencia competente viaja como `dest_dept_code`** —el código del organigrama—, no como el nombre en `dest_dept`: document-service valida el código y denormaliza el nombre, y sin él el radicado nacido de un correo queda sin vínculo con la dependencia (no aparece al repartir por código, y una tilde de diferencia crea dos áreas). `doc_class` es el código del tipo documental, que es lo que lo hace casar con una serie de la TRD.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/descartar` `{motivo}` — aparta un correo que llegó a la carpeta y no debía radicarse. Exige reserva y motivo, y deja asiento `email.mensaje.descartado` en `audit_log` **en la misma transacción**: no es best-effort. Decidir que una comunicación recibida **no** se registra es tan decisión como registrarla —de hecho es la que hace desaparecer el documento del archivo—, y hasta aquí solo dejaba un `UPDATE` sobre una tabla corriente, reescribible después sin rastro. El asiento **no** lleva el asunto ni el remitente: un correo descartado nunca se radicó, así que no tiene nivel de clasificación con el que recortar su lectura, y en `audit_log` lo vería todo `USUA_PERM_ADMIN`. El motivo sí, porque es el texto de la decisión, no el del documento.

> **Ruta heredada retirada.** Existieron `GET /api/v1/ingest/email/estado` y `POST /api/v1/ingest/email/poll`, que operaban sobre un único buzón declarado en el entorno con las variables `IMAP_*` y radicaban de golpe todo lo no leído. Están retiradas junto con esas variables: un buzón por despliegue no expresa lo que exige el Acuerdo AGN 060/2001 art. 13, la contraseña vivía en claro en el entorno, y radicar la bandeja entera radicaba también la publicidad. Los sustituyen los endpoints de cuentas y bandeja de arriba, donde la entidad separa lo que se radica de lo que no.

### `POST /api/v1/public/{tenant}/pqrs` (radicación ciudadana, Ley 1755/2015)

**Sin autenticación.** Un ciudadano radica una PQRS: `{ "tipo": "peticion|queja|reclamo|sugerencia|denuncia", "nombre", "identificacion"?, "email"?, "asunto", "descripcion" }`. Crea un radicado de Entrada (`metadata.canal=pqrs_ciudadano`) y devuelve `{radicado_id, tracking_number, verification_code}` para seguimiento posterior con el endpoint de verificación.

### `GET /api/v1/public/{tenant}/verify/{code}` (consulta pública, E13)

**Sin autenticación.** Verifica la trazabilidad de un radicado por su código de verificación (token no adivinable, distinto del número de radicado). El tenant viaja en la ruta. Devuelve solo información NO sensible: `tracking_number`, `doc_type`, `status`, `registered_at`, `dest_dept`. `404` si el código no existe; `400` si el slug de tenant es inválido.

### `GET /api/v1/reports/radicados`

Resumen estadístico de radicados del tenant (E09): `total` y conteos `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia` (la de **radicación**: a quién iba dirigido, cuenta todos los radicados) y `by_ubicacion` (**ADR-029**: dónde está AHORA lo que sigue en trámite, **sin** cerrados ni anulados: la carga real de cada dependencia). Filtros opcionales `?date_from=&date_to=`. Agregaciones SQL nativas (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`

Exporta el resumen en **CSV** (`seccion,clave,valor` con secciones tipo/estado/mes/`dependencia_radicacion`/`ubicacion_actual`; la sección `dependencia` se renombró `dependencia_radicacion` por ambigua — ADR-029 —) — para reportes periódicos al AGN/entes de control. Mismos filtros `?date_from=&date_to=`.

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

### `GET /api/v1/assistant/transcribe` · `POST /api/v1/assistant/transcribe`

Dictado por voz del asistente (E18, ADR-020). El **`GET` es una sonda de disponibilidad**, no un adorno: responde si hay proveedor de transcripción configurado y alcanzable, para que la interfaz decida si ofrecer el micrófono en vez de ofrecerlo y fallar al soltar el botón. El `POST` recibe el audio y devuelve el texto.

**No hay proveedor `stub`, y la asimetría con el resto del sistema es deliberada**: un validador puede decir honestamente «no evaluado», pero un transcriptor de mentira tendría que **inventarse lo que se dijo**. Sin proveedor, el `POST` responde `503` y el `GET` lo anuncia antes.

### `GET /api/v1/dependencias/tree`

El organigrama completo como **árbol** (`parent_id` resuelto), no como lista plana: la jerarquía es lo que consumen el selector de dependencia y la distribución de radicados, y reconstruirla en cliente a partir de una página parcial produce nodos huérfanos.

### `GET /api/v1/reports/indice-reservado` · `GET /api/v1/reports/indice-reservado.csv`

**Índice de información clasificada y reservada** (E08, Ley 1712/2014 art. 20 + Decreto 1081/2015). Registro de todos los radicados `nivel_seguridad ≥ 2` con la metadata del acto de clasificación. Gate **`PERM_RECLASIFICAR`**. **CONTENT-FREE** (nunca expone el asunto/contenido) y **ORTOGONAL al clearance** (el registro es COMPLETO por mandato legal — es una publicación de metadata, no una lectura de contenido). Mapeo: `nivel 2 = reservada → art. 19`, `nivel 3 = clasificada → art. 18`. Cada ítem: `{numero_radicado, tipo_radicado (E/S/I, NO serie TRD), nivel_seguridad, clasificacion, excepcion_ley_1712, fundamento_juridico, causal_reserva, plazo_reserva_meses, fecha_clasificacion, fecha_radicacion}`. La metadata se puebla al **radicar clasificado** o **reclasificar** (ambos exigen `fundamento` al pasar a nivel≥2, `422` si falta — Ley 1712 art. 19/28) y se limpia al desclasificar. El `.csv` neutraliza inyección de fórmulas en los campos de texto libre. La generación deja una entrada agregada en `audit_log` (RF-BUS-10).

**Índice completo y «nunca NULL» (T13 / AREA-09-04, migración `041`).** Cada fila añade `dependencia` (la que **recibe** E/I o la que **produce** S) y `registrado_por` (identificador opaco de usuario; `null` para lo que entró sin usuario). La promesa de que ningún clasificado carece de fundamento ya la sostiene la base: `CHECK radicados_clasificado_con_fundamento` (`NOT VALID` + backfill con el texto explícito «PENDIENTE DE MOTIVAR…» para lo heredado, validado en la migración) y la importación INT-05 rechaza con `422` todo el paquete si algún radicado trae `nivel_seguridad ≥ 2` sin `clasificacion.fundamento`. El nivel serie/expediente y el catálogo cerrado de fundamentos siguen pendientes (decisión de la entidad).

### `GET /api/v1/search`

Búsqueda avanzada de radicados (E09, ADR-014: PostgreSQL FTS). Texto completo con ranking (`tsvector`/`ts_rank`) + difuso (`pg_trgm`), combinable con filtros estructurados.

Busca en: `tracking_number` (peso A), `subject` (B), `sender_name` / `sender_entity` (C), `dest_dept` / `observations` (D).

**Query params** (todos opcionales): `q` (texto, min 2 chars — **opcional**: sin texto es una consulta filtrada ordenada por fecha), `doc_type`, `status`, `date_from`/`date_to` (rango sobre `registered_at`), `dest_dept_code` (dependencia de **radicación**), `ubicacion_dept_code` (dónde está **ahora**, ADR-029; mismo `WHERE` en filas y `COUNT`), `meta.<campo>=valor` (filtro por metadato, contención JSONB/GIN), `page`, `size`. Cabecera `X-Total-Count`. Cada resultado trae `ubicacion_dept_code`/`ubicacion_dept` además de `dest_dept`.

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

- **Motor con operadores** (`conditions` + `match_mode`): lista de `{field, op, value}` combinada por `all` (AND) o `any` (OR). Campos: `doc_type, doc_class, dest_dept_code, origin_dept_code, dest_dept, origin_dept, subject, sender_name, sender_entity, pages`. **Una dependencia se compara por código** (ADR-032): `eq/ne/in` sobre `dest_dept`/`origin_dept` (nombre) se rechazan con `422 condicion_por_nombre_obsoleta` (cambiaban de sentido al renombrar); `contains`/`not_contains`/`regex` sobre el texto siguen permitidos. Si el radicado no trae código resoluble, ninguna condición por código casa (tampoco `ne`). El destino es `assign_to_dept_code` (canónico; `assign_to_dept` es el alias deprecado) y debe ser una dependencia **activa**. Operadores: `eq, ne, contains, not_contains, in, gt, lt, gte, lte, regex`.
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

Actualizar parcialmente (ej: cambiar prioridad, activar/desactivar). El asiento registra **solo los campos que la petición tocó**, cada uno con su valor `antes` y `despues`; un `PATCH` sin campos no muta nada y no asienta.

### `DELETE /api/v1/workflow/rules/{id}`

Eliminar regla. Responde `204`. El borrado es **físico**, así que el asiento lleva el **retrato completo** de la regla — es el único sitio donde sobrevive lo que decía.

> **Los tres actos de escritura dejan asiento en `public.audit_log`** (ADR-010), atómico con la escritura: `workflow.regla_creada`, `workflow.regla_modificada`, `workflow.regla_eliminada`, con `object_type="workflow_rule"` y `object_ref` = el **UUID** de la regla (no su nombre: renombrarla no puede partir su historial en dos). Si la cadena inmutable no puede recibir el asiento, la escritura revierte.
>
> Una regla decide a qué dependencia se asigna cada radicado entrante: crearla es redirigir correspondencia oficial que aún no ha llegado, y borrarla es dejar de redirigirla. Los tres exigen `X-User-Id` — un asiento sin actor no responde quién decidió el enrutamiento, que es lo que se le pregunta a la traza.

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
> (véase [Asistente conversacional (E18, ADR-019/ADR-020) — vía gateway](#asistente-conversacional-e18-adr-019adr-020-via-gateway)),
> con un loop LLM real que ejecuta las tools de solo lectura _as-the-user_. Se
> conserva esta sección porque documenta la otra mitad del contrato —**lo que el
> proxy SSR del frontend hace con la respuesta**—, que no está en la del backend.
>
> El `503 assistant_unavailable` sigue existiendo, pero ya no significa "el backend
> no está construido": lo devuelve el propio mcp-server cuando le falta
> `ANTHROPIC_API_KEY` o el SDK falla (degradación honesta, RF-FIR-15). **Las dos
> causas comparten código pero no mensaje**, porque piden cosas distintas a quien
> lee: sin modelo configurado, `detail` dice que la instalación no lo tiene y que
> quien la administra puede activarlo —esperar no lo arregla—; con el proveedor
> caído, que se reintente en unos minutos. El proxy SvelteKit **reenvía** ese
> motivo al panel (solo el del sobre propio del asistente, acotado a 300
> caracteres) y traduce cualquier otra ausencia de ruta (404/501/5xx/conexión
> rehusada) a «El servicio del asistente no responde». Hasta 2026-10 el proxy lo
> sustituía por un texto fijo y la interfaz prometía que la función «se activará
> próximamente», un resto de cuando el backend no existía.

### `POST /api/v1/assistant/message`

Envía un turno del usuario y devuelve la respuesta del asistente. Autorización: **autenticado** (sin permiso `--op` fino; cada acción que el asistente dispare hacia adelante se re-valida contra el RBAC/clearance del usuario en el servicio que la ejecute). Headers de identidad (`X-Tenant-Slug`/`X-User-Id`/`X-Username`) los inyecta el proxy SSR desde `locals`, nunca del body.

```json
// Request  (allowlist en el proxy: solo estos dos campos)
{ "mensaje": "string (≤4000, trim)", "conversation_id": "uuid (opcional)" }
// Response 200
{
  "conversation_id": "uuid",
  "message": { "id": "uuid", "role": "assistant", "texto": "respuesta (AUTORITATIVO)", "formato": "markdown|text", "ts": "ISO-8601" },
  "propuestas": [],
  "no_verificados": []   // radicados del texto que ningún tool_result devolvió (AREA-11-04)
}
```

- `texto` es la **fuente de verdad**; `formato` es una pista de render. El proxy convierte `formato:"markdown"` a HTML con `marked` y lo **sanea server-side** (`sanitizeHtml`) antes de cruzar al cliente — el output del asistente es **contenido no confiable** (XSS/prompt-injection). El backend **no** debe enviar HTML; si lo hiciera, el proxy lo ignora.
- Reservado para el futuro (el slice actual los ignora sin romperse): `acciones[]`, `citations[]`, `usage`.
- Preparado para **streaming** (no construido): un `POST .../message/stream` (NDJSON/SSE) con el mismo `conversation_id` y `message.id` estables por turno permitiría _appendear_ deltas al mismo turno sin cambiar el shape.
- **Nota de contrato (a reconciliar)**: el proxy envía `X-Username` desde el claim `preferred_username` (el `SessionUser` del front no tiene `username`).
