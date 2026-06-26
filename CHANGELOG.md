# Changelog — OrpycaMCP

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.0.0/) + [Conventional Commits](https://www.conventionalcommits.org/)

---

## [Unreleased]

### Fixed

- `fix(auth)`: **D-07 — espejo RBAC de la UI roto** — *Por qué*: `GET /auth/me` devolvía `UserInfoResponse` sin el campo `permissions`; el frontend hacía `me.permissions ?? []` → siempre vacío → controles de UI desactivados para todos — *Impacto*: `UserInfoResponse` incluye ahora `permissions: list[str]`; el endpoint resuelve los permisos efectivos desde la BD del tenant reutilizando exactamente `RBACService.resolve_effective_permissions` (mismo que `GET /users/{id}/permissions`); usuarios root reciben `USUA_PERM_ROOT` en la lista (coherente con `has_permission()`); fail-closed: `[]` cuando no hay `X-Tenant-Slug` o el usuario no está aprovisionado. Nuevo `get_optional_tenant_conn` en `database.py`. 4 nuevos tests (verdes). `ruff check` limpio.

### Security

- `fix(document,workflow)`: **D-06 — hueco de autorización en acciones de operador** — *Por qué*: 4 endpoints de escritura (solicitar/aprobar/rechazar anulación, respuesta vinculada, devolver, vistos-buenos/decidir) solo exigían presencia de `X-User-Id`; cualquier usuario autenticado podía ejecutarlos sin importar su rol — *Impacto*: `POST /{id}/anulacion` exige `PERM_SOL_ANULAR`; `aprobar`/`rechazar` exigen `PERM_PANU_CODI` (nivel jefe, fiel al legacy); `POST /{id}/respuesta` exige `PERM_RESPUESTA_VINCULADA`; `devolver` verifica que el actor sea el tenedor actual del paso activo (autorización por tenencia, fiel al legado); `vistos-buenos/decidir` requiere `X-User-Id` válido + membership en la cadena de revisores del radicado (error `not_a_reviewer` si no pertenece, `no_es_su_turno` si no es su turno). Comprobación DB-based (ADR-013: `auth_permissions`/`auth_group_permissions`/`auth_memberships` en el schema del tenant, fail-closed). Migración `auth-service tenant/007` (dos permisos nuevos: `PERM_SOL_ANULAR`, `PERM_RESPUESTA_VINCULADA`). 10 nuevos tests (76 document + 52 workflow verdes). `ruff check` limpio
- `fix(frontend)`: **D-05 — el `access_token` deja de viajar al cliente** — *Por qué*: el token se serializaba al navegador vía `data.token`, exponiéndolo a XSS/exfiltración — *Impacto*: 9 endpoints proxy SSR (`(app)/**/api/+server.js`) hacen las llamadas al gateway server-side leyendo `locals.accessToken` (URL **interna** desde `config.server.js`), con guarda de sesión (401 si no hay token) y saneo del parámetro `version` del índice; eliminada la capa de servicios cliente que llamaba al gateway desde el navegador (`*.service.js` + `ENDPOINTS`), reemplazada por tipos puros en `$lib/types/api.js`. Verificado por `svelte-ux-reviewer`; `npm run check` y `spec-drift.sh` en verde
- `feat(auth,signature)`: **guard fail-fast del contrato de issuer** — *Por qué*: una mala config (host interno de Docker sin `KEYCLOAK_ISSUER`) hace `expected_issuer` ≠ `iss` real y el servicio rechaza **todos** los tokens silenciosamente (401 genéricos) — *Impacto*: `auth-service` y `signature-service` registran al arranque el `iss` esperado y emiten un WARNING explícito en ese caso roto; sin falsos positivos en dev (override presente) ni en prod single-host (FQDN). Nuevos tests unitarios `issuer_config_warnings` (auth 49 verdes, signature 21 verdes)
- `refactor(auth)`: **migración `python-jose` → `PyJWT[crypto]`** (`>=2.10.0`) — *Por qué*: `python-jose` está mínimamente mantenida y arrastra CVEs (CVE-2024-33663 algorithm confusion, CVE-2024-33664 DoS JWE); PyJWT es el estándar activo — *Impacto*: `security.py` resuelve la clave del JWKS por `kid` (`PyJWKSet.from_dict`) antes de `jwt.decode`; excepciones `jose.JWTError` → `jwt.PyJWTError`; nuevos tests que firman JWT RS256 reales y verifican firma/issuer/expiración/kid (`test_token_validation.py`)
- `fix(auth)`: validación del emisor (`iss`) en los JWT — *Por qué*: antes se verificaba firma RS256 y expiración pero **no el issuer**, por lo que un token firmado por el mismo Keycloak para otro realm/cliente podía aceptarse — *Impacto*: `jwt.decode` pasa `issuer=settings.expected_issuer`; nuevo setting `KEYCLOAK_ISSUER` (opcional, por defecto derivado de `KEYCLOAK_URL`+`KEYCLOAK_REALM`) para el desfase URL pública/interna en Docker
- `fix(deps)`: elevados suelos de versión con CVE conocido — `python-multipart` `>=0.0.9`→`>=0.0.18` (CVE-2024-53981 DoS), `mcp` `>=1.2.0`→`>=1.12.0` (alinear con la API actual del SDK)
- `chore(build)`: **dependencias congeladas con lockfiles** — *Por qué*: los `requirements.txt` usaban solo `>=` sin tope ni lock, así que builds en fechas distintas resolvían versiones diferentes (cadena de suministro no reproducible) — *Impacto*: nuevo `requirements.lock` por servicio (pin transitivo exacto generado con `uv pip compile`, Python 3.11) y los `Dockerfile` instalan desde el `.lock`; el `requirements.txt` permanece como entrada legible de alto nivel. 44 tests auth verdes con las versiones pineadas (incl. `pytest 9.x`, `pytest-asyncio 1.4`). Regenerar: `uv pip compile services/<svc>/requirements.txt -o services/<svc>/requirements.lock`

### Changed

- `refactor(rebrand)`: renombrado completo `orfeomcp` → **`OrpycaMCP`** / **`orpycamcp`** antes de la v0.01
  — *Por qué*: el proyecto es la evolución de Orfeo bajo el concepto **Orpyca** ("Orfeo Pyme Calidad") — el Orfeo para pymes — con conexión a IA vía **MCP** (Model Context Protocol)
  — *Impacto*: convención por superficie — **marca** `OrpycaMCP`; **token de datos/infra** `orpycamcp` (BD `orpycamcp_db`, usuario/credenciales, realm Keycloak `orpycamcp`, buckets `orpycamcp-{slug}-*`, streams Redis `orpycamcp.*`, paquete Python `orpycamcp_common`, red `orpycamcp-net`, contenedores `orpycamcp-*`, emails dev `@orpycamcp.local`); **repo/imágenes/proyecto Compose** `orpyca-mcp`; **empresa/web/org** `Orpyca`/`orpyca.com`/GitLab `orpyca`. **Requiere recrear volúmenes** (postgres/minio/keycloak): los datos de desarrollo previos no son compatibles. La carpeta raíz del repo se renombra a `orpyca-mcp` (paso manual)

- `docs(claude)`: `CLAUDE.md` sincronizado con la arquitectura real — 11 microservicios con puertos correctos; eliminadas referencias a SQLAlchemy/Alembic (contradecían ADR-003: asyncpg + SQL crudo); fases actualizadas al estado specDrive; rutas corregidas (proxy del gateway, runner de migraciones)

- `chore(ci)`: CI unificada en **GitLab** — eliminado `.github/workflows/` (duplicado); README/badges/clone/Issues/BibTeX apuntan a `gitlab.com/orpyca/orpyca-mcp`; corregido el paso del job de docs que referenciaba un archivo borrado

- `fix(workflow)`: hoja de ruta del radicado acotada por clearance (E05 §10 / RF-SEG-08) — `GET /workflows/{id}/events` exige `X-User-Id` y filtra por la visibilidad del radicado (no read-up); deja de ser legible por cualquiera del tenant. Canal B (hoja de ruta operativa), no auditoría forense

- `feat(archive)`: hoja de ruta del expediente (E02 §9 / RF-EXP-09) — tabla `expediente_eventos` append-only e inmutable (migración `archive tenant/014`), evento emitido en apertura/vinculación/exclusión/cierre/transferencia, y `GET /api/v1/expedientes/{id}/eventos` acotado por clearance

- `feat(audit)`: cobertura de `public.audit_log` (E08 §9, Canal A) — `workflow-service` y `archive-service` emiten también a la auditoría forense transversal vía `orpycamcp_common.audit` (antes solo document + auth); archive-service ahora instala la librería compartida

- `chore(compose)`: puertos publicados de desarrollo movidos a un rango alto y distinto (**19xxx / 15432 / 16379**) para no chocar con otras pilas de dev — gateway 19080, auth 19001, tenant 19002, document 19003, archive 19004, storage 19005, workflow 19006, notification 19007, signature 19008, mcp-server 19009, knowledge 19011, postgres 15432, redis 16379, MinIO 19900/19901, Keycloak 19180. Los puertos internos de los contenedores y la comunicación por la red Docker no cambian

- `fix(compose)`: `signature-service` y `knowledge-service` ahora esperan a `postgres` *healthy* (`depends_on: condition: service_healthy`) — antes arrancaban con `depends_on` plano y salían en el arranque con `ConnectionRefused` contra una BD aún no lista

### Added

- `feat(infra)`: **frontend en su propio contenedor** (dev `docker-compose.yml` `19300:3000` y `infra/docker-compose.prod.yml`) — *Por qué*: el frontend no estaba en Compose; faltaba el split de URLs y la coherencia del issuer de Keycloak — *Impacto*: split público/interno (`config.js` navegador vs `config.server.js` SSR), `ORIGIN`/`PROTOCOL_HEADER`/`HOST_HEADER` para que adapter-node derive bien el `redirect_uri` del PKCE detrás del proxy; pinning de issuer (`KC_HOSTNAME_URL` en Keycloak + `KEYCLOAK_ISSUER` en auth/signature) para el desfase navegador `localhost:19180` ↔ red `keycloak:8080`; `realm-export.json` con `redirectUris`/`webOrigins` de `19300` y usuarios dev completos. Verificado E2E (login PKCE → token con `iss` público → gateway `HTTP 200`). Documentado en `docs/es/deployment.md` (contrato de issuer + frontend SSR)
- `feat(frontend)`: **E22 Fase 1 (plantilla base)** — arranca la app de presentación SvelteKit en `frontend/` (ADR-011/020) — *Por qué*: dar UI humana a la plataforma, antes solo API/MCP — *Impacto*: **T-01/T-02** sistema de diseño Orpyca por tokens (`_tokens.scss`: variables Sass `$op-*` + custom properties `--op-*` para rebranding por tenant en runtime; Bulma + layout + utilidades; regla 60/30/10); **T-04** cliente del `api-gateway` (`api.service.js`: bearer + `X-Tenant-Slug`, refresh en 401, error normalizado); **T-03** login OAuth2 **Authorization Code + PKCE** server-side contra Keycloak (`orpycamcp-frontend`), tokens en cookies httpOnly (nunca en el bundle), `state` anti-CSRF, guarda de sesión SSR + ruta `403` *(pendiente smoke test e2e con Keycloak levantado)*; **T-05** `AppLayout` (sidebar/topbar/breadcrumb/dock) con navegación filtrada por `permissions[]`/`roles[]` del JWT (espejo del RBAC; el backend re-valida); **T-06** biblioteca de 11 componentes accesibles (Button, Card, DataTable, FormField, Modal, Toast, StatusChip, EmptyState, Loader, Drawer, Stepper) + catálogo `/_kit`. Revisado por `svelte-ux-reviewer`: corregidos open redirect en el callback (validación por `URL.origin`), IDs ARIA únicos y trampa de foco en modal/drawer, y tokenización de colores de estado/focus. `svelte-check`/ESLint/`vite build` (adapter-node) verdes en Docker.
- `feat(frontend)`: **E22 Fase 2 — vistas núcleo consolidadas** — *Por qué*: resolver los flujos clave del operador en una sola vista (anti multi-pestaña del legado Orfeo) — *Impacto*: **T-07** Dashboard (KPIs por estado/tipo/dependencia vía `GET /reports/radicados`); **T-08** Bandeja unificada de radicados (filtros, paginación `X-Total-Count`, drawer de detalle y acciones inline sin navegar, sobre `GET/PATCH /documents`); **T-09** Radicación guiada E/S/I en un stepper de 5 pasos (tipo→metadatos→anexos drag-drop→TRD→destino, confirmación atómica con `POST /documents` y `POST /storage/upload`, catálogos por SSR paralelo); **T-10** Workspace de expediente con 5 facetas en pestañas (Resumen·Índice E15·Anexos E07·Flujo/Historial·Ubicación física E17) compuestas en SSR con `Promise.allSettled` (sin endpoint agregado backend). Todas consumen contratos reales del backend vía gateway con `X-Tenant-Slug` y UI por rol. Faltantes anotados: acciones avanzadas (anular/devolver/responder/VoBo → T-08b), filtros por fecha/dependencia/texto (→ T-11), endpoint agregado de anexos por expediente (deuda backend). `svelte-check`/ESLint/`vite build` verdes en Docker.

> **Plan specDrive — conformidad SGDEA (F1–F6, 21 épicas).** El detalle granular por épica vive en [`docs/es/changelog.md`](docs/es/changelog.md). Resumen consolidado (núcleo implementado, ~295 tests verdes en 10 microservicios, 20 ADRs, migraciones validadas en PostgreSQL/pgvector real):
> - **F1 Fundamentos**: librería común `orpycamcp_common` (ADR-010), migraciones por tenant (ADR-012), administración (E14), seguridad RBAC + auditoría inmutable + clasificación (E08), radicación (E01), metadatos JSONB + plantillas (E03), anexos (E07), notificaciones (E16), flujos + reglas + transacciones (E05).
> - **F2 Consulta**: búsqueda avanzada + reportes + búsqueda de expedientes (E09, ADR-014 PostgreSQL FTS), consulta pública por código de verificación (E13), ingesta de correo IMAP (E19).
> - **F3 Ciclo de vida**: TRD/CCD jerárquico + retención en dos fases + disposición AGN (E04, ADR-015), ciclo de vida del expediente cierre/transferencia/foliado (E02), firma electrónica (E06, ADR-016).
> - **F4 Conformidad**: índice electrónico XML versionado append-only + huella por documento + verificación (E15).
> - **F5 Preservación/físico**: archivo físico + FUID (E17, ADR-017), transferencias primarias/secundarias (E12), interoperabilidad por webhooks firmados (E11, ADR-018), plan de preservación + eventos PREMIS (E10).
> - **F6 Inteligencia**: mcp-server — capa MCP cliente del gateway (E18, ADR-019); knowledge-service — pgvector + recuperación semántica con pre-filtrado ACL (E21, ADR-006).

- `feat(auth)`: auth-service — autenticación Keycloak ROPC, validación JWT con JWKS cacheado, audit log en PostgreSQL, endpoints `/token`, `/refresh`, `/logout`, `/validate`, `/me`
  — *Por qué*: dependencia fundacional de todos los servicios; sin auth no hay trazabilidad legal
  — *Impacto*: cualquier servicio puede validar un JWT llamando a `/validate` sin lógica propia

- `feat(tenant)`: tenant-service — CRUD de instituciones con provisioning automático de schema PostgreSQL `tenant_{slug}`
  — *Por qué*: el multi-tenancy es un requisito fundacional de Orfeo; cada institución debe tener aislamiento completo de datos
  — *Impacto*: agregar una nueva institución crea automáticamente su schema y está lista para operar

- `feat(gateway)`: api-gateway — proxy HTTP centralizado con validación JWT, rate limiting por IP (Redis), inyección de headers de identidad (`X-User-Id`, `X-Tenant-Slug`, `X-User-Roles`)
  — *Por qué*: los servicios internos no deben conocer Keycloak; el gateway resuelve auth una sola vez
  — *Impacto*: los clientes tienen un único punto de entrada; los servicios reciben identidad ya validada

- `feat(document)`: document-service — radicación de documentos E/S/I con numeración atómica (`{AÑO}-{CÓDIGO}-{TIPO}-{SEQ:06d}`), gestión de anexos como referencias UUID a storage-service
  — *Por qué*: la radicación es el core legal del sistema; el número es inmutable y requerido por Ley 594/2000
  — *Impacto*: cualquier comunicación oficial puede registrarse con trazabilidad completa

- `feat(storage)`: storage-service — upload a MinIO con deduplicación por SHA-256, buckets por tenant, URLs pre-firmadas para descarga directa
  — *Por qué*: separar el almacenamiento de archivos del registro documental permite escalar independientemente
  — *Impacto*: el mismo archivo no se almacena dos veces en el mismo tenant; la descarga no pasa por el servicio

- `feat(workflow)`: workflow-service — flujos de distribución de radicados entre dependencias, historial de pasos, eventos en Redis Stream `orpycamcp.workflow.events`
  — *Por qué*: el flujo de distribución es la segunda función más crítica de Orfeo tras la radicación
  — *Impacto*: cada movimiento de un radicado queda registrado con responsable y timestamp

- `feat(archive)`: archive-service — expedientes (`open→closed→transferred`) con vinculación de radicados, TRD con series documentales y tiempos de retención
  — *Por qué*: la Ley 594/2000 exige organizar los documentos en expedientes y aplicar la TRD
  — *Impacto*: las instituciones pueden cumplir con los requisitos de archivo y disposición documental

- `feat(notification)`: notification-service — consumer de Redis Streams, envío de emails SMTP, historial en Redis con TTL 30 días
  — *Por qué*: las dependencias deben ser notificadas cuando reciben un radicado sin polling
  — *Impacto*: el workflow publica un evento y la notificación llega sin acoplamiento directo

- `refactor(all)`: retirar SQLAlchemy y Alembic de todo el proyecto — usar asyncpg directo y SQL crudo con migraciones en archivos `.sql`
  — *Por qué*: SQLAlchemy agregaba complejidad innecesaria; asyncpg directo es más simple y explícito
  — *Impacto*: menos dependencias, SQL visible y auditable, migraciones como archivos SQL simples

### Fase 4 — Calidad e Integración (2026-06-04)

- `infra(lint)`: pyproject.toml con configuración centralizada de ruff y mypy — todos los 8 servicios pasan linting
  — *Por qué*: la calidad de código es requisito para pasar a producción; centralizar config en una sola fuente
  — *Impacto*: cada commit es verificado automáticamente para imports no usados, type hints, y violaciones de estilo

- `infra(health)`: health check orquestado en api-gateway — `GET /health` verifica Redis + todos los 7 servicios en paralelo con asyncio.gather()
  — *Por qué*: saber el estado global del sistema sin hacer 8 llamadas separadas
  — *Impacto*: los clientes y orquestadores ven degraded (503) si un servicio crítico falla

- `test(e2e)`: suite de integración end-to-end en docker-compose.e2e.yml — tests de flujo completo (crear tenant → registrar documento → buscar)
  — *Por qué*: los tests unitarios no capturan interacciones entre servicios
  — *Impacto*: detectamos antes de producción si un servicio rompió a sus clientes

- `docs(adr)`: ADR-003 documenta la decisión de retirar SQLAlchemy y usar asyncpg directo
  — *Por qué*: futuro contribuidores entienden por qué el proyecto no usa ORMs
  — *Impacto*: claridez arquitectónica + decisión documentada para auditoría

---

## [0.1.0] — 2026-06-03 (scaffold inicial)

### Added

- `feat(infra)`: estructura base del proyecto con 8 microservicios FastAPI
- `feat(infra)`: docker-compose.yml con PostgreSQL 15, MinIO, Keycloak 24, Redis 7 y MailHog
- `feat(infra)`: realm Keycloak `orpyca` con roles RBAC y usuarios de desarrollo
- `feat(infra)`: init-db.sql con schema isolation por tenant
- `feat(ci)`: pipeline GitLab CI/CD con validación, revisión Claude, documentación automática y GitLab Pages
- `feat(docs)`: MkDocs Material con documentación bilingüe es/en
- `feat(adr)`: ADR-001 arquitectura microservicios con FastAPI
- `feat(adr)`: ADR-002 multi-tenancy con schema isolation PostgreSQL
- `feat(framework)`: agentes Claude Code especializados y memoria persistente del proyecto
