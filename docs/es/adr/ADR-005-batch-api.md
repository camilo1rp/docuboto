# ADR-005: Batch API para operaciones bulk

**Status**: Accepted (2026-06-04)

**Context**: Las instituciones necesitan registrar cientos o miles de documentos (radicados) y expedientes sin hacer llamadas individuales. Un endpoint por documento sería ineficiente para operaciones de migración o ingesta masiva.

**Decision**: Implementar Batch API con job tracking asincrónico:

1. **Endpoints**:
   - `POST /api/v1/batch/documents` — crear 1-1000 radicados
   - `POST /api/v1/batch/expedientes` — crear 1-100 expedientes con radicados vinculados
   - `GET /api/v1/batch/{job_id}/status` — monitorear progreso

2. **Diseño**:
   - **Respuesta inmediata (202 Accepted)** — job queued, no bloquea cliente
   - **Procesamiento asincrónico** — asyncio.create_task() en desarrollo; Celery/Dramatiq en producción
   - **Job tracking en PostgreSQL** — `batch_jobs` y `batch_job_items` tables
   - **Transaccionalidad por ítem** — cada documento es una transacción independent; fallo de uno no afecta otros
   - **Resultado granular** — cliente ve qué items fallaron y por qué

3. **Schemas**:
   - `BatchDocumentsRequest` — array de `BatchDocumentCreate` (1-1000)
   - `BatchExpedientesRequest` — array de `BatchExpedienteCreate` (1-100)
   - `BatchJobStatusResponse` — incluye array de `BatchItemResult` con status/error/result_id

4. **Database**:
   - `batch_jobs` — id, job_type, status (pending/processing/completed/failed), progress
   - `batch_job_items` — id, job_id, item_index, payload, result_id, status, error_message

**Consequences**:

✅ Ventajas:
- Operaciones masivas eficientes (1 API call vs 1000)
- Transaccionalidad granular (un fallo no bloquea otros)
- Progress tracking (cliente puede monitorear con GET /status)
- Sin bloques de respuesta (202 Accepted)
- Fácil migraciones desde Orfeo PHP legacy

❌ Desventajas:
- Complejidad de implementación (async job processing)
- Necesita task queue en producción (no asyncio.create_task)
- Requiere polling del cliente para status (no webhooks yet)
- Storage de jobs en DB (puede crecer)

**Alternative considered**:
- Synchronous batch API — bloqueadora, no escalable para 1000+ items
- Webhooks — necesita callback URL, más complejo de validar
- Message queue (Kafka) — overkill para MVP, Celery es más simple

**Related ADRs**:
- ADR-003: asyncpg directo (no ORM) — batch API usa SQL directo
- ADR-004: (TBD) — integración con Redis para job events (futuro)

**Follow-up**:
- [ ] Implementar task queue (Celery con Redis) para producción
- [ ] Agregar webhooks para notifications cuando job completa
- [ ] Batch expedientes API
- [ ] Batch storage/upload para archivos en operaciones bulk
