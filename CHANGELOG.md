# Changelog — OrpycaMCP

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.0.0/) + [Conventional Commits](https://www.conventionalcommits.org/)

---

## [Unreleased]

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
