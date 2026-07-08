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
| **F3 — Ciclo de vida** | E04 TRD/CCD (jerarquía + retención en dos fases + disposición AGN), E02 expedientes (cierre/transferencia/foliado), E06 firma electrónica (hash+identidad + **XAdES-B del índice**, Inc.1) | ✅ |
| **F4 — Conformidad** | E15 índice electrónico (XML versionado append-only + huella por documento + verificación) + **firma XAdES-B/T/LT/LTA del índice al cierre con sello institucional + sello de tiempo + material de validación a largo plazo (CA local de dev, no acreditado)** (E06 Inc.1/4/5, ADR-016) + **RT-15 perfil v4 "conforme completo"** (orden documental estable + exclusión trazable + dependencia productora + fecha de declaración + política de acceso + pista de auditoría atestada, aplicabilidad prospectiva, ADR-022) | ✅ núcleo |
| **F5 — Preservación / físico** | E17 archivo físico (ubicaciones, unidades, signatura, préstamos, historial, **FUID**), E12 transferencias primarias/secundarias, E11 interoperabilidad (webhooks firmados), E10 preservación (plan + eventos PREMIS + **WORM/Object-Lock del índice firmado**, ADR-023) | ✅ núcleo |
| **F6 — Inteligencia** | E18 mcp-server (catálogo de tools sobre el gateway), E21 knowledge-service (pgvector + recuperación semántica con pre-filtrado ACL) | ✅ núcleo |

### Pendientes del specDrive (dependen de integraciones/infra externas)

