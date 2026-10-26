# Plantilla de servicio — Docuboto

Esqueleto mínimo de un microservicio FastAPI de OrpycaMCP, calcado de los servicios reales (auth, document, archive…). Sirve para crear servicios nuevos de forma **consistente** con las convenciones del proyecto.

> Referenciada por `CLAUDE.md` → "Adding a new service". Pensada sobre todo para los servicios nuevos del plan: `signature` (8008), `search` (8009), `mcp` (8010), `knowledge` (8011).

## Qué incluye

```
app/
  main.py              # app factory: lifespan (pool asyncpg), router de health, handler de errores
  core/
    config.py          # pydantic-settings; DATABASE_URL + propiedad database_url_asyncpg
    database.py        # pool asyncpg (create_pool/close_pool/get_db_conn)
  routers/health.py    # GET /health (verifica BD con SELECT 1)
  schemas/common.py    # HealthResponse
  services/  repositories/   # capas de negocio y acceso a datos (vacías)
migrations/
  runner.py            # aplica NNN_*.sql en orden; tracking en public._migrations
  001_example.sql      # plantilla de migración (SQL crudo, idempotente)
Dockerfile  requirements.txt  .env.example  pytest.ini  tests/test_health.py
```

## Cómo crear un servicio nuevo

1. **Copiar** la plantilla:
   ```bash
   cp -r docs/templates/service-template services/<nombre>-service
   ```
2. **Sustituir los marcadores** en todos los ficheros:
   - `__SERVICE_NAME__` → nombre en kebab/lower (p. ej. `knowledge`)
   - `__SERVICE_TITLE__` → título legible (p. ej. `Knowledge`)
   - `__PORT__` → puerto asignado (ver `documentos/specDrive/00-reconciliacion-tecnica.md` §1; p. ej. `8011`)
   ```bash
   cd services/<nombre>-service
   grep -rl '__SERVICE_NAME__\|__SERVICE_TITLE__\|__PORT__' . \
     | xargs sed -i 's/__SERVICE_NAME__/knowledge/g; s/__SERVICE_TITLE__/Knowledge/g; s/__PORT__/8011/g'
   ```
3. **Registrar en `docker-compose.yml`** un bloque `build: ./services/<nombre>-service` con sus puertos, `DATABASE_URL`/`REDIS_URL`/Keycloak y `depends_on`.
4. **Registrar las rutas** en `services/api-gateway/app/core/routes.py` (salvo `mcp-server`, que consume el gateway, no se enruta).
5. **Cliente Keycloak** en el realm, si el servicio valida JWT.
6. Renombrar/crear la primera migración `migrations/001_<algo>.sql` y borrar `001_example.sql`.

## Convenciones que respeta (no romper)

- **asyncpg + SQL crudo**, sin SQLAlchemy/Alembic — [ADR-003](../../es/adr/ADR-003-asyncpg-sin-orm.md).
- **`DATABASE_URL`** se mantiene en formato `postgresql+asyncpg://` y se normaliza con `settings.database_url_asyncpg` (asyncpg no entiende el sufijo `+asyncpg`).
- **Multi-tenant por schema** `tenant_{slug}` — [ADR-002](../../es/adr/ADR-002-multi-tenancy-schema-isolation.md); el `search_path` se fija por petición.
- **Eventos** por Redis Streams `orpycamcp.<servicio>.events` — ver `documentos/specDrive/00-contrato-eventos.md`.
- **Auditoría** vía la librería común contra `public.audit_log` — [ADR-008](../../es/adr/ADR-008-auditoria-inmutable.md).
- **Migraciones** numeradas `NNN_*.sql`, idempotentes, aplicadas por `migrations/runner.py`.
- Formato de error JSON `{error, detail, status}` y `redirect_slashes=False` (igual que los servicios existentes).

## Build y tests (siempre en Docker)

```bash
docker compose up -d --build <nombre>-service
docker compose exec <nombre>-service python migrations/runner.py
docker compose exec <nombre>-service pytest -q
```
