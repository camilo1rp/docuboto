# Referencia de API

> Base URL en desarrollo: `http://localhost:8080/api/v1`
> Todos los endpoints (excepto los públicos) requieren `Authorization: Bearer {token}`.

## Convenciones

| Convención | Valor |
|---|---|
| Autenticación | `Authorization: Bearer {JWT}` |
| Paginación | `?page=1&size=20` — respuesta incluye `X-Total-Count` |
| Fechas | ISO 8601 UTC — `2024-01-15T10:30:00Z` |
| IDs | UUID v4 |
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

Requiere `Authorization: Bearer <token>`. Si se proporciona `X-Tenant-Slug`, se resuelven los permisos desde la BD del tenant. Sin esa cabecera (superadmin / llamada directa), `permissions` devuelve `[]` (fail-closed).

```json
// Response 200
{
  "user_id": "uuid-keycloak-sub",
  "username": "operador",
  "email": "operador@demo.orpycamcp.local",
  "tenant_slug": "demo",
  "roles": ["operator"],
  "permissions": ["PERM_RADI", "USUA_PERM_CONSULTA"]
}
```

- `permissions`: lista de nombres de permisos con `crud > 0` asignados al usuario a través de sus grupos. Los usuarios `is_root=true` siempre incluyen `USUA_PERM_ROOT`, coherente con `has_permission()` y `GET /users/{id}/permissions`.
- Si el usuario no está aprovisionado en el tenant, `permissions` es `[]` (fail-closed).

### 2FA TOTP — credencial del usuario (E06 Inc.6, RF-FIR-13)
El secreto TOTP (RFC 6238) es una **credencial de identidad durable** y vive en auth-service (tabla `auth_user_totp` por tenant, cifrada con **AESGCM**; AAD `tenant:{slug}:user:{id}:totp`). El secreto en claro se revela **una sola vez** en el `otpauth://` de `enroll` (`Cache-Control: no-store`); nunca vuelve a salir. Todos son auto-servicio (bearer del propio usuario).
- `POST /api/v1/auth/me/totp/enroll` — genera el secreto (20 bytes, base32) en estado `pendiente_activacion` y devuelve `{otpauth_uri, estado}` (para QR en un autenticador). Re-enrolar sobre un TOTP **activo** exige step-up (un código actual válido): `409 totp_ya_activo` / `401 step_up_invalido`.
- `POST /api/v1/auth/me/totp/activate` `{code}` — activa la credencial con un primer código válido → `{estado:"activo", activated_at}`. `409 totp_no_pendiente`; `401 codigo_invalido` (suma a `failed_attempts`); `423 totp_bloqueado`.
- `GET /api/v1/auth/me/totp` — `{estado, activated_at, locked_until}`. **Nunca** devuelve el secreto.
- `DELETE /api/v1/auth/me/totp` `{step_up_totp_code}` — revoca la credencial **activa** exigiendo step-up (código actual) → `{estado:"revocado"}`. `401 step_up_invalido`; `404 no_totp`.
- *(Interno, servicio-a-servicio en la red Docker, **no** enrutado por el gateway)* `POST /internal/totp/verify` `{code, contexto?}` — lo llama signature-service al firmar; deriva la identidad del **bearer reenviado** (no de un `user_id` de cuerpo → sin IDOR), honra `locked_until` antes de comparar, y avanza el anti-replay `last_timestep` en un único `UPDATE` atómico. Lockout tras `TOTP_MAX_FAILED_ATTEMPTS` (default 5) durante `TOTP_LOCKOUT_SECONDS` (default 900). Ventana de skew ±`TOTP_SKEW_STEPS` (default 1).

**Clave de firma PERSONAL PKI (E06/E17 F4, Inc.1 — custodia servidor, `acreditado=false`).** La clave privada de firma de cada usuario vive **solo en auth-service** cifrada AESGCM (KEK `SIGNING_KEY_ENCRYPTION_KEY` **separada** de la del TOTP), y NUNCA sale del servicio.

- `POST /api/v1/auth/me/signing-key/enroll` — genera el par RSA-2048 en memoria, cifra la privada, emite un **CSR** (subject = identidad del usuario + `cargo` mejor-esfuerzo desde grupos RBAC) y queda `pendiente_emision`; devuelve **solo** `{csr_pem, estado}` — **nunca** la clave privada. Exige `PERM_FIRMA`. Un re-enroll revoca la clave activa previa (endurecimiento de step-up diferido a Inc.2). La emisión del cert es **out-of-band** (`scripts/gen-user-cert.sh`, corre con `ca.key` fuera de todo contenedor; sub-CA de usuarios distinta de la del sello).
- `GET /api/v1/auth/me/signing-key` — `{estado, certificado_fingerprint?, not_after?}`. **Nunca** devuelve la clave privada.
- `DELETE /api/v1/auth/me/signing-key` `{step_up_totp_code}` — **revoca la clave propia** exigiendo step-up TOTP (verifica-y-consume; sin código → `422`) → `{estado:"revocado"}` (E06/E17 Inc.2). Deja asiento `signing_key.revocada` (con `revocado_at`/`motivo`) **atómico** con el UPDATE. `404` si no hay clave.
- `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` `{motivo?}` — un **admin** (`USUA_PERM_ADMIN`) revoca la clave de otro usuario (caso "empleado desvinculado"): el `user_id` objetivo va en el path, el actor sale del token (no confundibles). Asiento atómico con `revocado_por`.
- *(Interno, **no** enrutado por el gateway)* `GET /internal/signing-key/revocation-status?fingerprint=<hex>` — lo consulta signature-service al verificar una firma personal (`X-Internal-Token` + `X-Tenant-Slug`): devuelve **solo** `{status, revocado_at?}` (`status ∈ vigente|revocado|desconocido`) — nunca cert ni clave. Aislado por tenant: un fingerprint de otro tenant / inexistente → `desconocido` (fail-closed, nunca `vigente`). La activación pendiente→activo persiste `user_ca_fingerprint`+`certificado_fingerprint` y valida `leaf.verify_directly_issued_by(sub_ca)` fail-closed.
- *(Interno, **no** enrutado por el gateway)* `POST /internal/signing-key/sign-digest` `{digest_b64, alg:"RSA-SHA256", prehashed:true, totp_code}` — lo llama signature-service al firmar un acto personal de la cadena: deriva el usuario del **bearer reenviado** (sin IDOR), **verifica-y-consume el TOTP atómicamente** con la firma, descifra la clave **solo en memoria** (vida mínima), firma el digest, y devuelve `{signature_value_b64, certificado_pem, certificado_fingerprint, serial, not_after}` — la privada nunca se devuelve. Es la interfaz "firmar un digest" de un HSM (seam a firma cualificada). `X-Internal-Token` comparado con `compare_digest`. La activación pendiente→activo deja asiento `signing_key.activada` atómico.

### Gestión de usuarios RBAC (E14)
- `POST /api/v1/auth/users` · `GET /api/v1/auth/users` (paginado, `X-Total-Count`) · `GET /api/v1/auth/users/{id}/permissions` — administración de usuarios. Todo el router exige `USUA_PERM_ADMIN`.
- `GET /api/v1/auth/users/pickable?size=N` — proyección mínima `[{id, username}]` de usuarios **activos**, para selectores de UI (p. ej. el `UserPicker` de cadenas de visto bueno, T-08c). Gateado con `PERM_RADI` (no admin); no expone `email` ni metadatos administrativos (D-10). `size` en `[1, 500]`.

### URD y contexto de dependencia (E08)
- `GET /api/v1/auth/users/{user_id}/urd` · `POST` · `DELETE /{urd_id}` — administra las asignaciones Usuario-Rol-Dependencia (una por (usuario, dependencia); una sola principal). Exige `USUA_PERM_ADMIN`.
- `GET /api/v1/auth/context` — contexto del llamante: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — fija la dependencia activa del propio usuario. **No reemite token** (ADR-013): persiste el contexto y lo audita. `400 no_urd_for_dependency` si no tiene URD activa allí.

### Clasificación de seguridad (E08, RF-SEG-08, Ley 1712/2014)
- `GET /api/v1/auth/security-levels` — catálogo de niveles (1 Pública, 2 Reservada, 3 Clasificada).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — fija el nivel máximo accesible de un grupo (exige `USUA_PERM_ADMIN`).
- `GET /api/v1/auth/clearance` — clearance del llamante: `{user_id, max_level, accessible_levels[]}` (MAX sobre sus grupos; ROOT = 3).
- `GET /api/v1/auth/clearance/check?level=N` — `{level, max_level, allowed}` (mínimo privilegio: `allowed = max_level ≥ level`).

### `GET /api/v1/audit`
Consulta la auditoría inmutable del tenant (ADR-008), más recientes primero. Exige `USUA_PERM_ADMIN`. Filtros: `?action=&object_type=&object_ref=&actor=&page=1&size=20`. Cabecera `X-Total-Count`.

```json
[ { "id": 42, "ts": "2024-01-15T10:30:00Z", "service": "document-service", "actor": "uuid",
    "canal": "api", "action": "document.radicado_created", "object_type": "radicado",
    "object_ref": "2024-ICETEX-E-000001", "payload": { "...": "..." }, "hash": "sha256..." } ]
```

### `GET /api/v1/audit/verify`
Recalcula la cadena de hash del tenant y reporta su integridad (ADR-008). Exige `USUA_PERM_ADMIN`.

```json
{ "tenant_slug": "icetex", "ok": true, "broken_id": null }
```
- `ok=false` con `broken_id` = id de la primera fila cuya cadena no concuerda.

---

## tenant-service — puerto 8002

### `POST /api/v1/tenants`
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

### `GET /api/v1/tenants`
Lista instituciones. Query: `?status=active&page=1&size=20`. Header `X-Total-Count`.

### `GET /api/v1/tenants/{id}`
Obtener por UUID.

### `GET /api/v1/tenants/by-slug/{slug}`
Obtener por slug. Usado por otros servicios para validar el tenant del JWT.

### `PATCH /api/v1/tenants/{id}`
Actualizar `name` o `status`. `slug` y `code` son inmutables.

### Administración del tenant (E14)
- **Dependencias** (organigrama): `GET/POST/PATCH /api/v1/dependencias`, `GET /api/v1/dependencias/by-codigo/{codigo}` (jerárquicas).
- **Catálogos** (lookup): `GET/POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
- **Parámetros**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}`.
- **Días no hábiles / festivos**: `GET /api/v1/config/holidays` (`?year=`), `POST /api/v1/config/holidays` (alta), `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year}` → siembra los festivos colombianos calculados automáticamente).
- **Días hábiles**: `GET /api/v1/business-days/calculate?from_date=&days=` — calcula la fecha de vencimiento aplicando fines de semana + festivos + no laborables del tenant (base de RF-RAD-04).

