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
Metadatos de disposición del documento (RF-MET-08): `programa`, `retention_until`, `accion` (conservar/eliminar/transferir/seleccionar), `confirmado`, `marcado_eliminacion`. `PATCH` hace merge de los campos provistos. `404` si el radicado no existe.

### `GET /api/v1/documents/{id}`
Obtener por UUID. Incluye lista de anexos. **Control de acceso (RF-SEG-08):** si el radicado supera el *clearance* del llamante, responde `404` (no revela su existencia).

### `GET /api/v1/documents/by-tracking/{tracking_number}`
Obtener por número de radicado (ej: `2024-ICETEX-E-000001`). Misma acotación por clearance que el detalle por UUID (`404` si excede el nivel del llamante).

### `PATCH /api/v1/documents/{id}`
Actualizar `subject`, `dest_dept`, `observations`, `status`, `pages`.
`tracking_number`, `doc_type`, `year`, `sequence` son inmutables.

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
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` — registro inmutable PREMIS (INGESTA/FIJACION/MIGRACION/VALIDACION_PDFA/WORM/REPLICA).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, nombre, valor_huella?}], indice_sha256?}` — empaqueta el **AIP** (BagIt + PREMIS) y devuelve `bagit`, `premis_xml`, `aip_sha256`. `GET /api/v1/preservacion/aip/{expediente_id}` recupera el AIP vigente.

---

## workflow-service — puerto 8006

> Requiere headers `X-Tenant-Slug` y `X-User-Id`.

> **Distribución automática (RF-FLU-04):** además de los endpoints, workflow-service consume el stream `orpycamcp.document.events`. Al radicarse un documento (`document.radicado.created`) evalúa las reglas activas y, si alguna casa, **autoasigna** el radicado a su dependencia (paso + evento append-only, autor = sistema). Es idempotente (no redistribuye si el radicado ya tiene pasos).

### `POST /api/v1/workflows/assign`
Asigna un radicado a una dependencia (primer paso o reasignación).

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
Transfiere el radicado a otra dependencia. Requiere `from_dept`.

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
Barrido de vencimientos (RF-FLU-07): detecta pasos activos vencidos no alertados, emite `workflow.step.overdue` al bus (→ notificaciones) y los marca para no duplicar. Idempotente por paso; pensado para invocación periódica por un scheduler. Responde `{ "alerted": N }`.

