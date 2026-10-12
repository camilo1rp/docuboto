# Decisiones de Arquitectura (ADRs)

Los ADRs (Architecture Decision Records) documentan las decisiones técnicas importantes: qué se decidió, por qué, y qué alternativas se descartaron.

Los archivos completos están en `docs/es/adr/`.

## Índice

| ADR | Título | Estado |
|---|---|---|
| [ADR-001](adr/ADR-001-microservices-architecture.md) | Arquitectura de microservicios con FastAPI | Aceptado |
| [ADR-002](adr/ADR-002-multi-tenancy-schema-isolation.md) | Multi-tenancy con schema isolation en PostgreSQL | Aceptado |
| [ADR-003](adr/ADR-003-asyncpg-sin-orm.md) | asyncpg directo en lugar de SQLAlchemy ORM | Aceptado |
| [ADR-005](adr/ADR-005-batch-api.md) | API de operaciones por lotes (batch) | Aceptado |
| [ADR-006](adr/ADR-006-proveedor-ia-configurable.md) | Proveedor de IA configurable (multi-proveedor, global) para la capa de conocimiento | Propuesto |
| [ADR-007](adr/ADR-007-metadatos-jsonb.md) | Metadatos documentales con JSONB validado por plantilla | Propuesto |
| [ADR-008](adr/ADR-008-auditoria-inmutable.md) | Auditoría inmutable con `audit_log` encadenado por hash | Propuesto |
| [ADR-009](adr/ADR-009-historial-flujos-eventos.md) | Historial de flujos como eventos append-only + proyección | Propuesto |
| [ADR-010](adr/ADR-010-libreria-compartida.md) | Librería compartida `orpycamcp_common` (auditoría y eventos) | Aceptado |
| [ADR-011](adr/ADR-011-frontend-sveltekit-monorepo.md) | Frontend SvelteKit (SSR) como app de presentación en el monorepo | Propuesto |
| [ADR-012](adr/ADR-012-migraciones-por-tenant.md) | Migraciones por tenant (runner `global/` + `tenant/`) | Aceptado |
| [ADR-013](adr/ADR-013-autorizacion-resuelta-en-bd.md) | Autorización resuelta en la BD por request (Keycloak autentica, la BD autoriza) | Aceptado |
| [ADR-014](adr/ADR-014-busqueda-postgresql-fts.md) | Búsqueda con PostgreSQL FTS (tsvector + pg_trgm + JSONB), no Elasticsearch | Aceptado |
| [ADR-015](adr/ADR-015-trd-ccd-modelo.md) | Modelo TRD/CCD: jerarquía serie↔subserie, retención en dos fases, disposición AGN (CT/E/S/M) | Aceptado |
| [ADR-016](adr/ADR-016-firma-electronica.md) | Firma electrónica nativa (hash SHA-256 + identidad + sello de tiempo), PKI/PDF diferida | Aceptado |
| [ADR-017](adr/ADR-017-archivo-fisico.md) | Archivo físico: ubicación (dirección recursiva) ≠ unidad de conservación (contenedor móvil), patrón ArchivesSpace | Aceptado |
| [ADR-018](adr/ADR-018-interoperabilidad-webhooks.md) | Interoperabilidad saliente por webhooks HTTP firmados (HMAC-SHA256) sobre el bus de eventos, suscritos por tenant | Aceptado |
| [ADR-019](adr/ADR-019-capa-mcp.md) | Capa MCP como fachada fina cliente del gateway (catálogo de tools declarativo, sin lógica ni BD) | Aceptado |
| [ADR-020](adr/ADR-020-sistema-diseno-orpyca-asistente.md) | Sistema de diseño Orpyca (tokens Sass + custom properties, branding por tenant) y asistente conversacional (texto/voz) como traductor a tools MCP con IA local opt-in | Propuesto |
| [ADR-021](adr/ADR-021-reclamacion-deadletter-eventos.md) | Reclamación de PEL y dead-letter por grupo consumidor en Redis Streams (signature/workflow/notification-service) | Aceptado |
| [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md) | Perfil v2 del índice electrónico — metadatos RT-15 por documento (formato, tamaño, foliación) | Aceptado |
| [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md) | WORM / Object-Lock de preservación (MinIO) y hoja de ruta AIP/PREMIS | Aceptado (Increment A) |
| [ADR-024](adr/ADR-024-root-fuera-de-banda.md) | `USUA_PERM_ROOT` no se concede desde la API — el administrador de entidad no es el techo de su entidad; ROOT solo se aprovisiona fuera de banda | Aceptado |
| [ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md) | La TRD es un instrumento convalidado, no una tabla de configuración: versionado append-only y fijación de la versión que gobierna cada expediente | Implementado (Increment 1+2) |
| [ADR-026](adr/ADR-026-tvd-fondo-acumulado.md) | La TVD es el mismo instrumento con otro contenido: discriminador `tipo_instrumento` sobre `trd_series`, no tabla propia (registro y circuito de convalidación + amarre y calendario del fondo acumulado) | Implementado (Increment 1+2) |
| [ADR-027](adr/ADR-027-sustitucion-almacen-objetos-rustfs.md) | MinIO archivó su proyecto libre y retiró sus imágenes: el almacén pasa a **RustFS**, y el motor lo elige una batería de conformidad WORM contra un S3 real (RustFS 7/7, MinIO 7/7, Garage 0/7), no un folleto | Implementado |
| [ADR-028](adr/ADR-028-el-flujo-mueve-el-estado-del-radicado.md) | El flujo mueve el estado del radicado: una función pura escrita en la misma transacción que el paso, cierre solo por «Finalizar trámite», `closed`/`anulado` terminales (409) y tenencia en `marcar_leido`; remediación de auditoría: clearance y tenencia en `assign`/`transfer`, la anulación cancela el paso, evidencia inmutable del backfill | Implementado |
| [ADR-029](adr/ADR-029-ubicacion-actual-del-radicado.md) | Ubicación actual del radicado: tres columnas `flujo_*` que escribe el workflow-service en el mismo `UPDATE` que el estado, dos columnas generadas `ubicacion_*` (por pares), una función pura `derivar_ubicacion` (último paso por cuándo se cerró, no por su número), código solo si el nombre es único, backfill con evidencia de solo inserción y fuera de todo lo público | Implementado |
| [ADR-031](adr/ADR-031-constancia-autorizacion-ip-firmada-politica-por-entidad.md) | Constancia de autorización: IP firmada por el gateway en rutas públicas, política de tratamiento por entidad servida y releída por el servidor, y acuse de envío con autor y sin reutilización | Aceptado |
| [ADR-032](adr/ADR-032-dependencia-por-codigo.md) | La dependencia se correlaciona por código, no por nombre: `codigo` inmutable (trigger + 422), función única `resolver_codigo_dependencia` con historial de nombres, contrato con código canónico y nombre como alias deprecado, backfill que falla cerrado ante lo ambiguo, desactivar/eliminar por código | Aceptado |
| [ADR-033](adr/ADR-033-entrada-de-produccion-nginx-en-contenedor.md) | Entrada de producción: nginx en contenedor (`edge`) como único servicio con puertos, `TRUSTED_PROXIES` sin el gateway de la red para que la IP sellada no se pueda forjar, Keycloak publicado solo en `/realms/` y `/resources/`, y terceros fijados por tag y digest | Aceptado |
| [ADR-034](adr/ADR-034-xmldsig-conforme-firma-verificable-por-terceros.md) | La firma XAdES se declara como se aplica: `Transforms` explícitos en las referencias que `signxml` deja sin ellos, `SignedInfo` firmado después del parcheo (sello y firma personal), autoverificación fail-closed con `XAdESVerifier()` por defecto, sin atajo de canonicalización al verificar, y entrada del `ArchiveTimeStamp` según EN 319 132-1 §5.5.2; validado contra EU DSS | Aceptado |

## Cómo proponer un nuevo ADR

1. Crea el archivo `docs/es/adr/ADR-{NNN}-{slug}.md`
2. Usa el formato estándar: Contexto, Decisión, Consecuencias, Alternativas
3. Actualiza esta página con el nuevo ADR
4. Abre un MR para discusión antes de marcar como "Aceptado"

<!-- TODO: translate to English -->