### PINAR — Plan Institucional de Archivos (E14, RF-ADM-08, Acuerdo AGN 003/2015)

Instrumento de planeación archivística por tenant. Requiere rol administrador (mediado por el gateway). El actor imputable llega en `X-User-Id`.

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
- `nivel_seguridad` (opcional, default `1`): clasificación de seguridad del radicado (RF-SEG-08) — `1`=PUBLICA, `2`=RESERVADA, `3`=CLASIFICADA (= `security_levels.code`). La búsqueda oculta a cada usuario los radicados por encima de su *clearance* (ver _Full-Text Search_).

### `POST /api/v1/metadata/templates`
Define la plantilla de metadatos (JSON Schema) de un tipo documental. Una sola plantilla activa por `tipo_documental`.

```json
// Request
{ "tipo_documental": "Factura", "version": 1, "activo": true,
  "json_schema": { "type": "object", "required": ["valor"],
                   "properties": { "valor": { "type": "number" } } } }
```
- `422 invalid_json_schema` si `json_schema` no es un JSON Schema válido; `409 conflict` si ya existe esa versión o una activa para el tipo.

### `GET /api/v1/metadata/templates`
Lista plantillas; filtro opcional `?tipo_documental=Factura`.

### `/api/v1/metadata/elements` (catálogo de elementos de metadato, RF-MET-03)
CRUD de definiciones reutilizables de campos de metadato (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Atributos: `clave`, `etiqueta`, `tipo_dato` (text/number/date/boolean/select), `longitud`, `ocurrencia_min/max`, `modificable`, `valor_default`, `opciones`, `orden`, `searchable`, `mapeo_dublin_core` (gancho RF-MET-09). `409` si la clave ya existe.

### `GET /api/v1/documents`
Listar radicados. Filtros: `?doc_type=E&status=registered&page=1&size=20`. **Filtro por metadato** (RF-MET-02): `?meta.<campo>=valor` (uno o varios) usa contención JSONB sobre el índice GIN, p. ej. `?meta.area=juridica`. **Control de acceso (RF-SEG-08):** el listado se acota por la clasificación del radicado igual que la búsqueda — cada usuario solo ve los de `nivel_seguridad ≤` su *clearance* (*fail-closed* a PUBLICA).

### `GET` / `PATCH /api/v1/documents/{id}/disposition`
Metadatos de disposición del documento (RF-MET-08): `programa`, `retention_until`, `accion` (conservar/eliminar/transferir/seleccionar), `confirmado`, `marcado_eliminacion`. `PATCH` hace merge de los campos provistos. Exige `USUA_PERM_EXPEDIENTE`. **Control de acceso (RF-SEG-08):** `404` tanto si el radicado no existe como si supera el *clearance* del llamante — indistinguibles, para no revelar la existencia de material clasificado. El gate aplica también al `PATCH`: la disposición decide conservación total frente a eliminación (Ley 594/2000 art. 24), así que quien no puede leer el radicado tampoco puede fijar su destino.

### `GET /api/v1/documents/{id}`
Obtener por UUID. Incluye lista de anexos. **Control de acceso (RF-SEG-08):** si el radicado supera el *clearance* del llamante, responde `404` (no revela su existencia).

### `GET /api/v1/documents/by-tracking/{tracking_number}`
Obtener por número de radicado (ej: `2024-ICETEX-E-000001`). Misma acotación por clearance que el detalle por UUID (`404` si excede el nivel del llamante).

### `PATCH /api/v1/documents/{id}`
Actualizar `subject`, `dest_dept`, `observations`, `status`, `pages`.
`tracking_number`, `doc_type`, `year`, `sequence` son inmutables.
**Control de acceso (RF-SEG-08):** `404` si el radicado no existe **o** supera el *clearance* del llamante. El gate es imprescindible porque la respuesta devuelve el radicado completo (asunto, remitente, anexos): sin él, la escritura sería también una lectura.

### `PATCH /api/v1/documents/{id}/security-level`
**Reclasificación del nivel de seguridad** de un radicado ya radicado (E08, paridad con el *Cambio Nivel de Seguridad* de Orfeo). Cambia `nivel_seguridad` (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) sin alterar contenido ni `tracking_number` (inmutabilidad intacta). Requiere permiso **`PERM_RECLASIFICAR`**.
- **Body**: `nivel_seguridad` (1..3, obligatorio), `motivo` (string no vacío, **obligatorio** — el acto de reserva debe motivarse, Ley 1712 art.19/28), y opcionales `causal_reserva`, `fundamento_juridico`, `plazo_reserva_meses`.
- **Control de acceso (RF-SEG-08)**: *no-read-up* — si el *clearance* del llamante es menor que el nivel actual del radicado responde `404` neutro (no revela existencia ni nivel); *no-write-up* — no puede fijar un nivel mayor que su propio *clearance* (`403`). Si el nivel solicitado coincide con el actual, es *no-op* idempotente (sin auditoría ni evento).
- **Traza**: registra en el `audit_log` inmutable (`document.radicado_reclassified`) el `nivel_anterior`, `nivel_nuevo`, `motivo` y el fundamento jurídico, de forma atómica con el cambio.
- **Propagación**: emite `document.radicado.reclassified`; el signature-service re-sincroniza el *snapshot* `nivel_seguridad` de las cadenas de firma del objeto (cierra el residual de *staleness* de RF-SEG-08).

---

## storage-service — puerto 8005

> Requiere header `X-Tenant-Slug`. Archivos almacenados en MinIO bucket `orpycamcp-{slug}-documents`.

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
{ "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...", "expires_in": 3600 }
```

### `GET /api/v1/storage/files/{file_id}/verify`
Reverifica la integridad (RF-DIG-02): descarga el objeto de MinIO, recalcula el SHA-256 y lo compara con el registrado.

```json
{ "file_id": "uuid", "expected_sha256": "...", "actual_sha256": "...", "integrity_ok": true }
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
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, file_id, nombre}], indice_file_id?, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment B, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** empaqueta el **AIP OAIS real**: por cada documento resuelve su objeto por `file_id`, descarga los bytes de MinIO, **recalcula la fixity** (SHA-256 vs `files.sha256`; mismatch → `409` + evento `FIJACION` fallo), arma un **Bag BagIt RFC 8493** (`data/` con los bytes + `bagit.txt` + `bag-info.txt` con Payload-Oxum real + `manifest-sha256.txt` + `premis.xml` [PREMIS v3, namespace oficial, validado contra un perfil XSD local] + `indice-firmado.xml` [si `indice_file_id`] + `tagmanifest-sha256.txt`), lo **serializa ZIP**, lo sube al **bucket WORM** de preservación con `Retention(COMPLIANCE, retain_until)` derivada de la TRD + `legal_hold`, y lo registra en `preservacion_worm_objeto` (`tipo='aip'` → cubierto por el gate de disposición). Requiere `USUA_PERM_EXPEDIENTE` con **`crud≥3`**. `nombre` se valida (sin `/`, `..`, control chars) y debe ser **único** dentro de `documentos` (`422`). `201` con `{id, expediente_id, aip_sha256 (del ZIP), num_documentos, estado, bucket, object_key, aip_bytes, formato, retain_until, retention_mode, legal_hold, premis_xml, replicado, replica_target, replica_copias_hechas, created_at}`; `400` retención inválida; `404/422` `file_id` inexistente o `nombre` duplicado; `409` fixity mismatch; `503` si `PRESERVATION_ENABLED=false`. **REPLICA (C1b)**: si `PRESERVATION_REPLICA_ENABLED=true` y el plan tiene `num_copias≥2`, tras subir el AIP primario se crea **una réplica inmutable** en un segundo bucket WORM (`replica_target="local"` = segundo bucket del mismo MinIO, *no* copia geográfica; `="cross-site"` si `MINIO_SECONDARY_ENDPOINT`). Es **best-effort no-fatal**: un fallo de réplica **no** revierte el AIP (`replicado=false` + evento `preservacion_evento` `REPLICA` `resultado=fallo`); `num_copias≤1` → sin réplica.
- `GET /api/v1/preservacion/aip/{expediente_id}` — AIP vigente (requiere `USUA_PERM_EXPEDIENTE` `min_crud=1` — expone `premis_xml` con nombres/hashes de documentos y ubicación WORM). Los campos de ubicación WORM son `null` en filas AIP previas al Increment B (retrocompat).
- `POST /api/v1/preservacion/proteger-indice` `{file_id, expediente_id, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment A, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** protege el **índice electrónico firmado** (ya subido a `files`, referenciado por `file_id`) bajo **WORM / Object-Lock (MinIO)**. Aprovisiona (idempotente) el bucket de preservación por tenant `orpycamcp-{slug}-preservation` (nombre **solo del claim JWT**), **verifica fixity** (recalcula el SHA-256 sobre los bytes y lo contrasta contra el hash **registrado en la subida original** — el llamador **no** declara ningún hash; mismatch → `409` + evento `FIJACION` fallo, no sube), copia el objeto con **`Retention(COMPLIANCE, retain_until)`** por-objeto + `legal_hold`, registra en `preservacion_worm_objeto` y emite eventos PREMIS `WORM`/`FIJACION` + `audit_log`. La **retención la aporta el caller** derivada de la TRD (storage no llama a archive): `retain_until` directo **o** `retencion_anios` (1–100) + `fecha_inicio` (aritmética de años calendario), nunca ambos. Requiere `USUA_PERM_EXPEDIENTE` con **`crud≥3`** (umbral de disposición) + clearance. **Idempotente (E10 Inc.1 wiring)**: `201` con la ubicación WORM si es la primera protección; **`200`** con la fila existente si el índice de ese expediente YA está protegido (no re-sube — corte-circuito antes del `put_object`, respaldado por el índice único parcial `WHERE tipo='indice'` y un **advisory lock de sesión por `(tenant, expediente)`** que serializa `check→put→insert` para no duplicar objetos WORM irreversibles bajo carrera). `400` retención inválida (ausente/pasada/fuera de cotas o fuentes en conflicto); `404` si el `file_id` no existe en el tenant; `409` fixity mismatch; `503` si `PRESERVATION_ENABLED=false`. **Frontera de confianza**: la correspondencia `file_id ↔ expediente_id` (que `file_id` sea el índice firmado del expediente) la garantiza **archive-service** (único invocador); storage valida que el `file_id` exista pero no modela expedientes. **Efecto en disposición**: mientras el índice esté bajo `retain_until` futuro o `legal_hold`, la disposición final del expediente queda **bloqueada** (la eliminación no puede ocurrir antes de expirar la retención — Ley 594/2000, Acuerdo AGN 060/2001).
- *(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)* `POST /api/v1/preservacion/proteger-indice-internal` — gemelo **interno** (D-02) de `proteger-indice` con el MISMO servicio/idempotencia, autenticado **solo** por `X-Internal-Token` en vez de `require_permission(crud≥3)` y atribuido al principal de sistema `SYSTEM_RECONCILER_ID`. Existe porque el **cableado WORM del índice** (`IndexService.proteger_worm_indice`, archive-service, E10 Inc.1) tiene dos orígenes: el intento síncrono best-effort tras firmar el índice al cierre (`origen="close"`, usuario real → ruta pública) y la **3ª fase de reconciliación** (`run_once_worm`, `origen="reconciliation"`, `SYSTEM_RECONCILER_ID` sin fila `auth_users` → esta ruta interna; `require_permission` la rechazaría siempre). La retención se deriva de la TRD del expediente (`archivo_gestion_years + archivo_central_years`, `fecha_inicio`=cierre); **fail-closed si la TRD no resuelve** (no protege, marca `trd_sin_retencion`, recuperable por el barrido). Espeja `/signature/internal/sign-indice`.
- `POST /api/v1/preservacion/proteger-artefacto` `{file_id, expediente_id, tipo, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Inc.2 wiring, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** forma **generalizada** de `proteger-indice`: protege bajo WORM/Object-Lock cualquier **artefacto de preservación** del expediente, con `tipo ∈ {`indice`, `acta_transferencia`}` (default `indice` si se omite). MISMO servicio, fixity, retención derivada de la TRD por el caller, RBAC `USUA_PERM_EXPEDIENTE crud≥3` + clearance, e **idempotencia** que el `proteger-indice`, ahora **por `(expediente_id, tipo)`** (índice único parcial `WHERE tipo='acta_transferencia'` en `preservacion_worm_objeto`, migración `010`) y con el **advisory lock de sesión por `(tenant, expediente, tipo)`** — dos tipos del mismo expediente (índice y acta) no colisionan pero cada uno serializa su propio `check→put→insert`. `/proteger-indice` se conserva como **alias duro** que fuerza `tipo="indice"` sea cual sea el body (retrocompat del Inc.1). Códigos y frontera de confianza idénticos a `proteger-indice`. El **acta de transferencia firmada** (sello XAdES, `transferencia_acta_firma`) la cablea archive vía `TransferenciaService.proteger_worm_acta` (disparo síncrono best-effort tras el sello + 4ª fase de reconciliación `run_once_actas_worm`); su retención se deriva de la TRD del expediente de la transferencia con **`fecha_inicio = firmado_at` del acta** (no el cierre — un acta puede firmarse mucho después en transferencias secundarias).
- *(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)* `POST /api/v1/preservacion/proteger-artefacto-internal` — gemelo **interno** de `proteger-artefacto` (con `tipo` en el body), autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Lo usa la reconciliación desatendida del acta (`run_once_actas_worm`, `origen="reconciliation"`), igual que `proteger-indice-internal` para el índice. `/proteger-indice-internal` sigue siendo el alias duro `tipo="indice"`.
- *(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)* `POST /api/v1/preservacion/renovar-retencion-internal` `{expediente_id, tipo, retencion_anios + fecha_inicio | retain_until}` — **(E10 debt: renovación de retención WORM para series de Conservación Total)** extiende (nunca acorta) el `retain_until` de un artefacto **ya protegido** bajo WORM, identificado por `(expediente_id, tipo)` (`tipo ∈ {indice, acta_transferencia}`). Reusa `RetentionMixin`. Semántica **monotónica**: si la fecha resuelta no supera **estrictamente** el `retain_until` ya persistido, es un **no-op idempotente** (`200`, `renovado=false`, sin tocar MinIO). Si la supera, llama a `Minio.set_object_retention` (extensión real; COMPLIANCE rechaza acortar de todos modos) y responde `200` con `renovado=true`; el UPDATE + evento PREMIS `RENOVACION` + asiento `file.worm_retencion_renovada` van en **una tx** (el `set_object_retention` queda fuera, antes). Autenticado **solo** por `X-Internal-Token` (atribuido a `SYSTEM_RECONCILER_ID`): la renovación es un **acto de sistema** conducido por la 5ª fase de reconciliación de archive (`run_once_worm_renovacion`), único que conoce qué serie es CT — **NO hay ruta pública** (se retiró en la remediación: storage no puede imponer el filtro CT, así que una ruta pública con `retain_until` directo sería superficie irreversible sin garantía CT). `404` `worm_object_not_found`; `400` retención inválida; `503` si `PRESERVATION_ENABLED=false`. `retention_mode`/`legal_hold` **no cambian**. Respuesta: `{id, expediente_id, tipo, bucket, object_key, retain_until, retention_mode, renovado, renovaciones, renovado_at}`.
- `GET /api/v1/expedientes/indices/pendientes-renovacion?page=&size=` — **(E10 debt: visibilidad de la renovación CT)** lista los artefactos WORM de series **CT** (índice + acta) cuya renovación está **agotada** (`worm_renovacion_agotado=TRUE`) o **en riesgo** (`worm_retain_until` vence dentro de `worm_renovacion_ventana_dias`) — para que un archivista vea un lock COMPLIANCE de conservación permanente a punto de expirar sin renovar (espejo de `pendientes-firma`). Gate `USUA_PERM_EXPEDIENTE` + **no-read-up** (el filtro `nivel_seguridad <= clearance` vive en el SQL de ambas consultas; `X-Total-Count` refleja el conteo **ya filtrado** por clearance — no es oráculo). Ordena agotados primero, luego vencimiento más próximo. Por fila: `{expediente_id, expediente_code, tipo, worm_retain_until, worm_renovacion_intentos, worm_renovacion_agotado, worm_renovacion_ultimo_error}`.
- *(Interno, servicio-a-servicio; **no** alcanzable desde el gateway)* `POST /api/v1/preservacion/renovar-retencion-internal` — gemelo **interno** de `renovar-retencion`, autenticado solo por `X-Internal-Token`, atribuido a `SYSTEM_RECONCILER_ID`. Único llamador en este incremento: la **5ª fase de reconciliación** de archive-service (`run_once_worm_renovacion`), que barre — por cada tenant activo — los índices y actas **de series de disposición Conservación Total (CT)** cuyo `retain_until` vence dentro de `WORM_RENOVACION_VENTANA_DIAS` (default 400 días) y llama a este endpoint con `retencion_anios=total_TRD` + `fecha_inicio=hoy` (**ventana rodante**, nunca una fecha lejana/9999). El filtro CT es **fail-closed en la consulta SQL** del lado de archive (`disposition ∈ {CT, conserve}`) — series `E`/`S`/`M` nunca entran al barrido, aunque estén próximas a vencer (expiran y se disponen legítimamente). Backoff exponencial propio (`worm_renovacion_intentos`/`worm_renovacion_agotado` en `expediente_indice`/`transferencia_acta_firma`, migraciones `031`/`032`) — un `agotado=true` en este eje es una alarma de **mayor severidad** que en el sellado/protección inicial: implica riesgo de que el lock COMPLIANCE de un registro de conservación **permanente** venza sin haberse podido extender. **AIP queda fuera de alcance** de la renovación (sin espejo en archive-service, deuda futura).

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

### `POST /api/v1/workflows/overdue/scan`
Barrido de vencimientos (RF-FLU-07): detecta pasos activos vencidos no alertados, emite `workflow.step.overdue` al bus (→ notificaciones) y los marca para no duplicar. Idempotente por paso; pensado para invocación periódica por un scheduler. Responde `{ "alerted": N }`. Exige `USUA_PERM_ADMIN` (barrido transversal del tenant). La reasignación masiva `POST /api/v1/workflows/reassign/cascade` (RF-FLU-08) exige el mismo permiso de administración.

### Vistos buenos secuenciales (paridad legado)
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — crea la cadena ordenada de revisión (1..20 revisores, sin duplicados). Exige `PERM_RADI` (D-09); `403 forbidden` si falta permiso, identidad o tenant. `409 cadena_existente` si el radicado ya tiene cadena.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — el revisor de turno (menor orden pendiente) aprueba/rechaza. Requiere `X-User-Id` válido (400 si falta). `403 not_a_reviewer` si el actor no está en la cadena de revisores de ese radicado; `403 no_es_su_turno` si está en la cadena pero no es su turno; un rechazo detiene la cadena.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — cadena + estado global (`en_revision|aprobado|rechazado`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (devolución al remitente)
Re-enruta un radicado mal asignado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Cierra el paso activo como `returned` y crea uno nuevo hacia `to_dept`, con la causal en el historial. Autorización por tenencia: solo el responsable actual del paso activo puede devolver. `403 not_current_holder` si el actor no es el tenedor. `404` si no hay paso activo.

### Transacciones de trámite (RF-FLU-01)
- `GET /api/v1/workflows/transaction-types` — catálogo (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), con su permiso atómico y efecto de estado.
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — ejecuta la transacción: aplica el efecto al paso activo (p. ej. `marcar_leido`→in_progress, `anular`→cancelled) y registra el evento append-only. **Enforcement RBAC por-tipo (RF-SEG-03):** cada tipo declara en `transaction_types.requires_permission` el permiso que exige (`anular`→`PERM_ANULAR`, `cerrar_exp`→`PERM_CERRAR_EXP`, `vobo`→`PERM_VOBO`…); sin él, `403 forbidden` y la transacción **no** se aplica. Un tipo con `requires_permission = NULL` es abierto. `404 unknown_transaction` / `no_flow`. `PATCH /{step_id}/complete` exige `PERM_TRAMITAR`.

### `POST /api/v1/workflows/{radicado_id}/rollback`
Revierte la última asignación (RF-FLU-08): cancela el paso vigente —solo si sigue `pending`— y reactiva el anterior, atómicamente, con evento `tipo_tx=rollback`. `409 no_active_step` (sin paso activo) o `cannot_rollback_initial` (es la radicación inicial).

### `POST /api/v1/workflows/reassign/cascade`
Reasigna en una transacción todos los pasos activos de un usuario o dependencia a un nuevo responsable, sin dejar radicados huérfanos (RF-FLU-08).

```json
// Request (indicar from_assigned_to o from_dept)
{ "from_dept": "Dirección Jurídica", "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12 }
```

### `GET /api/v1/workflows/{radicado_id}/events`
Historial **append-only** de transacciones del radicado (RF-FLU-02, inalterable). A diferencia de `/history` (proyección de pasos), registra cada transacción y su tabla rechaza UPDATE/DELETE. **Control de acceso (E05 §10 / RF-SEG-08):** es la *hoja de ruta operativa* (Canal B) — visible a cualquier usuario que pueda **leer** el radicado, acotada por su clearance (no read-up); exige `X-User-Id` y un radicado por encima del clearance devuelve historial vacío. No requiere permiso de administrador (eso es la auditoría forense `audit_log`).

```json
[ { "id": "uuid", "tipo_tx": "assign", "from_dept": null, "to_dept": "Ventanilla",
    "actor": "uuid", "comentario": "...", "detalles": { "step_number": 1 }, "ts": "2024-01-15T10:30:00Z" } ]
```

### `GET /api/v1/workflows/{radicado_id}/current`
Paso activo actual del radicado. `404` si está cerrado o sin asignar.

### `GET /api/v1/workflows/pending`
Radicados pendientes. Filtros: `?dept=Dirección+Jurídica&assigned_to={uuid}&page=1&size=20`.

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

> **Control de acceso:** **todos** los endpoints de expedientes (12: create/list/search/get/foliado/eventos/close/transfer/update/link/batch/unlink) y los de `batch.py` (`POST /api/v1/batch/expedientes`, `GET /api/v1/batch/{job_id}/status`) requieren `USUA_PERM_EXPEDIENTE` (fail-closed: `403` sin `X-Tenant-Slug`, sin `X-User-Id`, o sin el permiso). **Mínimo privilegio (RF-SEG-03, nivel `crud` 0-5):** las operaciones de **disposición** — `close`, `transfer`, `unlink`, y el `PATCH` que transiciona a `closed`/`transferred` — exigen `USUA_PERM_EXPEDIENTE` con **`crud≥3`** (Crear/Borrar); el resto (lecturas, create, link, PATCH de nombre/serie) basta con `crud≥1`. En `list`/`get`/`eventos`/`search` **y el `PATCH`** la **clearance (RF-SEG-08, no read-up)** filtra **además** lo visible por nivel de seguridad: cada usuario solo ve/edita expedientes de `nivel_seguridad ≤` su *clearance* (*fail-closed* a PUBLICA sin `X-User-Id`), un expediente por encima devuelve **404** (no revela su existencia), y en `list`/`search` el **`X-Total-Count` también queda filtrado** (no revela cuántos clasificados existen). Toda consulta de `list`/`search` deja una **traza agregada** en `audit_log` (RF-BUS-10: usuario, criterios, total; sin el texto crudo de `q`), separada de la pista por-expediente. En `link`/`batch`/`unlink`/`indice/rebuild` (M2, RF-SEG-08) la **mutación es legítima sin clearance de lectura** (componer/ordenar el expediente es gestión archivística, ortogonal a leer su contenido) y **siempre** ocurre y **siempre** audita; pero la **respuesta se acota** si el llamante no puede leer el expediente (`nivel_seguridad >` su *clearance*): counts/huella/`added_at` → `null`, y se **suprimen los oráculos** (409 `radicado_already_linked`, 404 `radicado_link_not_found`, 409 `expediente_not_open`) devolviendo un ack **uniforme e indistinguible** sea cual sea el estado — un no-lector no infiere pertenencia, volumen ni ciclo de vida de un expediente clasificado. El permiso se resuelve en la BD del tenant (el api-gateway no gatea por ruta).

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

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — topología recursiva (sede→…→gaveta); `codigo` único por tenant; `ruta` derivada. Filtros `?parent_id=&activo=`.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — unidades de conservación (caja|carpeta|…); al indicar `ubicacion_id` se deriva la **signatura topográfica** (`DEP01-E05-C0124`, única). Filtros `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — vincular expediente a unidad (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — qué contiene la unidad; **filtrado por fila** por clearance (RF-SEG-08, no-read-up): enumerar el contenido de una unidad revela la *existencia* de sus expedientes clasificados, así que un no-lector no ve los de `nivel_seguridad >` su *clearance* (Ley 1712/2014 art. 19; *fail-closed* a PUBLICA, `403` sin `X-User-Id`). `GET /api/v1/expedientes/{id}/unidades` — dónde está el expediente (signatura + rango); **sin filtro de clearance a propósito**: se consulta por un `expediente_id` ya conocido y solo devuelve ubicación física (signatura/folios, no asunto/serie) — es gestión de custodia ortogonal al clearance de lectura (mismo criterio que la transferencia).
- **Préstamos (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 si ya prestada), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` se deriva de `fecha_devolucion_esperada`.
- **Historial (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — movimientos append-only (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — inventario documental (expediente↔unidad↔signatura↔serie) en JSON con orden consecutivo y campos AGN 042/2002; filtros `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exporta en XML. **Control de acceso (RF-SEG-08, no-read-up):** el FUID describe **contenido** (`asunto`/serie/`signatura`), así que se filtra **por fila** por la clasificación del expediente — cada usuario solo ve en el inventario los de `nivel_seguridad ≤` su *clearance* (`total` cuenta solo lo visible; *fail-closed* a PUBLICA, `403` sin `X-User-Id`). En `GET /transferencias/{id}/fuid` (un solo expediente) el filtro es un **404 total** si el expediente supera el clearance (indistinguible de inexistente). La transferencia en sí (create/enviar/recibir/rechazar) es acto de **custodia**, ortogonal al clearance de lectura (no filtra por él, como el cierre/transferencia de expedientes).

#### TRD/CCD (E04, ADR-015)
> **Control de acceso (config archivística):** las **mutaciones** de config (`POST/PATCH /api/v1/trd`, `POST/PATCH/DELETE /api/v1/tipos-documentales`, `POST /api/v1/expediente-metadata/templates`) requieren **`USUA_PERM_TRD`** (fail-closed: `403` sin tenant/user-id/permiso) y dejan asiento en `audit_log`. **Disposición de config** — `DELETE /tipos-documentales/{id}` y `PATCH /trd/{id}` (cambia retención/disposición final) — exige **`crud≥3`** (RF-SEG-03). Las **lecturas** GET quedan **abiertas** (solo tenant): alimentan la clasificación en radicación (dropdowns de TRD/tipos) y la validación de plantillas — consultarlas es uso del instrumento, no acto de configuración.
- `POST/GET/PATCH /api/v1/trd` — series y **subseries** documentales. Campos: `code`, `name`, `parent_id` (subserie), `archivo_gestion_years`/`archivo_central_years` (retención en dos fases), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`. (POST/PATCH exigen `USUA_PERM_TRD`.)
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **calendario de retención**: `fin_archivo_gestion`, `fin_archivo_central` y disposición final (`disposition_code`/`disposition_label`) derivados de la serie y la fecha de cierre.

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
- `GET /api/v1/expedientes/{id}/export.zip` — **exportación del expediente a ZIP** (E02/export, Ley 594/2000 art. 19). Copia de CONSULTA/ENTREGA (distinta del AIP de preservación E10 y de la transferencia FUID E12). El ZIP contiene: `indice-electronico.xml` (E15, si está firmado — best-effort), `manifiesto.csv` (radicados **vinculados y excluidos** —estos con su causal, sin bytes—, con `orden_documental`/`folios`/`formato`/`dependencia_productora`/`fecha`), `documentos/{tracking}/{idx}-{filename}` (bytes de los anexos), `LEEME.txt` (leyenda de alcance) y `checksums.txt` (SHA-256 por miembro, para validar integridad). Requiere `USUA_PERM_EXPEDIENTE`. **No-read-up POR-RADICADO** (RF-SEG-08): `404` si el expediente supera el clearance del llamante, y **los radicados individuales por encima del clearance se omiten por completo** (ni su existencia se enumera en el manifiesto) — no basta el gate a nivel de expediente. archive orquesta; el ensamblado de bytes lo hace storage-service (endpoint interno D-02, inalcanzable desde el gateway) sin persistir nada; `arcname` saneado anti zip-slip. La exportación deja una entrada de auditoría agregada (`archive.expediente_exportado`, RF-BUS-10). *Deuda: PDF combinado, streaming para expedientes grandes, `asunto`/`tipo documental` en el manifiesto.*
- `GET /api/v1/expedientes/{id}/historial-acceso` (`?page&size`, E15 v4 R3) — **pista de acceso** del expediente: lee `public.audit_log` (solo lectura, filtro `tenant_slug` explícito) filtrado por `object_ref = code` y `object_type ∈ (expediente, expediente_indice)`, orden `ts DESC`, con `X-Total-Count`. Devuelve `{ts, actor, canal, action, ...}`. Requiere `USUA_PERM_EXPEDIENTE` + no-read-up (**404** si el expediente supera el clearance del llamante, no 403 — no filtra existencia).
- `GET /api/v1/expedientes/{id}/roles-autorizados` (E15 v4 R4) — **snapshot vivo** (no firmado) de la matriz de acceso: grupos que tienen `USUA_PERM_EXPEDIENTE` (`crud≥1`) **y** `role_clearance.max_level ≥ nivel_seguridad` del expediente, como `[{grupo, clearanceMax, crud}]` ordenado por nombre. Mismo gate (`USUA_PERM_EXPEDIENTE` + no-read-up). Es el dato que el índice firmado deliberadamente **no** incrusta (evita churn del artefacto probatorio y fuga de topología IAM en transferencias AGN).
- `GET /api/v1/expedientes/{id}/indice/versions` — historial de versiones (append-only) con `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifica integridad: contrasta el `valor_huella` del índice contra el hash actual de cada documento **y** re-hashea el XML almacenado contra `xml_sha256` (`huella_indice_ok` — detecta alteración del propio XML del índice, incluso no firmado). Con firma XAdES-B descarga el XML firmado desde MinIO y lo verifica contra signature-service — el bloque `firma` incluye `{presente, valida, nivel_conformidad, acreditado, motivo}` (`motivo='signed_xml_unavailable'` → `valida=false`). `valido` es la conjunción de fixity por documento + `huella_indice_ok` + firma (si presente). Requiere `USUA_PERM_EXPEDIENTE`.
- `GET /api/v1/expedientes/indices/pendientes-firma` — lista operacional (paginada, `X-Total-Count`) de los índices en estado `pendiente_firma` de expedientes `closed`/`transferred`, con sus contadores de reconciliación (`reconcile_intentos`, `reconcile_agotado`, `reconcile_ultimo_error`, versión, `expediente_code`) — visibilidad directa para el archivista de los índices que aún no se sellaron, **incluidos los `reconcile_agotado=true`** que exigen intervención humana (montar el sello + retry manual). **No-read-up**: filtra por `nivel_seguridad <= clearance` del llamante (en `COUNT` y filas), un clearance bajo no ve índices de expedientes clasificados. Requiere `USUA_PERM_EXPEDIENTE` (E15/E06 Inc.8).
- `POST /api/v1/expedientes/{id}/indice/firmar` — reintento **manual** del sellado XAdES del índice `pendiente_firma` (exige `USUA_PERM_EXPEDIENTE`+`PERM_FIRMA`, atribuido al humano). `409 indice_ya_firmado` si ya está firmado (idempotente), `503 index_signing_failed` si sigue sin poder sellarse. Un retry manual **re-arma** el backoff de reconciliación (`reconcile_agotado`→false) para que el job automático reanude tras corregir la causa raíz. El asiento `archive.indice_firmado` lleva `origen="manual"`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenera (nueva versión si cambió el conjunto o los metadatos; `{unchanged:true}` si idéntico). **`409 expediente_not_open`** si el expediente no está `open`. Se crea la **versión 0** automáticamente al crear el expediente. Requiere `USUA_PERM_EXPEDIENTE`. Cada regeneración deja un asiento `archive.indice_regenerated` (con `xsd_version`) en `audit_log`.
- Al vincular un radicado (`POST /api/v1/expedientes/{id}/radicados`) se puede aportar `content_hash` (SHA-256, `valor_huella`), los metadatos RT-15 **`formato`** (MIME), **`tamano_bytes`** y **`folios`** (opcionales, `ge=0`) y, en **v4**, la **`dependencia_productora_codigo`** (INTEGER) + **`dependencia_productora_nombre`** (texto) — opcionales pero **juntos o ninguno** (`422` el caso parcial). Se persiste un snapshot inmutable por versión del índice, de modo que `GET /indice?version=N` muestra la productora tal como estaba al generarse esa versión. Vincular/excluir regenera el índice automáticamente; sobre un expediente no `open` devuelve `409`.

#### `GET /api/v1/expedientes/search` (búsqueda FTS, E09)
Búsqueda full-text de expedientes (ADR-014). `q` opcional (`tsvector` sobre `code`/`name`/`description` + ranking `ts_rank`); filtros `?status=&date_from=&date_to=&meta.<campo>=`. Cabecera `X-Total-Count`. Sin texto = consulta filtrada ordenada por `opened_at`. **Control de acceso (RF-SEG-08 / RF-BUS-04):** solo se devuelven expedientes con `nivel_seguridad ≤` el *clearance* del llamante (*fail-closed* a PUBLICA). El nivel se fija al crear el expediente (`POST /api/v1/expedientes`, campo `nivel_seguridad`, default 1).

#### `/api/v1/tipos-documentales` (3.er nivel TRD, RF-MET-07)
CRUD del catálogo de tipos documentales (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Campos: `code`, `nombre`, `trd_serie_id` (serie/subserie), `obligatorio`, `metadata_template` (plantilla asociada). Filtros `?trd_serie_id=&activo=`. `409` si la `(serie, code)` ya existe.

#### `GET /api/v1/expedientes`
Listar con filtros `?status=open&page=1&size=20`.

#### `GET /api/v1/expedientes/{id}`
Obtener por UUID. Incluye lista de radicados vinculados. **Control de acceso (RF-SEG-08):** si el expediente supera el *clearance* del llamante, responde `404`.

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

#### `POST /api/v1/trd`
Crear serie documental.
```json
{ "code": "100", "name": "Contratos", "retention_years": 5, "total_retention": 10, "disposition": "conserve" }
```

#### `GET /api/v1/trd`
Listar series. Filtro: `?is_active=true&page=1&size=50`.

#### `GET /api/v1/trd/{id}`
Obtener serie por UUID.

#### `PATCH /api/v1/trd/{id}`
Actualizar serie (excepto `code`).

---

## notification-service — puerto 8007

### `POST /api/v1/notifications/send`
Envío de notificación por email. Responde `202` (aceptado para envío async). El historial se persiste en la BD del `tenant_slug` indicado.

```json
{ "recipient_email": "juridica@icetex.gov.co", "subject": "Nuevo radicado", "body": "...", "tenant_slug": "icetex", "sensible": false }
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
- `POST /api/v1/signature/sign-personal` `{payload_xml, objeto_tipo, objeto_id?, totp_code, firmante_id?}` — **firma personal PKI de un payload XML arbitrario** con el certificado del actor autenticado (E17/F4 Inc.1; primitivo `personal_signing.sign_personal_xml` reutilizado por la cadena de radicados y por la firma del acta de transferencia). Autenticado como **usuario** (`get_actor` + `PERM_FIRMA`, `X-User-Id`/JWT — **no** `X-Internal-Token`: lleva identidad + TOTP del firmante real, a diferencia de `/internal/sign-indice`). **ASSERT `firmante_id == user(JWT)`**: si el body trae `firmante_id` y difiere del actor derivado del token → `403 firmante_no_coincide` (no suplantable por el body; la fila persistida usa siempre el actor del JWT). Arma el XAdES con clave RSA efímera + el cert público real, pide a auth-service que firme el digest de `SignedInfo` **consumiendo el TOTP atómicamente** (split firma-remota), y sube el XML firmado a MinIO con nombre determinista que incluye el `firma_id` (patrón H-H, sin sobrescritura por carrera). Devuelve el `FirmaResponse` estándar (`firma_id`, `minio_key_firmado`, `xades_level`, `provider="xades_personal"`, `acreditado` **siempre `false`**, `nivel_conformidad='xades_personal_no_acreditado'`). El gate de clearance real solo se resuelve para `objeto_tipo="radicado"`; para otros tipos (p. ej. `acta_transferencia_personal`) el gate no-read-up lo aplica **el servicio mediador** (archive) **antes** de llamar — verificado que una llamada directa no es bypass (solo firma el payload que el propio caller aporta, no inyecta en las tablas de dominio). `4xx` si el TOTP es inválido; `503` si el canal de auth cae.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifica. **Firmas `xades_local`**: `payload_xml` debe ser el **XML firmado completo** (el de `minio_key_firmado`); valida criptografía + cobertura de la raíz + vigencia del cert + huella del sello + coincidencia con `signed_xml_sha256`; **XAdES-T** valida además el token RFC 3161 (cubre el `ds:SignatureValue`, firma CMS, cert TSA **atado a `firma.tsa_cert_fingerprint`**); **XAdES-LT/LTA** valida además (fail-closed) la **cadena `seal→CA`** (anclada a la CA **del tenant** vía pinning `ca_fingerprint` — un sello de la CA de otro tenant falla), la **firma y no-revocación de la CRL** (serial del sello no revocado), y el **`ArchiveTimeStamp`** sobre el conjunto cubierto determinista; `crl_next_update` se expone sin hard-fail si caducó (no acreditado). Si la firma trae **`xades:OCSPValues`** (E06 Inc.7), valida además (fail-closed) la respuesta **OCSP (RFC 6960)**: `responseStatus=successful`, responder emitido por la CA del tenant + vigencia, EKU `OCSPSigning` + `id-pkix-ocsp-nocheck`, firma del `BasicOCSPResponse`, **CertID atado al sello** (serial + issuer hashes), `certStatus=good`, y **pinning** del fingerprint del responder contra `firma.ocsp_responder_fingerprint` (sin él → fail-closed); su ausencia es retrocompat (LT válida sobre CRL sola). **Firmas `xades_personal`** (firma personal PKI por-usuario, E06/E17 Inc.1-2): valida cripto + cobertura + vigencia + huella del leaf; si la firma embebe la sub-CA (`xades:CertificateValues`, Inc.2) ancla además la **cadena leaf→sub-CA de usuarios del tenant** con pinning `user_ca_fingerprint` (rama separada de la del sello — no exige CRL embebida); y **consulta la revocación en línea** (estado en BD, sin caché) → `valida=false` (`motivo=certificado_revocado`) si el cert fue revocado. El veredicto se desglosa: `firma_criptograficamente_valida` (cripto+cadena a secas) y `revocacion_status ∈ {vigente, revocado, no_verificable, desconocido}`; `valida = cripto AND revocacion_status=='vigente'`; un canal de revocación caído → `valida=false`/`motivo=revocacion_no_verificable` pero `firma_criptograficamente_valida=true` (default fail-closed, `verify_personal_revocation_require`). Firmas `xades_personal` de Inc.1 (sin `CertificateValues`) verifican como XAdES-B/T (grandfather) pero **siguen siendo revocables** por identidad del cert. **Firmas `nativa`**: `payload_xml` es el original en claro. Devuelve `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?, sello_tiempo_valido?, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ocsp_cert_status?, firma_criptograficamente_valida?, revocacion_status?}` (en `/verify` el campo **autoritativo** del nivel realmente verificado es `xades_level`, derivado del resultado). Endpoint **público** (sin auth). `404` si la firma no existe.
- `GET /api/v1/signature/firmas/{firma_id}` — metadatos de una firma (mismos campos que `sign-xml`, sin `certificado_pem` completo). Público. `404` si no existe.
- *(Interno, servicio-a-servicio; **no** alcanzable desde el gateway — `X-Internal-Token` no está en `FORWARDED_REQUEST_HEADERS`)* `POST /api/v1/signature/internal/sign-indice` `{payload_xml, objeto_id, version, objeto_tipo?, indice_id?}` — sella un objeto XAdES-elegible **atribuido al principal de sistema `SYSTEM_RECONCILER_ID`** (no a un humano), para los **jobs de reconciliación** de archive-service (índice E15/E06 Inc.8, y acta de transferencia H-H). Autenticado **solo** por `X-Internal-Token` (fail-closed: settings vacío → 503, header ausente → 401, errado → 403); **no** exige `PERM_FIRMA` ni `X-User-Id`. `objeto_tipo` (default `"indice"`) se **valida contra el allow-set `{"indice","acta_transferencia"}`** (`422` fuera de él) → solo esos dos tipos producen el sello institucional del tenant (que NO deriva del actor); **estructuralmente no puede firmar la cadena personal**. El asiento `firma.creada` lleva `origen="reconciliation"`. La firma personal (`/requests/{id}/sign`, `/sign-xml`) NUNCA acepta este token.

**Cadena de firma (bandeja del firmante, RF-FIR-04).** Todas exigen `PERM_FIRMA`; el firmante se deriva del JWT (nunca del cuerpo, RF-FIR-11). El turno activo es el de menor `orden` aún pendiente. La firma personal exige **2FA** (RF-FIR-13): **OTP-correo** (E06 Inc.2) o, más fuerte, **TOTP RFC 6238** (posesión de dispositivo, E06 Inc.6, ver [ADR-016 addendum Inc.6](adr/ADR-016-firma-electronica.md#addendum--incremento-6-2026-07-06-2fa-por-totp-rfc-6238-en-la-firma-personal)). El secreto TOTP vive en **auth-service** (cifrado AESGCM); ver `POST /api/v1/auth/me/totp/*` abajo. **Formato de la firma personal (E06/E17 F4, Inc.1)**: si el firmante tiene una **clave de firma PKI activa** (ver `/me/signing-key/*`), la cadena produce una firma **XAdES-B/T por-usuario** con su propio certificado (verificable por terceros vía `/verify`, `provider=xades_personal`); si no, degrada a la **firma HMAC nativa** (`provider=nativa`, retrocompat). Ambas son firma electrónica **art. 7 no acreditada** (`acreditado=false`) — la PKI por-usuario añade verificabilidad/formato/separación criptográfica, no valor legal reforzado (custodia servidor ⇒ sin sole-control). **Gate XAdES por nivel (Inc.3, `FIRMA_PERSONAL_REQUIRE_XADES` ∈ `off`|`clasificados`|`todos`, default `off`)**: con `clasificados`, un acto de `nivel_seguridad>=2` **ya no degrada** a nativa — sin cert activo → **`422 firma_xades_requerida`** (con guía de enrolamiento), auth-service caído → **`503 canal_firma_personal_no_disponible`** (fail-closed); con `todos`, aplica en cualquier nivel. La denegación deja asiento `firma.xades_requerida_denegada` atómico y **no consume** ningún factor 2FA. En batch se evalúa **por ítem**. El env legacy bool se acepta (`false`→`off`, `true`→`todos`); un valor no reconocido aborta el arranque. Habilitar `clasificados` solo tras completar el enrolamiento de los firmantes con clearance>=2 (la emisión del cert es offline).
- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…]}` — crea la cadena (turnos `orden` 1..N). `409 cadena_existente` si el objeto ya tiene una cadena abierta. *Nota:* para firmar con 2FA la solicitud debe tener `payload_sha256` estable.
- `GET /api/v1/signature/requests/{id}` — estado de la cadena: cabecera + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — cadena abierta de un objeto (`404` si no hay).
- `GET /api/v1/signature/pending?page=&size=` — bandeja: solicitudes cuyo **turno activo** es del usuario autenticado (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** emite el reto 2FA: genera un OTP de 6 dígitos ligado a `(solicitud, firmante del turno activo, hash_documento)`, lo envía por correo al firmante y devuelve `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). Solo el firmante del turno activo (`403 no_es_su_turno`); `404 solicitud_no_encontrada` (neutro); **`422 payload_no_fijado`** si la solicitud no tiene `payload_sha256`; `429 challenge_cooldown` si se reemite antes de 30s; `503 canal_2fa_no_disponible` si el correo no se puede enviar (fail-closed). El OTP **nunca** aparece en la respuesta ni en logs.
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id?, otp?, totp_code?}` — firma el turno activo (reusa el firmante nativo HMAC) exigiendo el **2FA verificado**; avanza la cadena y, al firmar el último turno, la solicitud pasa a `firmado`. **Selección de factor**: si viene `totp_code` (6 dígitos) y el firmante tiene TOTP **activo** → se verifica contra auth-service (**no** requiere `/challenge` previo); si vienen `challenge_id`+`otp` → OTP-correo; si viene `totp_code` pero el firmante **no** tiene TOTP activo → **`422 totp_no_activo`** (no cae en silencio al correo); ambos → prevalece TOTP; ninguno → **`409 2fa_requerido`**. Errores TOTP: `401 totp_incorrecto` `{intentos_restantes}`, `423 totp_bloqueado`, `503 canal_2fa_no_disponible` si auth no responde (fail-closed → rollback). Errores OTP-correo: `401 otp_incorrecto` `{intentos_restantes}` (5 intentos → `challenge_bloqueado`), `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`, `403 challenge_ajeno`. Comunes: `403 no_es_su_turno`; `409` si la solicitud no está pendiente o se pierde la carrera del turno (lock `FOR UPDATE`). El 2FA es **no desactivable** para `nivel_seguridad ≥ 2` (RESERVADA/CLASIFICADA) — ambos factores lo satisfacen; TOTP es el más fuerte.
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rechaza el turno activo y **detiene** la cadena (solicitud `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id?, otp?, totp_code?}, …]}` — firma en lote los turnos del usuario, **un segundo factor por documento** (RF-FIR-13): OTP-correo (`challenge_id`+`otp`) o `totp_code`; no aborta ante un fallo individual (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). *(Contrato cambiado en E06 Inc.2: antes `{solicitud_ids:[…]}`.)*

### Envíos postales (E20) — document-service
Ciclo de vida del despacho físico de un radicado de salida. Máquina de estados `registrado → en_transito → entregado/devuelto/fallido` (`entregado`/`devuelto`/`fallido` terminales). Las rutas humanas exigen **`PERM_RADI_SALIDA`** y aplican no-read-up por *clearance*.
- `POST /api/v1/documents/{document_id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado con un operador (guía autogenerada). `201`. Resuelve el *clearance* del llamante → `404 radicado_not_found` si el radicado no existe **o** lo supera (indistinguibles): no se despacha lo que no se puede ver.
- `GET /api/v1/documents/{document_id}/envios` — envíos del radicado (filtrados por *clearance*).
- `GET /api/v1/envios?estado=&page=&size=` — listado transversal del tenant (`X-Total-Count`, no-read-up).
- `GET /api/v1/envios/{shipment_id}` — detalle (`404` si el radicado supera el *clearance* del llamante).
- `POST /api/v1/envios/{shipment_id}/estado` `{estado, descripcion?}` — transición **manual** (despacho humano). Resuelve el *clearance* del llamante → `404` si el radicado lo supera (cierre D-14); `409 transicion_invalida` para un salto no permitido.
- `GET /api/v1/envios/{shipment_id}/tracking` — **consulta por guía (pull, F5)** contra el operador (stub `consultar_estado`); devuelve `{estado_interno_actual, estado_operador, estado_interno_mapeado, operador_conectado}`. Solo lectura, no aplica la transición.
- `POST /api/v1/public/postal/callback/{tenant_slug}` `{operador, guia, estado_operador, event_id, descripcion?, motivo?}` — **webhook ENTRANTE del operador (F5, RF-POR-08)**. **Sin JWT** (bajo `/api/v1/public/`); autenticación **HMAC-SHA256** con cabecera `X-OrpycaMCP-Signature: sha256=<hex>` sobre el cuerpo crudo, con el secreto per-tenant/per-operador de `postal_operator_credential` (identidad propia del operador, separada del permiso humano — cierra D-14 #3). El `tenant_slug` del path fija el `search_path`; el secreto vive en el schema de ese tenant. **Idempotente** por `(operador, event_id)` (reintento → no-op). Mapea `estado_operador` a la máquina interna (`422 estado_operador_no_mapeable` si no mapea). Devuelve `{status: "applied"|"duplicate"|"recorded_no_change", shipment_id, estado}`. `401 firma_invalida` (sin credencial o firma mala, indistinguibles); `404 envio_no_encontrado` (guía desconocida); `400 cuerpo_invalido`. Una notificación que implicaría un retroceso (o sobre un terminal) se registra como `recorded_no_change` sin mutar el estado. Cada transición emite un evento `document.envio.*` para E16 y deja asiento en `audit_log` (`actor=postal:<operador>`).

### Interoperabilidad — OAI-PMH (E11, RF-INT-02/INT-06) — document-service

- `GET /api/v1/public/oai/{tenant}?verb=...` — **cosecha de metadatos OAI-PMH 2.0** de los radicados **públicos** del tenant. **Sin JWT** (bajo `/api/v1/public/`, como el webhook postal); el `tenant` del path fija el `search_path`. Respuesta `application/xml`. Verbos: `Identify`, `ListMetadataFormats`, `ListIdentifiers`, `ListRecords`, `GetRecord` (args `identifier`+`metadataPrefix`), `ListSets`. Cosecha selectiva `from`/`until` (fecha OAI) y `set` (= `doc_type` E/S/I). Identificador OAI `oai:{tenant}:{tracking_number}`. Paginación por `resumptionToken` (100/página; el token preserva el `metadataPrefix`). Errores OAI estándar (`badVerb`, `badArgument`, `cannotDisseminateFormat`, `idDoesNotExist`, `noRecordsMatch`, `badResumptionToken`).
  - **Dos formatos de diseminación (RF-INT-06):**
    - `metadataPrefix=oai_dc` — **Dublin Core** simple (title=asunto, identifier=número de radicado, type, date, publisher, language).
    - `metadataPrefix=ead` — **EAD 2002 / ISAD(G)** (namespace `urn:isbn:1-931666-22-9`): cada radicado se disemina como un fragmento EAD autocontenido con `<archdesc level="item">` (unidad documental simple) y los **6 elementos obligatorios de ISAD(G)/NTC 4095 para intercambio** — 3.1.1 `<unitid countrycode="CO" repositorycode>` (código de referencia país+repositorio+número), 3.1.2 `<unittitle>` (asunto), 3.1.3 `<unitdate normal>` (fecha), 3.1.4 `@level="item"`, 3.1.5 `<physdesc>` (soporte), 3.2.1 `<origination>` (dependencia origen) — más 3.4.1 `<accessrestrict>` (público, Ley 1712/2014), 3.7.2 `<descrules>` (ISAD(G) 2ª ed. / NTC 4095), `<langmaterial>` y `<repository>`. **Deuda declarada** (roadmap): descripción monolvel `item`; el contexto multinivel fondo→sección→serie→expediente (archive-service, TRD/CCD) se inyectará en un fast-follow.
  - **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** el recorte (`_PUBLIC` en la fuente) es **ortogonal al formato** — elegir `ead` no sortea el filtro ni crea oráculo de reservados; un id reservado/anulado/inexistente → `idDoesNotExist` indistinguible en ambos formatos.
- **Control de acceso (CRÍTICO, Ley 1712/2014 arts. 18-19, RF-SEG-08):** la cosecha **solo** expone lo **público** (`nivel_seguridad=1`, no anulado). El recorte se aplica en la fuente, así la omisión de reservados/clasificados es **indistinguible**: `completeListSize` cuenta solo público, `ListSets` no enumera un *set* solo-reservado, y `GetRecord`/`ListMetadataFormats` sobre un identificador reservado devuelven `idDoesNotExist` (no confirman su existencia). No hay credencial de tercero con clearance en este alcance (cosecha pública).

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

- `POST /api/v1/export` `{comentario?, year?, doc_type?}` — **exporta radicados a un paquete interoperable** (ZIP). **Autenticado** (no público); gate `USUA_PERM_EXPEDIENTE` + **no-read-up por-radicado** (solo se exporta lo que el llamante puede leer; los sobre-clearance se omiten y se reportan content-free en el manifiesto). Respuesta `application/zip` con cabeceras `X-Export-UUID`, `X-Export-Count`, `X-Export-Excluded`. Contenido del ZIP: `manifiesto.json` (UUID de exportación, comentario, marcas inicio/fin, lista de radicados con SHA-256, excluidos), `esquema/radicado.schema.json` (**JSON Schema draft 2020-12** publicado), `radicados/{numero}.json` (datos por radicado que **validan contra el esquema**: metadata sistema+contextual, disposición, clasificación, anexos, historial), `anexos/{numero}/{i}-{archivo}` (binarios), `checksums.txt` (SHA-256 por miembro). El **número de radicado se preserva** como identidad (Ac. AGN 060/2001). Empaquetado físico ZIP provisional (BagIt/OAIS+WORM = E10). Filtros opcionales `year` (2000–2100) y `doc_type` (E/S/I).

- `POST /api/v1/import` — **importa un paquete interoperable** de radicados. **Autenticado**; gate `USUA_PERM_EXPEDIENTE` + **no-write-up por clearance**. Recibe el ZIP de INT-01 como **cuerpo crudo** (`Content-Type: application/zip`, tope 512 MiB). **Validación TODO-o-NADA antes de ingerir**: valida cada radicado contra el JSON Schema de confianza del servicio y **verifica el SHA-256 de cada binario**; si algo falla → `422 paquete_invalido` **sin dejar estado parcial**. Reingiere **preservando el número de radicado** (una colisión se **omite**, no se sobreescribe); reconstruye la relación E↔S (`responde_a`) por número; sube los binarios a storage; audita `radicado.import`. Guardas anti zip-slip/zip-bomb. Respuesta `{import_uuid, importados, omitidos_existentes, omitidos_clearance, relaciones_reconstruidas}`. Un radicado con `nivel_seguridad` > clearance del importador se **omite** (`omitidos_clearance`).

### Capa MCP (E18, ADR-019) — mcp-server, puerto 8009
Servicio separado (no detrás del gateway): expone la API como *tools* a agentes/LLM, siendo él mismo cliente del gateway. **Protocolo MCP** disponible por **stdio** (`python -m app.mcp_stdio`) y **HTTP streamable** (`POST /mcp`, contexto por cabeceras `Authorization`/`X-Tenant-Slug`/`X-User-Permissions`/`X-User-Roles`). API HTTP simple del catálogo:
- `GET /api/v1/mcp/tools` — catálogo de tools visibles para el usuario (filtra por `X-User-Permissions`; `X-User-Roles` con `ROOT` ve todas).
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invoca la tool: propaga `Authorization` + `X-Tenant-Slug` al gateway. Operaciones de escritura exigen `X-Confirm-Write: true` (`412` si falta) y el permiso correspondiente (`403` si falta). Tools: `radicar_documento`, `consultar_radicado`, `buscar_radicados`, `consultar_expediente`, `indice_expediente`, `listar_trd`, `bandeja_tramite`, `consultar_fuid`, `buscar_conocimiento` (búsqueda semántica E21; POST `/api/v1/knowledge/search`, solo lectura, ACL server-side).

### Asistente conversacional (E18, ADR-019/ADR-020) — vía gateway
A diferencia del catálogo/protocolo MCP (que **no** se expone por el gateway), el asistente conversacional **sí** se enruta por el `api-gateway` (`/api/v1/assistant/` → mcp-server), de modo que hereda su validación JWT y la inyección de `X-Tenant-Slug`. El mcp-server orquesta un **loop LLM (Claude)** que ejecuta las tools de **solo lectura** del catálogo como llamadas al gateway **re-propagando el token del usuario** — el asistente corre *as-the-user*, nunca con credenciales propias, y cada tool revalida su RBAC/clearance en el backend.
- `POST /api/v1/assistant/message` `{mensaje: string(1..4000), conversation_id?: string}` — envía un turno del usuario. Devuelve `{conversation_id, message: {id, role:"assistant", texto, formato:"markdown", ts}}`. El `conversation_id` (generado si se omite) mantiene el hilo; el historial es **efímero por proceso** y **aislado por tenant** (un `conversation_id` nunca cruza tenants). **v1 = SOLO LECTURA**: la única tool de escritura del catálogo (`radicar_documento`) **se excluye** del conjunto ofrecido al modelo (defensa en profundidad: filtrada al construir el prompt y denegada de nuevo en el despacho). El contenido devuelto por las tools (p. ej. radicados de Entrada redactados por terceros) es **dato, nunca instrucción** (frontera anti-inyección en el system prompt). `503 assistant_unavailable` si el asistente no está configurado (sin `ANTHROPIC_API_KEY`) o el SDK falla — **degradación honesta**, nunca una respuesta simulada (RF-FIR-15); `401` sin token; `409 conversation_limit` al agotar el tope de turnos del hilo. Respuestas de tool `403`/`404` se comunican de forma **genérica** ("no encontré ese registro"), sin revelar la existencia de registros clasificados (no-oráculo, RF-SEG-08 / Ley 1712/2014).

### Capa de conocimiento (E21, ADR-006) — knowledge-service, puerto 8011
Capa **advisoria/derivada y opt-in** (no es fuente de verdad). Embeddings con proveedor pluggable (stub local por defecto).
- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — calcula el embedding y persiste el fragmento (pgvector) con su ACL. Para proteger contenido clasificado, el ingestor debe incluir el nivel en `acl.nivel_seguridad` (1–3); ausente = PUBLICA (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — recuperación semántica (kNN coseno). **Control de acceso resuelto en el servidor (RF-SEG-08 / RF-BUS-04):** se devuelven **siempre** solo fragmentos con `nivel_seguridad ≤` el *clearance* del llamante (derivado del JWT/BD, no del cuerpo de la petición); el `acl` opcional del cliente es solo *narrow* adicional (p. ej. por dependencia), nunca amplía el acceso. Devuelve `[{id, source_type, source_ref, texto, metadata, score}]`. Los *tombstones* (fragmentos purgados por reclasificación) quedan **excluidos** de la búsqueda.
- `POST /api/v1/knowledge/antecedentes` `{query|radicado_ref, source_type?, top_k?}` (XOR `query`/`radicado_ref`, E21 Inc.2, Patrón A) — recuperación pura ANCLADA y CITADA, sin LLM: reusa `search` (mismo pre-filtro ACL), devuelve `{antecedentes:[{source_type, source_ref, snippet, score}], advisory}` con un aviso **fijo** (CC-01/CC-02). `radicado_ref` inexistente **o** que excede el clearance del solicitante colapsan al mismo `404 antecedente_source_not_found` (anti-oráculo, sin 403 diferenciado).
- `POST /api/v1/knowledge/rag` `{query, top_k?}` (E21 Inc.3, Patrón B) — RAG generativo con citas. Recupera con el mismo pre-filtro ACL de `search` y, si `AI_PROVIDER` es un proveedor de generación real (`ollama_local` default soberano, `ollama_cloud`/`openai_compatible` externos, `anthropic` con Citations API nativa), sintetiza una respuesta ANCLADA. **Barrera dura de soberanía (sin override)**: con proveedor externo, excluye del contexto enviado al LLM los fragmentos `nivel_seguridad ≥ RESERVADA` — visibles por ACL, pero nunca egresados a un tercero; si no queda contexto generable, cae a recuperación pura (Patrón A) sobre todo lo recuperado. `AI_PROVIDER=disabled` (o no configurado) también cae siempre a Patrón A. Devuelve `{answer, citations:[{cited_text, source_type, source_ref, score}], proveedor, modelo, confianza, suggestion_id, advisory}` — `answer`/`modelo`/`suggestion_id` son `null` cuando no hubo generación real. Cada generación real se registra en `kb_suggestions` (CC-03: `proveedor`/`modelo` en columnas separadas) y en `audit_log`. `503 rag_unavailable` ante cualquier fallo del proveedor (SDK ausente, credencial ausente, error de red/API) — degradación honesta (RF-FIR-15), nunca una respuesta simulada.

> **Ingesta event-driven (E21, MVP):** además del `POST /ingest` manual, el knowledge-service **consume `orpycamcp.document.events`** (consumer group `knowledge-ingest` + DLQ, ADR-021) y auto-indexa cada radicado al crearse (`document.radicado.created` → `subject`/`sender`, `source_type="radicado"`, `source_ref=tracking_number`). El `nivel_seguridad` se **replica del evento** (fail-closed a CLASIFICADA si falta) y se **mantiene consistente ante reclasificación** (`document.radicado.reclassified`): una subida a RESERVADA+ con proveedor de embeddings externo **purga el contenido** (tombstone), y el nivel de un fragmento **nunca baja** salvo por una reclasificación explícita, sea cual sea el orden de llegada de los eventos. Material RESERVADA+ **no se embebe** con un proveedor externo (queda registrado en `audit_log`). Un radicado **anulado** (`document.radicado.annulled`) se marca *voided* de forma **permanente** y queda excluido de la búsqueda a cualquier clearance (no se re-indexa aunque un evento de creación se reprocese después). Es una capa **derivada/advisoria** (ADR-006): no es fuente de verdad; el registro legal vive en document/archive.

### Webhooks salientes (E11, ADR-018)
- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — suscribe un endpoint externo (`event_types` vacío = todos los eventos). El `secret` no se devuelve. **Exige `USUA_PERM_ADMIN`** (todo el CRUD): registrar una suscripción entrega cada evento del tenant a la URL indicada, así que es config de administración — sin el gate sería un canal de exfiltración (y SSRF).
- `GET /api/v1/webhooks` — lista las suscripciones (`X-Total-Count`). Exige `USUA_PERM_ADMIN`.
- `DELETE /api/v1/webhooks/{id}` — elimina una suscripción. Exige `USUA_PERM_ADMIN`.
- Al ocurrir un evento de dominio, OrpycaMCP hace `POST` del sobre canónico a las suscripciones que coinciden, firmado en la cabecera `X-OrpycaMCP-Signature: sha256=<hmac>` (best-effort con reintentos). El receptor debe ser idempotente.

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

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter?status=pending&origin_stream=&page=&size=` | Lista paginada (`X-Total-Count`). Solo cabeceras (sin payload de negocio) y **filtrada por clearance del llamante** (no-read-up fila-a-fila sobre el nivel vivo del radicado referenciado, misma resolución que el detalle): una fila cuyo objeto excede el nivel del admin se omite por completo (no aparece ni cuenta en `X-Total-Count`), consistente con el `404` neutro del detalle. Fail-closed ante cualquier error de resolución. La paginación se aplica en la capa de aplicación, después del filtrado. El conjunto de candidatas se acota a `1000` antes de filtrar; si se alcanza ese tope la respuesta incluye el header **`X-Truncated: true`** (advierte que `X-Total-Count` puede sub-reportar). |
| `GET` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}` | Detalle (sobre íntegro + fallo). Gateado por clearance (no-read-up sobre el nivel vivo del radicado; `404` **neutro** si insuficiente o inexistente —cuerpo byte-idéntico—). Una vista autorizada deja un asiento `evento.dead_letter.viewed` en `audit_log` (una vista bloqueada NO audita, para no confirmar existencia). |
| `POST` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}/replay` | Reprocesa el evento. `200` → `status='resolved'`; `409` si no está `pending`; `404` si no existe o sin clearance. **signature/workflow**: reproceso idempotente y atómico; `502` si falla (queda `pending`). **notification** (correo no transaccional): *best-effort claim-then-send* —limpia el dedup y reenvía—; `422` si el `origin_stream` es desconocido o el tenant no cuadra, `502` si el envío falla (queda `pending`). |
| `POST` | `/api/v1/{signature\|workflow\|notifications}/admin/deadletter/{event_id}/discard` | Cierra como `discarded`. Body `{ "reason": "..." }` (obligatorio, ≥10 caracteres tras `strip`). Mismos códigos. |

Toda acción (`replay`/`discard`/`viewed`) se registra en el `audit_log` inmutable con el actor humano (`X-User-Id`). En notification la **auditoría es dependencia dura** de la disposición: si el módulo de auditoría no está disponible, `replay`/`discard` responden `503` **antes** de transicionar (no se dispone sin dejar traza legal). Los eventos malformados sin `tenant_slug` (`SYSTEM_TENANT`) no son replayables (gestión de plataforma).

---

## Batch API — document-service

> Permite crear múltiples radicados o expedientes en una sola llamada. El procesamiento es asincrónico.

### `POST /api/v1/batch/documents`
Crea un job para registrar 1-1000 radicados. Retorna `202 Accepted` inmediatamente.

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

### `GET /api/v1/batch/{job_id}/status`
Monitorea el progreso del job.

```json
// Response 200
{
  "job_id": "uuid", "job_type": "documents",
  "status": "completed",  // pending | processing | completed | failed
  "total_items": 2, "processed_items": 2, "failed_items": 0,
  "items": [
    { "item_index": 0, "status": "success", "result_id": "uuid-radicado" },
    { "item_index": 1, "status": "failed", "error_message": "..." }
  ]
}
```

---

## Batch API — archive-service

### `POST /api/v1/batch/expedientes`
Crea 1-100 expedientes con radicados vinculados. Retorna `202 Accepted`.

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

> El `GET /api/v1/batch/{job_id}/status` también aplica para expedientes.

---

## Full-Text Search & Reportes — document-service

### Plantillas y borradores de Salida (paridad legado)
- `GET/POST/DELETE /api/v1/plantillas` — plantillas reutilizables (`nombre`, `cuerpo`, `tipo_documental`).
- `POST /api/v1/borradores` `{doc_type, subject, dest_dept?, cuerpo?, plantilla_id?}` — crea un borrador (si se da `plantilla_id` y no `cuerpo`, hereda el cuerpo de la plantilla).
- `GET /api/v1/borradores` (`?estado=`), `GET /{id}`, `PATCH /{id}` (editar solo si no radicado), `POST /{id}/aprobar` (borrador→aprobado), `POST /{id}/radicar` (→ genera el radicado real y marca el borrador `radicado`).

### Respuesta rápida (paridad legado)
- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, observations?, response_days?}` — crea un radicado de **Salida** en respuesta a este (hereda asunto `RE: …` y dependencia si se omiten), enlazado al antecedente (`responde_a`). Requiere permiso `PERM_RESPUESTA_VINCULADA`. `403 forbidden` si falta. Devuelve el nuevo radicado. **No-read-up (RF-SEG-08):** `404` neutro si el antecedente no existe o supera el *clearance* del llamante — precisamente porque la Salida **hereda el asunto** del antecedente, responder sin ese gate exfiltraría el asunto de una Entrada reservada a un documento legible.
- `GET /api/v1/documents/{id}/respuestas` — salidas que responden a este radicado.

### Anulación de radicado en dos pasos (paridad legado)
La ley prohíbe borrar radicados: se anulan con aprobación supervisora.
- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — solicita (paso 1). Requiere permiso `PERM_SOL_ANULAR`. `403 forbidden` si falta. `409` si ya está anulado o hay solicitud pendiente. **Las cuatro rutas de anulación** aplican no-read-up (RF-SEG-08): un radicado que supera el *clearance* del llamante devuelve el mismo `404` que uno inexistente — la causal y el motivo describen el documento.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — aprueba (paso 2; nivel jefe): el radicado pasa a `anulado` (el número se conserva). Requiere `PERM_PANU_CODI`. `403 forbidden` si falta.
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rechaza. Requiere `PERM_PANU_CODI`. `403 forbidden` si falta. `GET /api/v1/documents/{id}/anulacion` — última solicitud.

### Firma electrónica (E06, ADR-016)
- `POST /api/v1/documents/{id}/signatures` `{ "content_hash": "<sha256 hex>", "anexo_id"?, "reason"? }` — firma el radicado/anexo: registra identidad (claims del gateway), `content_hash` y sello de tiempo. `422` si el hash no es SHA-256. **Las tres rutas de firma** exigen `PERM_FIRMA` (el mismo permiso que gobierna signature-service) y aplican no-read-up: `404` neutro si el radicado no existe o supera el *clearance*.
- `GET /api/v1/documents/{id}/signatures` — lista las firmas (`X-Total-Count`). `404` si el radicado no es visible; `[]` (200) si es visible y aún no tiene firmas — un sub-*clearance* recibe siempre `404`, nunca `[]`, así que el par no es oráculo.
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` si alguna firma del radicado coincide con ese hash (si el contenido cambió, deja de verificar). Gateado igual que el listado: sin el filtro, el `valid` true/false sería un **oráculo de contenido** sobre documentos reservados.

### Envíos postales (E20)
- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — despacha el radicado por operador postal (4-72/servientrega/otro); genera la guía y estado `registrado`.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — envíos del radicado / detalle con historial de eventos.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — actualiza el estado de entrega (callback/sondeo del operador). Máquina de estados `registrado→en_transito→entregado|devuelto|fallido`; `409` si la transición es inválida.

### `POST /api/v1/ingest/email/poll` (ingesta de correo, E19)
Operación interna (requiere JWT). Trae los correos no leídos de la bandeja IMAP configurada, crea un radicado de Entrada por cada uno (`metadata.source=email`, remitente y anexos en `metadata`) y los marca como leídos. Tenant en `X-Tenant-Slug`. Respuesta `{ "ingested": n, "tracking_numbers": [...] }`. `503` si IMAP no está configurado (`IMAP_HOST` vacío).

### `POST /api/v1/public/{tenant}/pqrs` (radicación ciudadana, Ley 1755/2015)
**Sin autenticación.** Un ciudadano radica una PQRS: `{ "tipo": "peticion|queja|reclamo|sugerencia|denuncia", "nombre", "identificacion"?, "email"?, "asunto", "descripcion" }`. Crea un radicado de Entrada (`metadata.canal=pqrs_ciudadano`) y devuelve `{radicado_id, tracking_number, verification_code}` para seguimiento posterior con el endpoint de verificación.

### `GET /api/v1/public/{tenant}/verify/{code}` (consulta pública, E13)
**Sin autenticación.** Verifica la trazabilidad de un radicado por su código de verificación (token no adivinable, distinto del número de radicado). El tenant viaja en la ruta. Devuelve solo información NO sensible: `tracking_number`, `doc_type`, `status`, `registered_at`, `dest_dept`. `404` si el código no existe; `400` si el slug de tenant es inválido.

### `GET /api/v1/reports/radicados`
Resumen estadístico de radicados del tenant (E09): `total` y conteos `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia`. Filtros opcionales `?date_from=&date_to=`. Agregaciones SQL nativas (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`
Exporta el resumen en **CSV** (`seccion,clave,valor` con secciones tipo/estado/mes/dependencia) — para reportes periódicos al AGN/entes de control. Mismos filtros `?date_from=&date_to=`.

```json
{ "total": 8, "by_doc_type": {"E": 5, "S": 3}, "by_status": {"registered": 6, "archived": 2},
  "by_month": [{"month": "2024-01", "count": 4}, {"month": "2024-02", "count": 4}] }
```

### `GET /api/v1/reports/indice-reservado` · `.csv`
**Índice de información clasificada y reservada** (E08, Ley 1712/2014 art. 20 + Decreto 1081/2015). Registro de todos los radicados `nivel_seguridad ≥ 2` con la metadata del acto de clasificación. Gate **`PERM_RECLASIFICAR`**. **CONTENT-FREE** (nunca expone el asunto/contenido) y **ORTOGONAL al clearance** (el registro es COMPLETO por mandato legal — es una publicación de metadata, no una lectura de contenido). Mapeo: `nivel 2 = reservada → art. 19`, `nivel 3 = clasificada → art. 18`. Cada ítem: `{numero_radicado, tipo_radicado (E/S/I, NO serie TRD), nivel_seguridad, clasificacion, excepcion_ley_1712, fundamento_juridico, causal_reserva, plazo_reserva_meses, fecha_clasificacion, fecha_radicacion}`. La metadata se puebla al **radicar clasificado** o **reclasificar** (ambos exigen `fundamento` al pasar a nivel≥2, `422` si falta — Ley 1712 art. 19/28) y se limpia al desclasificar. El `.csv` neutraliza inyección de fórmulas en los campos de texto libre. La generación deja una entrada agregada en `audit_log` (RF-BUS-10).

### `GET /api/v1/search`
Búsqueda avanzada de radicados (E09, ADR-014: PostgreSQL FTS). Texto completo con ranking (`tsvector`/`ts_rank`) + difuso (`pg_trgm`), combinable con filtros estructurados.

Busca en: `tracking_number` (peso A), `subject` (B), `sender_name` / `sender_entity` (C), `dest_dept` / `observations` (D).

**Query params** (todos opcionales): `q` (texto, min 2 chars — **opcional**: sin texto es una consulta filtrada ordenada por fecha), `doc_type`, `status`, `date_from`/`date_to` (rango sobre `registered_at`), `dest_dept_code`, `meta.<campo>=valor` (filtro por metadato, contención JSONB/GIN), `page`, `size`. Cabecera `X-Total-Count`.

**Control de acceso (RF-SEG-08 / RF-BUS-04):** además del aislamiento por tenant (`search_path`), los resultados se acotan por la **clasificación** del radicado: cada usuario solo ve los de `nivel_seguridad ≤` su *clearance* (nivel máximo accesible = MAX de la matriz rol→nivel `role_clearance` sobre sus grupos; `is_root` accede a todo). El clearance lo resuelve la BD por request (ADR-013); política *fail-closed* a PUBLICA si no hay identidad o clearance asignado. La **misma acotación** se aplica al listado `GET /api/v1/documents` y a la búsqueda de expedientes `GET /api/v1/expedientes/search`.

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

## Asistente conversacional — mcp-server (E18/E22, ADR-020) — **contrato definido, backend PENDIENTE**

> El **frontend** del asistente está implementado (E22: store, service, proxy BFF, UI de chat en `AssistantDock`); el **backend** (`mcp-server`, LLM/knowledge reales) es scaffold pendiente de roadmap. El proxy SvelteKit apunta a este contrato y, mientras el gateway no lo exponga, traduce la ausencia (404/501/5xx/conexión rehusada) a un `503 assistant_unavailable` — la UI muestra "asistente no disponible", nunca una respuesta simulada.

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
- Preparado para **streaming** (no construido): un `POST .../message/stream` (NDJSON/SSE) con el mismo `conversation_id` y `message.id` estables por turno permitiría *appendear* deltas al mismo turno sin cambiar el shape.
- **Nota de contrato (a reconciliar)**: el proxy envía `X-Username` desde el claim `preferred_username` (el `SessionUser` del front no tiene `username`).
