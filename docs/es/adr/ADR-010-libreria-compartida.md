# ADR-010: Librería compartida `orpycamcp_common` (auditoría y eventos)

**Estado**: Aceptado (implementado en F1 — `shared/orpycamcp_common` + auth-service)
**Fecha**: 2026-06-16
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

Al planificar la Fase 1 surgieron dos capacidades **transversales** que, por contrato técnico, deben comportarse **igual en todos los servicios**:

1. **Auditoría inmutable** ([ADR-008](ADR-008-auditoria-inmutable.md)): `public.audit_log` append-only con **cadena de hash por `tenant_slug`**. El cálculo del hash y el orden de inserción son lógica delicada: si dos servicios la implementan distinto, la cadena se rompe o se vuelve inverificable.
2. **Bus de eventos Redis Streams** (00-contrato-eventos.md): un **sobre común** (`event_id`, `event_type`, `version`, `tenant_slug`, `occurred_at`, `actor`, `correlation_id`, `dedup_key`, `payload`) con idempotencia en producción y consumo. El contrato §4 lista `audit_log + librería de auditoría` y los eventos `radicado_*` / `documento_vinculado` como **consumidos por múltiples épicas** (E05, E09, E15, E16…).

**Estado actual del código (verificado):**

- **No existe ningún mecanismo de código compartido.** Cada servicio **copia** su `app/core/database.py`, `config.py` y, donde aplica, `redis_client.py` (ficheros idénticos byte a byte).
- **La auditoría solo existe en auth-service**, contra una tabla propia `public.auth_audit_logs` con un formato distinto al canónico de ADR-008. Los demás servicios no auditan.
- **El bus de eventos es ad-hoc**: solo `workflow-service` publica (stream `orpycamcp.workflow.events`) con un diccionario de 7 campos **sin `event_id` ni idempotencia**; solo `notification-service` consume.

Si en F1 cada servicio implementa auditoría y publicación de eventos por su cuenta, tendremos cadenas de hash divergentes, sobres de evento incompatibles y consumidores frágiles. Hace falta **un único lugar** para esa lógica, sin violar la regla **Docker-only** (no se instala nada en el host).

## Decisión

**Se crea un paquete Python interno `orpycamcp_common`, versionado dentro del monorepo en `shared/`, instalado en la imagen de cada servicio mediante `COPY shared /shared` + `pip install /shared` en el `Dockerfile`.**

Es la fuente única de verdad para la lógica transversal. No se publica a ningún registry externo, no es un submódulo: vive y se versiona con el repositorio.

### Alcance de la primera versión (deliberadamente mínimo)

El paquete arranca cubriendo **solo lo nuevo de F1**, para no refactorizar de golpe código que ya funciona:

| Módulo | Responsabilidad |
|---|---|
| `orpycamcp_common.audit` | `append(conn, *, tenant, service, actor, action, object_type, object_ref, payload)` — calcula la cadena de hash por tenant e inserta en `public.audit_log` (ADR-008). Único punto de escritura de auditoría. |
| `orpycamcp_common.events` | Sobre canónico de evento (dataclass/`TypedDict`), `publish(redis, stream, event)` con `event_id`/`occurred_at`, y `consume(...)` idempotente con `dedup_key` y *consumer groups*. Implementa 00-contrato-eventos.md. |

**Fuera de alcance por ahora** (se evalúa en un ADR posterior cuando haya holgura): consolidar `database.py`, `config.py` y `redis_client.py`, que hoy están duplicados. Se dejan como están para **no tocar los 7 servicios a la vez**; la duplicación de esos tres ficheros es estable y de bajo riesgo. La plantilla de servicio (`docs/templates/service-template/`) sigue siendo el patrón para servicios nuevos.

### Empaquetado e instalación (Docker-only)

```
orfeoMcp/
├── shared/
│   ├── pyproject.toml              # define el paquete orpycamcp_common
│   └── orpycamcp_common/
│       ├── __init__.py             # exporta versión
│       ├── audit.py                # append() + cadena de hash (ADR-008)
│       └── events.py               # envelope + publish/consume idempotente
└── services/<svc>/Dockerfile
```

