# Roadmap

> Actualizado al 2026-06-22.

OrpycaMCP ha seguido **dos planificaciones complementarias**:

1. **Roadmap fundacional** (Fases 1–6, abajo «Andamiaje»): levantó los 8 microservicios con su CRUD básico, infraestructura y CI/CD. **Completo.**
2. **Plan specDrive** (Fases F1–F6, 21 épicas, 138 RF): eleva el andamiaje a **conformidad SGDEA** (Ley 594/2000, Acuerdos AGN 001/2024, 042/2002, 003/2015; ISO 14721/16363). Es el plan vigente y su estado se detalla primero.

---

## Plan specDrive — Conformidad SGDEA (vigente)

**Estado global:** núcleo de las **21 épicas implementado, probado en Docker (~300 tests verdes en 11 microservicios) y con migraciones validadas contra PostgreSQL/pgvector real.** 20 ADRs aceptados.

| Fase | Épicas | Estado |
|---|---|---|
| **F1 — Fundamentos** | librería común (ADR-010), migraciones por tenant (ADR-012), E14 administración, E08 seguridad (RBAC + auditoría + URD + clasificación), E01 radicación, E03 metadatos, E07 anexos, E16 notificaciones, E05 flujos | ✅ |
| **F2 — Consulta** | E09 búsqueda avanzada + reportes + búsqueda de expedientes, E13 consulta pública por código de verificación, E19 ingesta de correo IMAP | ✅ |
| **F3 — Ciclo de vida** | E04 TRD/CCD (jerarquía + retención en dos fases + disposición AGN), E02 expedientes (cierre/transferencia/foliado), E06 firma electrónica (hash+identidad) | ✅ |
| **F4 — Conformidad** | E15 índice electrónico (XML versionado append-only + huella por documento + verificación) | ✅ núcleo |
| **F5 — Preservación / físico** | E17 archivo físico (ubicaciones, unidades, signatura, préstamos, historial, **FUID**), E12 transferencias primarias/secundarias, E11 interoperabilidad (webhooks firmados), E10 preservación (plan + eventos PREMIS) | ✅ núcleo |
| **F6 — Inteligencia** | E18 mcp-server (catálogo de tools sobre el gateway), E21 knowledge-service (pgvector + recuperación semántica con pre-filtrado ACL) | ✅ núcleo |

### Pendientes del specDrive (dependen de integraciones/infra externas)

- **E18**: binding del SDK MCP oficial (stdio / HTTP-streamable, `initialize`/capabilities).
- **E21**: ingesta event-driven, RAG anclado con citas, proveedor de embeddings/LLM real (hoy stub local determinista).
- **E10**: empaquetado AIP/BagIt, Object Lock/WORM real en MinIO, validación PDF/A con veraPDF.
- **E15**: firma XAdES del índice al cierre (requiere signature-service criptográfico, ADR-016).
- **E20**: integración con operadores postales (factible como consumidor de webhooks, ADR-018).
- **E17** (menores): rótulo PDF/QR, capacidad/ocupación, custodia externa.
- **E08** (reclasificación): el endpoint `PATCH /documents/{id}/security-level` ya reclasifica el `nivel_seguridad` con permiso `PERM_RECLASIFICAR` + no-read-up/no-write-up, auditoría inmutable (motivo + fundamento jurídico) y re-sync del *snapshot* de firma vía evento — cierra el residual de *staleness* de RF-SEG-08. Diferido: el **índice de información clasificada y reservada** publicado (Ley 1712 art.20 + Decreto 1081/2015), *outbox* transaccional del evento, y doble control de desclasificación.

---

## Andamiaje fundacional (Fases 1–6) ✅ Completo

> Estas fases construyeron la base sobre la que el specDrive eleva la conformidad.

### Fase 1 — Framework y Arquitectura
- [x] Estructura, CLAUDE.md, agentes, memoria persistente, docker-compose base (PostgreSQL, MinIO, Keycloak, Redis, MailHog), realm Keycloak, init-db.sql, CI/CD, MkDocs
- [x] ADR-001 microservicios · ADR-002 multi-tenancy
- [x] **auth-service**, **tenant-service**, **api-gateway**

### Fase 2 — Dominio Core
- [x] **document-service** (radicación E/S/I, numeración atómica, anexos)
- [x] **storage-service** (MinIO, SHA-256, pre-signed URLs, bucket por tenant)
- [x] **archive-service** (TRD, expedientes `open→closed→transferred`)

### Fase 3 — Workflows y Notificaciones
- [x] **workflow-service** (asignación/transferencia, historial, eventos Redis)
- [x] **archive-service** (vínculo radicado↔expediente)
- [x] **notification-service** (SMTP, consumer de eventos, historial)

### Fase 4 — Calidad e Integración
- [x] init-tenant, E2E, ADR-003 asyncpg, lint/type-check en CI, health check orquestado

### Fase 5 — Comunidad y Publicación
- [x] Guías de despliegue/contribución, README EN, docs bilingüe, AGPL v3, OpenAPI aggregation, GitHub Actions, GHCR, API reference EN

### Fase 6 — Características Avanzadas (andamiaje)
- [x] TRD seed FondeCund, Batch Documents/Expedientes, Full-Text Search, Workflow Rules Engine

---

## Más allá del specDrive 📋 Planeado

- [ ] App móvil nativa (iOS/Android)
- [x] ~~Firma de documentos~~ — firma electrónica nativa + **cadena de firma / bandeja del firmante** hechas (E06: turno ordenado, `GET /pending`, sign/reject/batch); **firma digital PKI/XAdES** pendiente (signature-service)
- [ ] Integración SSO corporativo (Entra ID, Okta, LDAP)
- [x] Frontend — scaffold SvelteKit en `frontend/` (ADR-011); UI completa pendiente
- [x] ~~API de webhooks~~ — webhooks salientes firmados hechos (E11)
- [ ] Exportación de expedientes a PDF/ZIP
- [x] ~~Reportes y estadísticas~~ — reportes de radicados hechos (E09); panel/indicadores avanzados pendientes
