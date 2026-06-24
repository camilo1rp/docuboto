# ADR-001: Microservices Architecture with FastAPI

**Date**: 2026-05-28
**Status**: Accepted
**Deciders**: Giampiero (orpyca.com)

## Context

OrpycaMCP is a reimplementation of the legacy PHP Orfeo DMS. We need an architecture that:
1. Is maintainable by an open-source community
2. Supports multi-tenant deployments (multiple institutions per installation)
3. Is horizontally scalable for larger institutions
4. Provides clear separation of concerns for independent contribution

## Decision

We adopt a **microservices architecture using FastAPI (Python)**, with:
- One FastAPI service per bounded context
- PostgreSQL schema isolation per tenant
- Redis Streams for async inter-service events
- Keycloak for centralized IAM
- MinIO for object storage (replacing Orfeo's filesystem)

## Services and Bounded Contexts

| Service | Bounded Context | Key Responsibility |
|---|---|---|
| api-gateway | Infrastructure | Routing, rate limiting, token relay |
| auth-service | Identity | JWT validation, Keycloak bridge, RBAC |
| tenant-service | Multi-tenancy | Institution CRUD, schema provisioning |
| document-service | Documents | Radicación (E/S/I), tracking numbers |
| archive-service | Archive | Expedientes, TRD, series |
| storage-service | Files | Annexe upload/download via MinIO |
| workflow-service | Workflow | Distribution flows, assignments |
| notification-service | Notifications | Email, in-app alerts |

## Consequences

**Positive:**
- Each service can be developed, tested, and deployed independently
- Contributors can focus on a single bounded context
- Services can be scaled independently based on load

**Negative:**
- Higher operational complexity than a monolith
- Network latency for inter-service calls
- More boilerplate code per service

## Alternatives Considered

**Modular monolith**: Simpler to start, but harder to scale and less suitable for community contributions across domains.

**Django + DRF**: More batteries-included, but FastAPI's async support and automatic OpenAPI generation better suit this project's needs.