```dockerfile
# En el Dockerfile de cada servicio que use auditoría y/o eventos:
COPY shared /shared
RUN pip install /shared
# ... resto del build del servicio
```

- **Precondición de build context (estado verificado):** hoy cada servicio se construye con `build: ./services/<svc>`, por lo que un `Dockerfile` **no puede** `COPY shared` (queda fuera del contexto). Para los servicios que usen `orpycamcp_common` hay que cambiar a contexto en la **raíz del repo**:
  ```yaml
  auth-service:
    build:
      context: .                              # raíz, para ver shared/ y services/
      dockerfile: services/auth-service/Dockerfile
  ```
  y añadir un **`.dockerignore` en la raíz** (no existe hoy) que excluya `services/*/`.venv`, `node_modules`, `frontend/`, `documentos/`, `data/`, `.git`, etc., para que el contexto ampliado no infle el build. Los `COPY` internos del Dockerfile pasan a rutas relativas a la raíz (`COPY services/auth-service/ .` + `COPY shared /shared`).
- `orpycamcp_common` usa **asyncpg** y el cliente de Redis ya presentes ([ADR-003](ADR-003-asyncpg-sin-orm.md)); no introduce ORM.
- Versionado simple (`__version__`) dentro del paquete; al ser monorepo, el commit fija la versión efectiva en todas las imágenes.

### Migración de la auditoría existente

auth-service deja de usar su tabla propia y pasa al canon:

- auth-service escribe la auditoría de autenticación (`login_success`, `login_failure`, `token_refresh`, `logout`) en `public.audit_log` **vía `orpycamcp_common.audit`**.
- Una migración de auth-service (`002_*`) marca **`public.auth_audit_logs` como obsoleta** (deja de escribirse; se conserva en solo-lectura para histórico y se retira en una limpieza posterior). No se pierde el histórico.
- Resultado: **una sola fuente de auditoría** consultable y verificable para todo el sistema.

## Consecuencias

**Positivas:**
- La cadena de hash de auditoría y el sobre de eventos se implementan **una vez**; imposible que dos servicios diverjan.
- Sin infraestructura nueva (registry, submódulos): la librería se versiona y despliega con el propio repo.
- Alcance mínimo → **sin regresiones** en el código que ya funciona; F1 puede avanzar.
- auth-service queda alineado con ADR-008 desde el inicio de F1.

**Negativas:**
- El build context de Docker debe incluir `shared/` (ajuste en `docker-compose.yml` y en los `Dockerfile`).
- Un cambio en `orpycamcp_common` obliga a **reconstruir las imágenes** de los servicios que lo usan (aceptable: ya se reconstruye en cada despliegue).
- Persiste la duplicación de `database.py`/`config.py`/`redis_client.py` hasta un ADR posterior (decisión consciente de acotar el riesgo ahora).

## Alternativas consideradas

- **Registry pip privado**: versionado independiente, pero exige montar y mantener un índice pip y publicar en cada cambio. Sobre-ingeniería para un monorepo.
- **Git submodule**: versionado por commit, pero añade fricción (checkout/sincronización de submódulos) sin ventaja real cuando todo el código ya vive en el mismo repositorio.
- **Seguir duplicando (status quo)**: cero abstracción nueva, pero garantiza que la cadena de hash de auditoría y el envelope de eventos diverjan entre servicios. Descartada: rompe ADR-008 y el contrato de eventos.
- **Refactorizar también database/config/redis ya**: más limpio a largo plazo, pero obliga a tocar los 7 servicios simultáneamente al inicio de F1. Pospuesto a un ADR futuro para no acumular riesgo.

## Relacionados

- [ADR-008](ADR-008-auditoria-inmutable.md) — define `audit_log` y la cadena de hash que implementa `orpycamcp_common.audit`.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — la librería usa asyncpg + SQL crudo, sin ORM.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — `audit_log` vive en `public` con `tenant_slug`.
- 00-contrato-eventos.md — sobre canónico que implementa `orpycamcp_common.events`.
- Épicas consumidoras: **E08** (auditoría), **E05/E16** (eventos), y por extensión todas las de F1.
