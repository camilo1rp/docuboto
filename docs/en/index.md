# Docuboto — Free Document Management System

**OrpycaMCP** is an open-source Document Management System (SGD — *Sistema de Gestión Documental*) for public and private institutions in Latin America, published under the **AGPL v3** license.

Its purpose is to manage the complete lifecycle of institutional documents: from the radicación (official registration) of correspondence, through its distribution and internal tracking, to its organization into expedientes (case files) and final disposition according to the TRD (Tabla de Retención Documental — document retention schedule).

---

## What is an SGD and why does it matter?

A Document Management System is not a file manager. It is the tool that guarantees that **every document entering or leaving an institution is registered, traced and managed in accordance with the law**.

In Colombia, document management for public entities is regulated by:

- **Ley 594 of 2000** (Law 594/2000) — General Archives Law: it requires public entities to organize their documents through TRD and to guarantee their preservation.
- **Acuerdo AGN 060 of 2001** (AGN Agreement 060/2001) — establishes guidelines for the administration of official communications.
- **Decreto 1080 of 2015** (Decree 1080/2015) — Single Regulatory Decree of the Culture sector, which consolidates archival regulations.
- **MIPG** — Integrated Planning and Management Model (*Modelo Integrado de Planeación y Gestión*), which requires document traceability as part of public management.

OrpycaMCP implements these standards in free software, allowing any institution to fulfill its archival obligations without depending on proprietary solutions.

---

## Document lifecycle in OrpycaMCP

```
                    ┌─────────────┐
   Citizen/         │             │
   external entity  │  RADICACIÓN │  Incoming (E) — Outgoing (S) — Internal (I)
   ──────────────► │             │  Unique and immutable number assigned
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ DISTRIBUTION│  Flow between dependencias
                    │  (Flujos)   │  Traceability of every step
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  PROCESSING │  The owner manages, responds
                    │             │  or transfers the document
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ EXPEDIENTE  │  Grouping of radicados by subject
                    │             │  States: Open → Closed → Transferred
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  ARCHIVING  │  TRD determines retention and final disposition
                    │   (TRD)     │  Preserve / Destroy / Digitize
                    └─────────────┘
```

---

## System modules

| Module | Description |
|---|---|
| **Radicación** (official registration) | Official registration of documents with a unique number. Types: Incoming, Outgoing, Internal |
| **Anexos** (attachments) | Attached files stored with guaranteed integrity (SHA-256) |
| **Distribution and flujos** (flows) | Routing of documents between dependencias with full traceability |
| **Expedientes** (case files) | Grouping of radicados by subject or case, with their own lifecycle |
| **TRD** | Tabla de Retención Documental (document retention schedule) — classifies and determines final disposition |
| **Multi-entity** | A single installation serves multiple institutions with fully isolated data |
| **Authentication** | Keycloak with LDAP support for integration with institutional directories |

---

## Who is OrpycaMCP for?

- **Colombian public entities** that need to comply with Law 594/2000 using free software
- **Latin American institutions** with similar document management regulations
- **Municipalities and governorates** that cannot afford proprietary solutions
- **Developers and communities** that want to contribute to a modern, maintainable SGD
- **Integrators** that need a well-documented REST API to connect other systems

---

## Differences from the original Orfeo

OrpycaMCP reimplements the original [Orfeo GPL](https://github.com/orfeosgd/orfeo) (monolithic PHP + PostgreSQL) with a modern architecture:

| Aspect | Original Orfeo | OrpycaMCP |
|---|---|---|
| Stack | PHP 7 + Apache | FastAPI (Python 3.11) |
| Architecture | Monolithic | Microservices |
| File storage | Server file system | RustFS (S3-compatible, ADR-027) |
| Authentication | PHP sessions + optional LDAP | Keycloak (SSO, LDAP, OAuth2) |
| Multi-entity | One installation per entity | Native multi-tenant |
| API | No standard REST API | Full OpenAPI / REST |
| Documentation | Limited | Automatic, bilingual, published |

The business logic (radicados, expedientes, TRD, flujos) is faithfully preserved from the original system.

---

## Quick Start

```bash
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp
docker compose up -d
```

Explore the API at `http://localhost:8080/docs`
Manage Keycloak at `http://localhost:8180/admin` (admin / orpycamcp_dev)

---

## Project status

| Phase | Content | Status |
|---|---|---|
| Phase 1 — Framework | Architecture, CI/CD, base documentation | ✅ In progress |
| Phase 2 — Core SGD | Radicación E/S/I, Anexos, TRD | 🔲 Planned |
| Phase 3 — Processing | Workflows, Expedientes, Notifications | 🔲 Planned |
| Phase 4 — Community | Public docs, GitHub, orpyca.com | 🔲 Planned |

---

## License

**AGPL v3** — the same as the original Orfeo GPL. Every modification must be published under the same license. This guarantees that the software remains free forever.
