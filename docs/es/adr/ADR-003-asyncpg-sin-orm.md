# ADR-003: asyncpg directo en lugar de SQLAlchemy ORM

**Estado**: Aceptado  
**Fecha**: 2026-06-03  
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

Durante la implementación del auth-service se utilizó inicialmente SQLAlchemy 2.x con Alembic para migraciones. Al revisar el código, se observó que SQLAlchemy se usaba únicamente para una tabla de audit logs, mientras que el resto del servicio (validación JWT, integración Keycloak) no interactuaba con la base de datos.

El stack ORM completo (SQLAlchemy + Alembic + aiosqlite para tests) agregaba:
- 3 dependencias adicionales (~4 MB de paquetes)
- Complejidad de configuración (engine, sessionmaker, DeclarativeBase)
- Un bug en tests por incompatibilidad de `schema="public"` con SQLite
- SQL implícito generado por el ORM en lugar de SQL explícito y auditable

## Decisión

**Usar `asyncpg` directamente para toda interacción con PostgreSQL en todos los servicios de OrpycaMCP.**

No usar SQLAlchemy, Alembic, ni ningún ORM o query builder.

## Implementación

### Pool de conexiones

```python
# app/core/database.py — patrón estándar en todos los servicios
import asyncpg
from fastapi import Request

_pool: asyncpg.Pool | None = None

async def create_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)
    return _pool

async def get_db_conn(request: Request):
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        yield conn
```

### Migraciones

Archivos `.sql` numerados en `migrations/` por servicio. Un script `migrations/runner.py` los aplica en orden y registra los aplicados en `public._migrations`.

```
migrations/
  001_create_tabla.sql
  002_add_index.sql
  runner.py             — idempotente, tabla de tracking incluida
```

### Tests

La conexión se mockea con `AsyncMock` — sin base de datos real ni SQLite:

```python
def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    return conn
```

## Consecuencias

**Positivas:**
- SQL explícito y auditable — cualquier query es visible en el repositorio
- Sin mapeo ORM → sin capa de abstracción que oculte problemas de performance
- Menos dependencias por servicio
- Los tests no necesitan una base de datos ni un ORM alternativo
- asyncpg es el driver PostgreSQL más rápido disponible para Python

**Negativas:**
- Sin generación automática de migraciones — las migraciones se escriben a mano en SQL
- Sin validación de tipos en queries — los errores de tipo se detectan en runtime
- Más código repetitivo en los repositorios (no hay helpers del ORM)

## Alternativas consideradas

- **SQLAlchemy Core** (sin ORM): descartado por seguir requiriendo Alembic y la capa de abstracción
- **Tortoise ORM**: descartado por ser menos maduro que asyncpg directo
- **Mantener SQLAlchemy**: descartado tras ver que el overhead no justificaba el valor en servicios simples