### Vistos buenos secuenciales (paridad legado)
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — crea la cadena ordenada de revisión (1..20 revisores, sin duplicados). Exige `PERM_RADI` (D-09); `403 forbidden` si falta permiso, identidad o tenant. `409 cadena_existente` si el radicado ya tiene cadena.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — el revisor de turno (menor orden pendiente) aprueba/rechaza. Requiere `X-User-Id` válido (400 si falta). `403 not_a_reviewer` si el actor no está en la cadena de revisores de ese radicado; `403 no_es_su_turno` si está en la cadena pero no es su turno; un rechazo detiene la cadena.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — cadena + estado global (`en_revision|aprobado|rechazado`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (devolución al remitente)
Re-enruta un radicado mal asignado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Cierra el paso activo como `returned` y crea uno nuevo hacia `to_dept`, con la causal en el historial. Autorización por tenencia: solo el responsable actual del paso activo puede devolver. `403 not_current_holder` si el actor no es el tenedor. `404` si no hay paso activo.

### Transacciones de trámite (RF-FLU-01)
- `GET /api/v1/workflows/transaction-types` — catálogo (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), con su permiso atómico y efecto de estado.
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — ejecuta la transacción: aplica el efecto al paso activo (p. ej. `marcar_leido`→in_progress, `anular`→cancelled) y registra el evento append-only. `404 unknown_transaction` / `no_flow`.

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

- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, ubicacion_origen_id?, ubicacion_destino_id?}` — crea (precondición: expediente `closed`; `409 expediente_not_closed` si no). Ciclo `preparada→enviada→recibida|rechazada`.
- `POST /{id}/enviar`, `POST /{id}/recibir` (al recibir, el expediente pasa a `transferred` y queda congelado), `POST /{id}/rechazar`.
- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (entregable: FUID de E17 filtrado por el expediente).

#### Archivo físico (E17, ADR-017)

> **Control de acceso (D-08):** todos los endpoints requieren `USUA_PERM_EXPEDIENTE`. Sin él: `403 {"error": "forbidden"}`. `GET /expedientes/{id}/unidades` también lo requiere; el frontend lo llama via `Promise.allSettled` y trata el 403 como error de faceta sin bloquear la vista.

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — topología recursiva (sede→…→gaveta); `codigo` único por tenant; `ruta` derivada. Filtros `?parent_id=&activo=`.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — unidades de conservación (caja|carpeta|…); al indicar `ubicacion_id` se deriva la **signatura topográfica** (`DEP01-E05-C0124`, única). Filtros `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — vincular expediente a unidad (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — qué contiene la unidad. `GET /api/v1/expedientes/{id}/unidades` — dónde está el expediente (signatura + rango).
- **Préstamos (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 si ya prestada), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` se deriva de `fecha_devolucion_esperada`.
- **Historial (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — movimientos append-only (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — inventario documental (expediente↔unidad↔signatura↔serie) en JSON con orden consecutivo y campos AGN 042/2002; filtros `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exporta en XML.

#### TRD/CCD (E04, ADR-015)
- `POST/GET/PATCH /api/v1/trd` — series y **subseries** documentales. Campos: `code`, `name`, `parent_id` (subserie), `archivo_gestion_years`/`archivo_central_years` (retención en dos fases), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`.
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **calendario de retención**: `fin_archivo_gestion`, `fin_archivo_central` y disposición final (`disposition_code`/`disposition_label`) derivados de la serie y la fecha de cierre.

#### `POST /api/v1/expedientes/{id}/radicados/batch` (inclusión masiva)
Vincula varios radicados de una vez `{radicados:[{radicado_id, tracking_number, content_hash?}]}` (hasta 500; idempotente, omite los ya vinculados); regenera el índice una sola vez. Devuelve `{vinculados, omitidos, total}`. `409` si el expediente no está `open`.

#### Ciclo de vida del expediente (E02)
- `POST /api/v1/expedientes/{id}/close` — cierra el expediente (`open→closed`), materializa la disposición TRD en `disposition` y **genera+firma (XAdES-B) la versión final del índice electrónico** vía signature-service (E06 Inc.1). El sellado es **best-effort y no bloquea el cierre**: si la firma se obtiene deja `expediente_indice.estado='firmado'`; si falla (sello no aprovisionado, servicio inalcanzable) deja **`estado='pendiente_firma'`** y emite el audit `archive.indice_firma_pendiente`. El audit de cierre incluye `indice_estado`/`firma_id`.
- `POST /api/v1/expedientes/{id}/indice/firmar` — **reintenta** el sellado XAdES-B de un índice `pendiente_firma` (E06 Inc.1). Idempotente: `409` si ya `firmado`, `503` si el sellado sigue sin obtenerse. Requiere `USUA_PERM_EXPEDIENTE` **+ `PERM_FIRMA`** y `X-User-Id` (400 si el UUID es malformado).
- `POST /api/v1/expedientes/{id}/transfer` — transfiere (`closed→transferred`). `409` si la transición es inválida **o si el índice no está `firmado`** (un índice `pendiente_firma` bloquea la transferencia — Acuerdo AGN 001/2024 art. 4.3.2.4). El mismo bloqueo aplica en el `PATCH` genérico con `status=transferred` y en la recepción de transferencias (E12).
- `GET /api/v1/expedientes/{id}/eventos` — **hoja de ruta append-only del expediente** (E02 §9, RF-EXP-09): lista cronológica de `{id, accion, actor, comentario, detalles, ts}` con `accion` ∈ `abierto|radicado_vinculado|radicado_excluido|cerrado|transferido|...`. **Control de acceso (Canal B / RF-SEG-08):** visible a quien pueda **leer** el expediente, acotada por su clearance (no read-up); no requiere admin. Distinta de `audit_log` (auditoría forense) y de `/unidades/{id}/movimientos` (movimientos físicos, E17).
- `GET /api/v1/expedientes/{id}/foliado` — foliado: radicados ordenados con `folio` secuencial (`{expediente_id, code, total_folios, items[]}`). (El **índice electrónico** normativo es E15, abajo.)

#### Índice electrónico (E15, Acuerdo AGN 001/2024)
- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — índice vigente o por versión: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`.
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — cuerpo XML (`application/xml`, namespace `urn:orpycamcp:indice:v1`).
- `GET /api/v1/expedientes/{id}/indice/versions` — historial de versiones (append-only) con `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifica integridad: contrasta el `valor_huella` del índice contra el hash actual de cada documento. Con firma XAdES-B descarga el XML firmado desde MinIO y lo verifica contra signature-service — el bloque `firma` incluye `{presente, valida, nivel_conformidad, acreditado, motivo}` (`motivo='signed_xml_unavailable'` si el XML firmado no se pudo recuperar → `valida=false`, nunca declara conforme lo no verificado). Requiere `USUA_PERM_EXPEDIENTE`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenera (nueva versión si cambió el conjunto; `{unchanged:true}` si idéntico). Se crea la **versión 0** automáticamente al crear el expediente. Requiere `USUA_PERM_EXPEDIENTE`.
- Al vincular un radicado (`POST /api/v1/expedientes/{id}/radicados`) se puede aportar `content_hash` (SHA-256 del documento), que el índice usa como `valor_huella`. Vincular/excluir regenera el índice automáticamente; sobre un expediente no `open` devuelve `409`.

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

#### `DELETE /api/v1/expedientes/{id}/radicados/{radicado_id}`
Desvincular radicado. `204`.

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
Envío manual de notificación por email. Responde `202` (aceptado para envío async). El historial se persiste en la BD del `tenant_slug` indicado.

```json
{ "recipient_email": "juridica@icetex.gov.co", "subject": "Nuevo radicado", "body": "...", "tenant_slug": "icetex", "sensible": false }
```

- `sensible` (opcional, default `false`): marca el correo como **sensible** (p. ej. el OTP del 2FA de firma, RF-FIR-13). Con `sensible=true` el correo se **envía por SMTP con el body real** pero el historial persiste el `body` **redactado** (`[contenido sensible omitido]`) y un `last_error` genérico — el secreto nunca queda en BD/backups. Todo emisor de correos con secretos **debe** fijar este flag.

### `GET /api/v1/notifications/history`
Historial de notificaciones del tenant (persistido en BD por institución). Requiere `X-Tenant-Slug`. Query: `?limit=20`.

### `GET /api/v1/notifications/{id}`
Obtener notificación por UUID. Requiere `X-Tenant-Slug`. `404` si no existe.

### Firma electrónica — signature-service, puerto 8008 (E06, ADR-016)
Proveedor **enchufable** (`app/providers/`): `nativa` (HMAC hash+identidad, default para todo objeto) y **`xades_local`** (XAdES-B enveloped real vía `signxml`, activo solo para el **índice electrónico**). Selección: `xades_local` cuando `objeto_tipo=="indice"` **y** `SIGNER_PROVIDER=="xades_local"` (default; kill-switch `SIGNER_PROVIDER=nativa`); cualquier otro caso → `nativa`. Ver [ADR-016 addendum Inc.1](adr/ADR-016-firma-electronica.md#addendum--incremento-1-2026-07-02-xades-b-del-índice-electrónico--sello-institucional).
- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version?}` — firma el payload y persiste la firma. Con `objeto_tipo="indice"` produce **XAdES-B** con el sello institucional del tenant y exige `PERM_FIRMA` + `X-User-Id` (el firmante **nunca** sale del cuerpo, RF-FIR-11). Devuelve `{firma_id, payload_sha256, provider, minio_key_firmado?, xades_level?, nivel_conformidad?, tipo_firma?, certificado_fingerprint?, acreditado, ...}`. `acreditado` **siempre `false`** en Inc.1 (sin CA acreditada ONAC, RF-FIR-15). `503 signing_seal_unavailable` si el tenant no tiene sello propio montado (fail-closed; con `SIGNER_ALLOW_DEV_SEAL=true`, solo dev, cae al par `_dev`); `503 signed_index_upload_failed` si storage-service rechaza la subida.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifica. **Firmas `xades_local`**: `payload_xml` debe ser el **XML firmado completo** (el de `minio_key_firmado`); valida criptografía + cobertura de la raíz + vigencia del cert + huella del sello + coincidencia con `signed_xml_sha256` de esa `firma_id`. **Firmas `nativa`**: `payload_xml` es el original en claro (sin cambios). Devuelve `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?}`. Endpoint **público** (sin auth). `404` si la firma no existe.
- `GET /api/v1/signature/firmas/{firma_id}` — metadatos de una firma (mismos campos que `sign-xml`, sin `certificado_pem` completo). Público. `404` si no existe.

**Cadena de firma (bandeja del firmante, RF-FIR-04).** Todas exigen `PERM_FIRMA`; el firmante se deriva del JWT (nunca del cuerpo, RF-FIR-11). El turno activo es el de menor `orden` aún pendiente. La firma personal exige **2FA por OTP-correo** (RF-FIR-13, E06 Inc.2, ver [ADR-016 addendum Inc.2](adr/ADR-016-firma-electronica.md#addendum--incremento-2-2026-07-02-2fa-por-otp-correo-en-la-firma-personal-rf-fir-13)).
- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…]}` — crea la cadena (turnos `orden` 1..N). `409 cadena_existente` si el objeto ya tiene una cadena abierta. *Nota:* para firmar con 2FA la solicitud debe tener `payload_sha256` estable.
- `GET /api/v1/signature/requests/{id}` — estado de la cadena: cabecera + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — cadena abierta de un objeto (`404` si no hay).
- `GET /api/v1/signature/pending?page=&size=` — bandeja: solicitudes cuyo **turno activo** es del usuario autenticado (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** emite el reto 2FA: genera un OTP de 6 dígitos ligado a `(solicitud, firmante del turno activo, hash_documento)`, lo envía por correo al firmante y devuelve `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). Solo el firmante del turno activo (`403 no_es_su_turno`); `404 solicitud_no_encontrada` (neutro); **`422 payload_no_fijado`** si la solicitud no tiene `payload_sha256`; `429 challenge_cooldown` si se reemite antes de 30s; `503 canal_2fa_no_disponible` si el correo no se puede enviar (fail-closed). El OTP **nunca** aparece en la respuesta ni en logs.
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id, otp}` — firma el turno activo (reusa el firmante nativo HMAC) exigiendo el **2FA verificado**; avanza la cadena y, al firmar el último turno, la solicitud pasa a `firmado`. **`409 2fa_requerido`** si falta el challenge verificado; `401 otp_incorrecto` `{intentos_restantes}` (a los 5 intentos → `challenge_bloqueado`); `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`; `403 challenge_ajeno`/`no_es_su_turno`; `409` si la solicitud no está pendiente o se pierde la carrera del turno (lock `FOR UPDATE`). El 2FA es **no desactivable** para `nivel_seguridad ≥ 2` (RESERVADA/CLASIFICADA).
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rechaza el turno activo y **detiene** la cadena (solicitud `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id, otp}, …]}` — firma en lote los turnos del usuario, **un OTP por documento** (RF-FIR-13); no aborta ante un fallo individual (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). *(Contrato cambiado en E06 Inc.2: antes `{solicitud_ids:[…]}`.)*

### Capa MCP (E18, ADR-019) — mcp-server, puerto 8009
Servicio separado (no detrás del gateway): expone la API como *tools* a agentes/LLM, siendo él mismo cliente del gateway. **Protocolo MCP** disponible por **stdio** (`python -m app.mcp_stdio`) y **HTTP streamable** (`POST /mcp`, contexto por cabeceras `Authorization`/`X-Tenant-Slug`/`X-User-Permissions`/`X-User-Roles`). API HTTP simple del catálogo:
- `GET /api/v1/mcp/tools` — catálogo de tools visibles para el usuario (filtra por `X-User-Permissions`; `X-User-Roles` con `ROOT` ve todas).
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invoca la tool: propaga `Authorization` + `X-Tenant-Slug` al gateway. Operaciones de escritura exigen `X-Confirm-Write: true` (`412` si falta) y el permiso correspondiente (`403` si falta). Tools: `radicar_documento`, `consultar_radicado`, `buscar_radicados`, `consultar_expediente`, `indice_expediente`, `listar_trd`, `bandeja_tramite`, `consultar_fuid`.

### Capa de conocimiento (E21, ADR-006) — knowledge-service, puerto 8011
Capa **advisoria/derivada y opt-in** (no es fuente de verdad). Embeddings con proveedor pluggable (stub local por defecto).
- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — calcula el embedding y persiste el fragmento (pgvector) con su ACL. Para proteger contenido clasificado, el ingestor debe incluir el nivel en `acl.nivel_seguridad` (1–3); ausente = PUBLICA (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — recuperación semántica (kNN coseno). **Control de acceso resuelto en el servidor (RF-SEG-08 / RF-BUS-04):** se devuelven **siempre** solo fragmentos con `acl.nivel_seguridad ≤` el *clearance* del llamante (derivado del JWT/BD, no del cuerpo de la petición); el `acl` opcional del cliente es solo *narrow* adicional (p. ej. por dependencia), nunca amplía el acceso. Devuelve `[{id, source_type, source_ref, texto, metadata, score}]`.

### Webhooks salientes (E11, ADR-018)
- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — suscribe un endpoint externo (`event_types` vacío = todos los eventos). El `secret` no se devuelve.
- `GET /api/v1/webhooks` — lista las suscripciones (`X-Total-Count`).
- `DELETE /api/v1/webhooks/{id}` — elimina una suscripción.
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
- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, observations?, response_days?}` — crea un radicado de **Salida** en respuesta a este (hereda asunto `RE: …` y dependencia si se omiten), enlazado al antecedente (`responde_a`). Requiere permiso `PERM_RESPUESTA_VINCULADA`. `403 forbidden` si falta. Devuelve el nuevo radicado.
- `GET /api/v1/documents/{id}/respuestas` — salidas que responden a este radicado.

### Anulación de radicado en dos pasos (paridad legado)
La ley prohíbe borrar radicados: se anulan con aprobación supervisora.
- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — solicita (paso 1). Requiere permiso `PERM_SOL_ANULAR`. `403 forbidden` si falta. `409` si ya está anulado o hay solicitud pendiente.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — aprueba (paso 2; nivel jefe): el radicado pasa a `anulado` (el número se conserva). Requiere `PERM_PANU_CODI`. `403 forbidden` si falta.
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rechaza. Requiere `PERM_PANU_CODI`. `403 forbidden` si falta. `GET /api/v1/documents/{id}/anulacion` — última solicitud.

### Firma electrónica (E06, ADR-016)
- `POST /api/v1/documents/{id}/signatures` `{ "content_hash": "<sha256 hex>", "anexo_id"?, "reason"? }` — firma el radicado/anexo: registra identidad (claims del gateway), `content_hash` y sello de tiempo. `422` si el hash no es SHA-256.
- `GET /api/v1/documents/{id}/signatures` — lista las firmas (`X-Total-Count`).
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` si alguna firma del radicado coincide con ese hash (si el contenido cambió, deja de verificar).

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