- **E18**: binding del SDK MCP oficial (stdio / HTTP-streamable, `initialize`/capabilities).
- **E21**: ingesta event-driven, RAG anclado con citas, proveedor de embeddings/LLM real (hoy stub local determinista).
- **E10**: **Object-Lock/WORM real en MinIO** ✅ **Increment A** (ADR-023: bucket de preservación por tenant, índice firmado bajo `Retention(COMPLIANCE)` derivada de la TRD, gate de disposición, legal hold) + **AIP real + PREMIS v3** ✅ **Increment B** (ADR-023 addendum: BagIt RFC 8493 con bytes reales + fixity recalculada, subido al bucket WORM y cubierto por el gate de disposición) + **PREMIS v3 conformidad plena** ✅ **Increment C1a** (valida contra el `premis.xsd` OFICIAL de la LoC vendorizado, `xsi:type="premis:file"`) + **REPLICA best-effort del AIP** ✅ **Increment C1b** (una copia WORM inmutable a target configurable —default segundo bucket del mismo MinIO, honestamente "réplica local, no cross-site"; cross-site vía `MINIO_SECONDARY_*`—, disparada por `num_copias≥2`, no-fatal) + **validación PDF/A — contrato + stub honesto** ✅ **Increment C2a** (interfaz `PdfaValidator` pluggable, stub por defecto que reporta `no_evaluado` sin fingir, evento `VALIDACION_PDFA` + PREMIS `validation`, advisory, migración `008` del tercer estado). Pendiente: **C2b** (diferido) — **veraPDF real** (subprocess endurecido + JRE/veraPDF en la imagen de deploy + CI que lo ejerza; requiere el pase completo de seguridad por el subprocess sobre PDF no confiable).
- **E15/E06**: firma **XAdES-B** del índice al cierre ✅ (Inc.1, ADR-016 addendum); **2FA por OTP-correo en la firma personal** ✅ (Inc.2, RF-FIR-13); **metadatos RT-15 del índice — perfil v2** ✅ (Inc.3, ADR-022); **XAdES-T — sello de tiempo RFC 3161 con TSA local** ✅ (Inc.4, ADR-016 addendum: fecha cierta **verificable, no acreditada**); **XAdES-LT/LTA — material de validación a largo plazo con CA local de dev** ✅ (Inc.5, ADR-016 addendum: cadena + revocación CRL embebidas + `ArchiveTimeStamp`, `revocation_provenance="dev"`, **no acreditado**, degradación observable a T sin material CA). Pendiente: **acreditación ONAC** (CA + TSA acreditadas vía integración externa — todo `no_acreditado` hoy), **OCSP** (hoy solo CRL), **perfil ArchiveTimeStamp plenamente EN 319 132** (hoy simplificado). **2FA de mayor aseguramiento — TOTP RFC 6238** ✅ (Inc.6, ADR-016 addendum: secreto en auth-service cifrado AESGCM, verify por bearer, anti-replay atómico + lockout; WebAuthn sigue pendiente). **RT-15 "conforme completo" (perfil v4, ADR-022): ✅** — cerrados dependencia productora, fecha de declaración, política de acceso por roles y pista de auditoría atestada (aplicabilidad prospectiva; instancias legacy pueden omitir productora). Pendiente menor: **backfill** de la dependencia productora de radicados legacy (requeriría un cliente a document-service) y **validación XSD estricta** con lxml por versión (los `.xsd` v2/v3/v4 están publicados pero no se ejecutan en runtime).
- **E20**: integración con operadores postales (factible como consumidor de webhooks, ADR-018).
- **E17** (menores): rótulo PDF/QR, capacidad/ocupación, custodia externa.
- **E08** (reclasificación): el endpoint `PATCH /documents/{id}/security-level` ya reclasifica el `nivel_seguridad` con permiso `PERM_RECLASIFICAR` + no-read-up/no-write-up, auditoría inmutable (motivo + fundamento jurídico) y re-sync del *snapshot* de firma vía evento — cierra el residual de *staleness* de RF-SEG-08. **La UI ya está hecha**: chip de nivel + modal de reclasificación gateado por `PERM_RECLASIFICAR` en el drawer de la bandeja (con `nivel_seguridad` expuesto en el read path de radicados). Diferido: el **índice de información clasificada y reservada** publicado (Ley 1712 art.20 + Decreto 1081/2015), *outbox* transaccional del evento, y doble control de desclasificación.
- **E08** (no-read-up en expedientes): cerrado el bypass de clearance del **listado** `GET /expedientes` y del `PATCH /{id}` (filtraban con el default "sin restricción"); ambos aplican ahora no-read-up con el `COUNT`/`X-Total-Count` filtrado. Seguimiento de seguridad rastreado (pre-existente, hecho explícito): **(M1)** `close/transfer` mutan un expediente CLASIFICADA sin clearance del actor —único que materializa disposición TRD, **prioritario**—; **(M2)** oráculo de membresía/conteo en `link_batch`/`rebuild`; **(M3)** write-up de `nivel_seguridad` en `POST /expedientes` sin contrastar el clearance del creador. Los tres usan la constante explícita `CLEARANCE_SIN_RESTRICCION` (`core/clearance.py`) para dejar el riesgo visible en el código.
- **E09** (RF-BUS-10 — traza de consulta): **parcial**. El listado/búsqueda de **expedientes** en archive-service ya emite una traza **agregada** por consulta (`object_type="expediente_busqueda"`, usuario/criterios/total, sin el texto crudo de `q`), separada de la pista por-expediente del índice firmado. **Pendiente**: extender la traza agregada al resto de rutas de búsqueda (document-service, etc.) — RF-BUS-10 **no está globalmente cumplido**; no marcar como conforme la auditoría de consultas hasta cubrir todos los servicios.

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
- [x] ~~Firma de documentos~~ — firma electrónica nativa + **cadena de firma / bandeja del firmante** hechas (E06: turno ordenado, `GET /pending`, sign/reject/batch); **XAdES-B del índice electrónico + sello institucional** hecho (Inc.1, ADR-016 addendum); **2FA por OTP-correo en la firma personal** hecho (Inc.2, RF-FIR-13); **XAdES-T (TSA local)** hecho (Inc.4) y **XAdES-LT/LTA (CA local de dev + CRL + ArchiveTimeStamp, no acreditado)** hecho (Inc.5, ADR-016 addendum); **2FA TOTP RFC 6238 en la firma personal** hecho (Inc.6, ADR-016 addendum: secreto en auth-service, AESGCM, verify por bearer); **acreditación ONAC (CA/TSA), OCSP, perfil LTA plenamente EN 319 132 y 2FA WebAuthn** pendientes (signature-service)
- [ ] Integración SSO corporativo (Entra ID, Okta, LDAP)
- [x] Frontend (E22, `frontend/`, ADR-011/020) — **prácticamente completo** (~14k LOC): login OAuth2 PKCE server-side (cookies httpOnly, refresh transparente, guard de rutas), sistema de diseño Orpyca `--op-*` + Bulma, **14 vistas de dominio** con proxies BFF (bandeja, radicar, **expedientes —listado T-12 ✅—**, firmas, envíos, búsqueda, reportes, archivo físico, transferencias, admin…), nav espejo del RBAC, componentes UI reutilizables. **Cobertura de tests** ✅ (Vitest 200 + Playwright E2E smoke 6; infra @testing-library/svelte + jsdom + Playwright). Pendiente: el **asistente conversacional** (ADR-020, full-stack: requiere el contrato `/api/v1/assistant/*` en gateway + mcp-server); campos de plantilla en la creación de expediente (Form Builder); E2E autenticado contra docker-compose (escrito, hoy skip)
- [x] ~~API de webhooks~~ — webhooks salientes firmados hechos (E11)
- [ ] Exportación de expedientes a PDF/ZIP
- [x] ~~Reportes y estadísticas~~ — reportes de radicados hechos (E09); panel/indicadores avanzados pendientes
