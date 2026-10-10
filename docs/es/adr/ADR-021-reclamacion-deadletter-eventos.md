# ADR-021: Reclamación de PEL y dead-letter por grupo consumidor en Redis Streams

**Estado**: Aceptado — **roadmap de eventos completo** en los tres servicios consumidores (signature-service, workflow-service, notification-service). Fases 1, 2, 3, 4-MVP-1/MVP-2/MVP-3 y 5 (replay/discard/gestión API) implementadas: R-4 resuelto; retención operacional de dedup activa; trim MINID del DLQ Redis activo; **purga de dead-letters terminales activa en los tres servicios**; **API de inspección/replay/discard con `PERM_DLQ_ADMIN` en los tres servicios**; toda consulta sobre la tabla compartida `evento_dead_letter` acotada por `origin_group`. El replay de **notification** es *best-effort* (correo no transaccional): *claim-then-send* con limpieza del dedup y estado transicionado solo tras éxito; la auditoría es dependencia dura en la disposición (503 / no-XACK si el módulo de auditoría no está). El trade-off del correo (posible doble-envío/pérdida) está acotado a **alertas internas de dependencia** —no a notificación en debida forma (CPACA Ley 1437/2011)— porque el registro legal vive en el radicado y en `audit_log` (2026-07-01)
**Fecha**: 2026-06-29
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

El patrón A-1 establecido en ADR-010 garantiza que un consumidor solo hace XACK tras
procesar con éxito un mensaje: si el procesamiento falla, el mensaje permanece en la
**PEL (Pending Entries List)** del grupo para ser reintentado. Esto es correcto para
fallos transitorios (BD caída, red lenta), pero sin un mecanismo de corte crea dos
problemas operativos:

1. **Acumulación silenciosa de poison-pills.** Un mensaje que falla sistemáticamente
   (payload inválido, bug en el handler, tenant inexistente) se queda en la PEL para
   siempre, incrementando `times_delivered` en cada arranque del consumidor pero sin
   que nunca se resuelva ni quede registrado.

2. **Sin recuperación ante consumidores caídos.** Si un consumidor muere a mitad del
   procesamiento, el mensaje queda asignado a ese consumidor en la PEL. Un consumidor
   sucesor que lea `">"` (sólo mensajes nuevos) nunca lo verá; tampoco hay un proceso
   que lo transfiera a un consumidor activo.

Ley 594/2000 **art. 4** (principios de trazabilidad y responsabilidad en la gestión
documental) y **art. 19** (autenticidad, integridad e inalterabilidad del soporte
electrónico), junto con **Acuerdo AGN 060/2001 art. 3** (control de comunicaciones
oficiales y registro de todas las actuaciones), exigen que cualquier acto sobre un
documento oficial deje trazabilidad. Un evento no entregado que desaparece en silencio
es una violación de ese principio, no sólo un problema operativo.

## Decisión

**Cada grupo consumidor recorre periódicamente su PEL con `XPENDING(IDLE)` + `XCLAIM`
y, tras `N` intentos fallidos, publica el mensaje en un _dead-letter stream_ específico
del servicio antes de ackearlo en el stream original.  La lógica reutilizable vive en
`orpycamcp_common.events.reclaim_stale`; la auditoría del dead-letter la hace el propio
servicio vía un callback `on_dead_letter`, de modo que el helper no tenga dependencia de
BD.**

### Helper `reclaim_stale` (ADR-010 — `shared/orpycamcp_common/events.py`)

```
reclaim_stale(redis, stream, group, consumer, *, handler,
              min_idle_ms, max_deliveries, deadletter_stream,
              batch, on_dead_letter, on_reclaim) → ReclaimStats
```

Algoritmo:
1. `XPENDING IDLE min_idle_ms` → lista de entradas con `message_id` y `times_delivered`.
2. Para cada entrada:
   - Si `times_delivered >= max_deliveries`: **dead-letter directo** (huérfano sin intento
     fresco en este barrido) —
     `XCLAIM` (si vacío: carrera, saltar); construir `failure_meta` (todos str)
     incluyendo `dead_letter_last_error=""` (sin intento fresco, el error concreto no
     se conoce — limitación aceptada, ver Consecuencias negativas);
     **Paso 1** `XADD deadletter_stream {sobre + failure_meta}` (buffer operativo, best-effort);
     **Paso 2** `await on_dead_letter(envelope, failure_meta)` — **si lanza**: loguear warning,
     `continue` (no XACK; el mensaje permanece en PEL para el próximo barrido; **cierra R-4**:
     no hay dead-letter sin traza durable); si `on_dead_letter is None`: saltar al paso 3;
     **Paso 3** `XACK`; `stats.dead_lettered += 1`.
   - Si no: **reclamar y reprocesar** —
     `XCLAIM` (si vacío: carrera, saltar); `stats.reclaimed += 1`; invocar
     `on_reclaim(msg_id)` (try/except); ejecutar `handler(fields)`:
     * Éxito: `XACK`, `stats.reprocessed_ok += 1`.
     * Excepción (R-5b): capturar `str(exc)` como `last_error`; calcular
       `effective_count = times_delivered + 1` (XCLAIM ya incrementó el contador
       internamente); si `effective_count >= max_deliveries` → **dead-letter en el mismo
       barrido** con `dead_letter_last_error=last_error`:
       **Paso 1** `XADD deadletter_stream` (buffer); **Paso 2** `on_dead_letter` — si lanza:
       no XACK, `stats.reprocessed_fail += 1`, seguir con el próximo mensaje (**cierra R-4**);
       si ok: **Paso 3** `XACK`, `stats.dead_lettered += 1`; en todo caso
       `stats.reprocessed_fail += 1`; si no → dejar en PEL sin ackear para el próximo barrido.

El helper es **libre de BD**: no importa `asyncpg` ni `audit`.  La auditoría del
dead-letter es responsabilidad del servicio a través del callback.

### Contrato de cadencias (invariante que todos los callers deben respetar)

```
min_idle_ms  (60 s)  > RECLAIM_INTERVAL  (30 s)  > block timeout  (5 s)
```

Un consumidor activo que tarda hasta 55 s en procesar un mensaje nunca sufrirá
una pre-emción por el barrido de reclamación.

### Cableado por servicio

**signature-service (Fase 1):**
- `DEADLETTER_STREAM = "orpycamcp.signature.deadletter"` — un stream por servicio, no uno
  global, para evitar que los dead-letters de servicios distintos interfieran entre sí.
- `RECLAIM_INTERVAL = 30.0 s`, `min_idle_ms = 60 000`, `max_deliveries = 5`.
- Callback `_persist_dead_letter(envelope, failure_meta)` — **durable persistence (R-4)**:
  1. Obtiene conexión al schema del tenant (`acquire_tenant(tenant_slug)`).
  2. Abre transacción: `INSERT INTO evento_dead_letter (...) ON CONFLICT (origin_group, event_id) DO NOTHING
     RETURNING event_id`.
  3. Si devuelve fila (INSERT real): `audit.append(...)` en la **misma transacción** →
     ambas escrituras commitean o ambas fallan (atomicidad).
  4. Si devuelve None (ON CONFLICT): idempotente, no audita.
  5. Errores transientes (BD caída) **no se capturan** → propagan al helper → no XACK →
     el mensaje permanece en PEL para reintento.
  6. Evento malformado permanente (sin `tenant_slug`): loguea error, **retorna normalmente**
     → el helper puede XACK el poison pill (reintentar loop infinito no ayuda).
  - El stream Redis `orpycamcp.signature.deadletter` es el **buffer operativo/replay**;
    la **copia autoritativa y durable** es Postgres (`evento_dead_letter` + `audit_log`).

**workflow-service (Fase 2):**
- `DEADLETTER_STREAM = "orpycamcp.workflow.deadletter"`.
- Mismos parámetros: `RECLAIM_INTERVAL = 30.0 s`, `min_idle_ms = 60 000`, `max_deliveries = 5`.
- Callback `_persist_dead_letter` con la misma semántica que Fase 1, más la mejora **R1**:
  - **R1 — evento sin `tenant_slug`**: en vez de solo loguear y retornar, se escribe una
    entrada en `public.audit_log` bajo `SYSTEM_TENANT` (obtenida via `get_pool().acquire()`
    sin fijar `search_path`). Si esa escritura falla por error transiente, la excepción
    propaga → el helper no XACK → reintento. Cuando la escritura es exitosa (o el módulo
    de auditoría no está disponible), la función retorna normalmente y el helper puede XACK.
    Resultado: incluso el poison pill permanentemente malformado deja una traza durable
    en `public.audit_log` (Ley 594/2000 art. 4 — trazabilidad de todo acto sobre documentos).
- **Xack en el loop normal: ack-solo-en-éxito (A-1, cierra R-WF1)** — consistente con
  signature-service. Un fallo del handler de distribución (`auto_distribute`) deja el
  mensaje en la PEL sin acusarlo; el barrido `reclaim_stale` lo recogerá, lo reintentará
  y, tras `max_deliveries=5`, lo escalará al dead-letter stream con traza durable en
  `evento_dead_letter` + `audit_log`. Con esto los fallos de handler en vivo (el modo
  de fallo más común de la distribución E05) ya no se descartan en silencio: fluyen al
  PEL → reclaim → dead-letter durable (Acuerdo AGN 060/2001 art. 3, Ley 594/2000 art. 4).
  - El reproceso es seguro: `auto_distribute` es idempotente por guard RN-8
    (`get_next_step_number > 1 → return`); un radicado ya distribuido no genera
    asignación duplicada si el mensaje se reintenta.
  - `CancelledError` se re-lanza siempre para permitir el apagado limpio del worker.
- El barrido se ejecuta en el bucle principal de `run()` con `time.monotonic()`;
  un fallo del barrido loguea WARNING y continúa sin tumbar el loop.
- Las propiedades R-9, R-5/R-6, R-4 aplican igual que en Fase 1:
  - **R-9** (`actor`): el acto de sistema lo ejecuta el worker, no el usuario original.
    El actor original se conserva en `payload["envelope"]["actor"]`.
  - **R-5/R-6** (`payload`): la fila de `audit_log` incluye el sobre íntegro más los
    identificadores de negocio y el `failure_meta`. Ambas filas de Postgres son
    **autosuficientes para reconstrucción forense** (Ley 594/2000 art. 4 y art. 19).

### Idempotencia

La idempotencia en el reproceso es **responsabilidad del handler**, no del helper.
En signature-service `_handle` ya es idempotente: el `UPDATE ... WHERE nivel_seguridad <> $1`
es un guard natural; una segunda ejecución con el mismo nivel no produce cambios (0 filas).
Para servicios donde el efecto no es idempotente por naturaleza (notificación de email en
notification-service), se añade una tabla de deduplicación por tenant (Fase 3 — implementado).

**notification-service (Fase 3) — patrón insert-then-send:**

La tabla `notification_processed_events(event_id TEXT PRIMARY KEY)` (migración tenant `004`)
actúa como guard de deduplicación: antes de cualquier efecto externo (webhook, email), el
handler realiza `INSERT … ON CONFLICT DO NOTHING RETURNING event_id`.

- `INSERT` devuelve fila → evento nuevo → continúa con efectos externos.
- `INSERT` devuelve `NULL` (ON CONFLICT) → ya procesado → retorno silencioso (dedup skip).
- Error de BD en el INSERT → propaga al caller (A-1: no ack → PEL → reclaim sweep).

**Trade-off insert-then-send:** si el worker muere entre el INSERT (claim) y el envío
del email, ese correo se pierde permanentemente en el periodo de fallo. El trade-off es
aceptado: el correo no es el registro legal; `audit_log` y el radicado en
`document-service` son la fuente autoritativa (Ley 594/2000 art. 4 y art. 19).

**Job de retención pendiente:** `notification_processed_events` crece indefinidamente.
Un proceso de limpieza periódica (`DELETE WHERE processed_at < now() - interval '7 days'`)
**debe** programarse como tarea separada; no está implementado en esta migración.

## Fases de implementación

| Fase | Servicio | Estado | Nota |
|------|----------|--------|------|
| 1 | signature-service | **Implementado** | Ack-solo-en-éxito (A-1); handler idempotente por guard SQL |
| 2 | workflow-service | **Implementado** | Ack-solo-en-éxito (A-1, cierra R-WF1); handler idempotente por guard SQL (`get_next_step_number > 1`); R1: evento sin `tenant_slug` auditado bajo `SYSTEM_TENANT` en `public.audit_log`; cobertura durable ampliada a fallos de handler en vivo (no solo crash-recovery) |
| 3 | notification-service | **Implementado** | Dedup por `notification_processed_events` (insert-then-send); ack-solo-en-éxito (A-1); dead-letter durable `evento_dead_letter` + `audit_log`; R1 bajo SYSTEM_TENANT; reclaim por los dos streams (workflow y document) |
| 4 | notification-service + shared | **MVP-1 implementado** (dedup TTL); **MVP-2 implementado** (XTRIM DLQ); **MVP-3 implementado** para signature+workflow (ver abajo) **y para notification-service** (cerrado después, ver «Cierre … notification-service, 2026-07-01»: `app/jobs/deadletter_retention.py`, cableado en `app/workers/stream_worker.py`) | MVP-1: Purga periódica de `notification_processed_events` por TTL configurable (default 7 d). MVP-2: XTRIM MINID por edad (30 d) en los tres DLQ streams; helper en `orpycamcp_common.events.trim_deadletter`; cableado en los tres workers con cadencia diaria. MVP-3: Purga de dead-letters terminales (resolved/discarded) con gracia de 90 d; `pending` NUNCA se purga |
| 5 | signature-service + workflow-service (+ notification-service, Inc. 2, cerrado 2026-07-01: `app/routers/admin_deadletter.py`) | **Implementado** | Replay/discard API con `PERM_DLQ_ADMIN`; clearance fail-closed sobre nivel vivo (404 neutro); atomicidad replay+estado+audit en una transacción; assert tenant match; concurrencia por `UPDATE … WHERE status='pending'`; columna `status` (pending/resolved/discarded) en `evento_dead_letter` |

---

## Fase 4 — Retención y ciclo de vida

### Clasificación archivística

`notification_processed_events` es una tabla **operacional** (no documental): su
único propósito es evitar efectos externos duplicados durante el procesamiento de
eventos.  No registra actos sobre documentos oficiales; no requiere acta de
eliminación (Ley 594/2000 art. 23 aplica a series documentales, no a datos de
infraestructura de deduplicación).  Basta con un log estructurado del conteo de
filas eliminadas por tenant.

### MVP-1 — Purga TTL de `notification_processed_events` (implementado)

**Mecanismo:** `DELETE FROM notification_processed_events WHERE processed_at < now() - make_interval(days => $1)`.
El placeholder `$1` recibe el TTL en días (default 7); la fecha no se interpola en el SQL.

**TTL de 7 días:** la ventana real de redelivery/reclamación es de minutos
(RECLAIM_INTERVAL 30 s × max_deliveries 5 = 2,5 min en el peor caso con mensajes
bloqueados).  7 días es un margen holgado que permite debugging y no tiene riesgo
de eliminar entradas aún necesarias para deduplicación.

**Multi-tenant:** `app/jobs/retention.py` lista los tenants activos desde
`public.tenants WHERE status='active' AND provisioning_status='active'` y purga
cada uno con `acquire_tenant(slug)` (search_path por tenant).  Un fallo en un
tenant se loguea como WARNING y no aborta los demás.

**Cadencia:** enganchado al loop de `stream_worker.run()` con gate `time.monotonic()`
(igual patrón que `reclaim_stale`), intervalo configurable `retention_interval_seconds`
(default 86 400 s / 24 h).  La primera ejecución ocurre tras el primer intervalo completo
desde el arranque (deja que el pool esté caliente; el margen es irrelevante frente al TTL
de 7 días).  Un fallo de retención loguea WARNING y no tumba el loop.

**Modo standalone:** `python -m app.jobs.retention` inicializa el pool, ejecuta
`run_once()`, cierra el pool.  Útil para cron externo o ejecución manual.

**Settings:** `retention_dedup_days: int = 7` y `retention_interval_seconds: int = 86400`
en `app/core/config.py` (pydantic-settings, sobreescribibles por env).

### MVP-2 — XTRIM del DLQ Redis (implementado)

**Mecanismo:** `XTRIM <stream> MINID ~ <cutoff_id>` con
`cutoff_id = f"{cutoff_ms}-0"` y `cutoff_ms = int(now_utc_ms) - max_age_ms`.
`approximate=True` (`~`) para eficiencia (evita rebalanceos innecesarios de
la estructura interna de Redis).

**Seguridad del recorte por MINID vs. MAXLEN ciego:** se usa MINID basado en
edad (horizonte 30 días, configurable) en lugar de un `MAXLEN ~ N` fijo.  Un
`MAXLEN` ciego podría descartar una entrada cuyo `on_dead_letter` aún no
confirmó el INSERT en Postgres; bajo MINID eso no puede ocurrir porque cualquier
entrada más vieja que el horizonte está garantizada espejada en Postgres (una
persistencia fallida produce un nuevo `XADD` al DLQ + reintento dentro de la
ventana de reclamación, que es de minutos, muy por debajo de los 30 días).

**Helper:** `orpycamcp_common.events.trim_deadletter(redis, stream, *, max_age_ms) → int`.
Sin dependencia de BD; devuelve el número de entradas eliminadas; propaga
excepciones Redis al caller.

**Cableado:** los tres workers con cadencia diaria (default `retention_interval_seconds = 86 400 s`):

- `signature-service` — gate nuevo `_last_dlq_trim`, `DLQ_TRIM_INTERVAL` leído de
  `settings.retention_interval_seconds`; config: `retention_dlq_days=30`, `retention_interval_seconds=86400`.
- `workflow-service` — mismo patrón que signature.
- `notification-service` — reusa el gate diario de MVP-1 (`_last_retention`): tras
  `retention.run_once()`, llama `trim_deadletter` en el mismo bloque; config: añade
  `retention_dlq_days=30`.

Cada worker envuelve el trim en su propio `try/except` (CancelledError re-lanza;
cualquier otro error loguea WARNING y no tumba el loop ni bloquea el gate).

**Settings nuevos** (por servicio):
```
retention_dlq_days: int = 30  # configurable por env
retention_interval_seconds: int = 86400  # solo signature y workflow; notification ya lo tenía
```

**Nota archivística:** el DLQ Redis es infraestructura operativa (buffer de replay),
no un registro legal.  La copia autoritativa vive en Postgres.  `XTRIM MINID ~ ...`
no requiere acta de eliminación (Ley 594 art. 23 aplica a series documentales, no a
buffers de infraestructura).  El log estructurado de `trim_deadletter` (INFO con
`entries_removed`) sirve como traza operativa suficiente.

### MVP-3 — Retención de `evento_dead_letter` (implementado para signature+workflow)

**Estado:** Implementado en signature-service y workflow-service.  notification-service
quedaba acoplado al Incremento 2 (su `status`+purga eran interdependientes con la API de
inspección/discard); **cerrado después, ver «Cierre … notification-service, 2026-07-01»**
(`app/jobs/deadletter_retention.py`).

**Clasificación archivística (ratificación del archival-compliance-auditor):**
`evento_dead_letter` es infraestructura operacional de cola de mensajes, no una
serie documental bajo Ley 594/2000 art. 23.  La copia legal de cada dead-letter vive
en `audit_log` (inmutable, escrita atómicamente en la inserción por `_persist_dead_letter`
y actualizada en la resolución/descarte por los endpoints de Fase 5).  La purga no
elimina la traza legal — elimina la fila operacional cuyo uso ya concluyó.  No se
requiere acta de eliminación (art. 23 aplica a series documentales, no a colas de
infraestructura).  Basta con log estructurado del conteo por tenant.

**Mecanismo:**
```sql
DELETE FROM evento_dead_letter
WHERE status IN ('resolved', 'discarded')
AND tenant_slug = $1 AND origin_group = $2
AND resolved_at < now() - make_interval(days => $3)
```
`$3` recibe el TTL en días (default 90); el intervalo nunca se interpola como texto.
`'pending'` NUNCA aparece en el WHERE — las filas sin resolución humana no se purgan.

**Guardia fail-closed:** `DeadLetterRepository.purge_terminal(days, tenant_slug,
origin_group)` lanza `ValueError` si `days < 1`; un valor 0 o negativo haría que
`make_interval(days => 0)` borrase TODAS las filas terminales (del tenant y
origin_group dados) independientemente de la edad (ventana ilimitada).  La guardia
cierra ese riesgo antes del `execute`.  Invariante en el que se apoya el `WHERE`:
un estado terminal (`resolved`/`discarded`) siempre implica `resolved_at IS NOT
NULL`, porque `transition_status` fija ambos campos en la MISMA `UPDATE` — una
fila que violara esa invariante simplemente nunca cumpliría `resolved_at <
now() - interval` (`NULL` nunca es `< algo` en SQL), o sea fail-closed, no
fail-open, ante cualquier dato corrupto.

**TTL de 90 días:** la ventana de reclamación activa es de minutos
(`RECLAIM_INTERVAL 30 s × max_deliveries 5 = 2,5 min` en el peor caso).  90 días es
un margen holgado que permite auditorías, debugging extendido y rollback sin riesgo
de eliminar filas aún útiles operativamente.

**Multi-tenant:** `app/jobs/deadletter_retention.py` lista los tenants activos desde
`public.tenants WHERE status='active' AND provisioning_status='active'` y purga
cada uno con `acquire_tenant(slug)` (search_path por tenant) **y** con `DLQ_GROUP`
de `app/core/constants.py` (ver subsección siguiente).  Un fallo en un tenant se
loguea como WARNING y no aborta los demás.

**`evento_dead_letter` es una tabla física COMPARTIDA — aislamiento por
`origin_group`.**  Tanto signature-service como workflow-service crean la misma
tabla (`CREATE TABLE IF NOT EXISTS`) en el MISMO schema `tenant_{slug}` de la
misma base de datos: las filas de ambos servicios conviven en una sola tabla y se
distinguen por la columna `origin_group` (el `GROUP` del consumer Redis que las
generó — `"signature-service"` / `"workflow-service"`; `reclaim_stale` la
propaga vía `failure_meta["dead_letter_group"]`).  Antes de esta corrección
**ninguna** consulta del repositorio llevaba el predicado `origin_group`, lo que
producía dos fallos reales (mismo tenant, no fuga cross-tenant, pero handler
incorrecto):
  - `purge_terminal` de un servicio borraba también los dead-letters terminales
    del OTRO servicio, acoplando sus ventanas de retención.
  - Los endpoints `/admin/deadletter` de un servicio veían, listaban, reproducían
    y descartaban los dead-letters del OTRO servicio — un replay de
    signature-service sobre un evento de workflow-service invocaría
    `apply_reclassification` sobre un payload de distribución (handler
    equivocado).

Corrección: **toda** consulta de `DeadLetterRepository` (`list_pending`,
`get_by_event_id`, `get_for_update`, `transition_status`, `purge_terminal`) recibe
ahora un parámetro `origin_group` obligatorio y lo añade al `WHERE`.  La fuente
única del valor es `app/core/constants.py` (`DLQ_GROUP`), consumida por
`document_consumer.py` (como `GROUP`, para no romper el nombre usado en el resto
del worker), por `app/routers/admin_deadletter.py` y por
`app/jobs/deadletter_retention.py` — está en su propio módulo (no en
`document_consumer.py`, donde vivía antes) precisamente porque
`document_consumer.py` importa `app.jobs.deadletter_retention` a nivel de módulo
*antes* de que `GROUP` quedara definido; importarlo de vuelta desde
`document_consumer` habría creado un ciclo de imports.

**Cadencia:** enganchado al gate diario ya existente `_last_dlq_trim` en
`document_consumer.run()` — tras el `events.trim_deadletter` del DLQ Redis, se
invoca `await deadletter_retention.run_once()` en su propio `try/except`.  Ambas
son tareas diarias de housekeeping sin dependencia de orden; compartir el gate evita
una segunda variable monotónica.  `CancelledError` re-lanza; cualquier otro error
loguea WARNING y no tumba el loop ni bloquea el gate.

**Modo standalone:** `python -m app.jobs.deadletter_retention` inicializa el pool,
ejecuta `run_once()`, cierra el pool.  Útil para cron externo o ejecución manual.

**Settings nuevos** (signature-service y workflow-service):
```
retention_deadletter_days: int = 90  # gracia post-cierre; sobreescribible por env
```

**Nota:** `pending` nunca se purga.  La tabla `evento_dead_letter` existe precisamente
para conservar esas filas hasta que un admin tome una decisión; borrarlas antes de eso
violaría el principio de trazabilidad (Ley 594/2000 art. 4).

**Corrección de tests de cadencia (2026-06-30):** los dos tests de
`test_worker_calls_run_once_after_interval` y `test_worker_run_once_failure_does_not_crash_loop`
en `workflow-service/tests/test_deadletter_retention.py` colgaban indefinidamente
(OOM/exit 137) porque conducían el worker con
`redis_mock.xreadgroup.return_value = []` (un `AsyncMock` que retorna al instante,
sin ceder el control real al loop) más `asyncio.create_task(run()) +
asyncio.wait_for(..., timeout=N) + task.cancel()`.  Como el `while True` de
`document_consumer.run()` no tiene ningún `await` real que ceda en su camino feliz,
un mock que retorna instantáneo hace *busy-spin* y nunca le da una vuelta al loop de
eventos: el timer de `wait_for` jamás se dispara y `task.cancel()` nunca gana la
carrera — cuelgue infinito más historial de llamadas del `AsyncMock` creciendo sin
límite (la causa del OOM).  La corrección, ya usada en
`signature-service/tests/test_deadletter_retention.py`, usa un `side_effect`
**finito** —`redis_mock.xreadgroup.side_effect = [[], asyncio.CancelledError()]`—
y conduce el worker con `await document_consumer.run()` directo bajo
`pytest.raises(asyncio.CancelledError)`, sin `create_task`/`wait_for`/`cancel`.
Ambos servicios quedan simétricos.

## Fase 5 — API de inspección, replay y discard (implementado)

**Servicios:** signature-service y workflow-service.  notification-service
(Incremento 2) quedó pendiente en esta fase y **se cerró después** (ver «Cierre …
notification-service, 2026-07-01»: `admin_deadletter.py`, replay con `PERM_DLQ_ADMIN`).
El job de purga (MVP-3) ya está implementado — ver sección anterior.

**Modelo de estado.**  Migración de tenant (`signature 006`, `workflow 010`) añade
a `evento_dead_letter` las columnas `status TEXT NOT NULL DEFAULT 'pending'
CHECK (status IN ('pending','resolved','discarded'))` y `resolved_at TIMESTAMPTZ NULL`,
más índices parciales (`ix_edl_pending` sobre los pendientes, `ix_edl_resolved`
sobre los cerrados).  La migración es idempotente (patrón
`DO $$ … information_schema.columns … $$`, PG15 sin `ADD COLUMN IF NOT EXISTS`).

**Permiso.**  `PERM_DLQ_ADMIN` (auth-service migración tenant `009`) protege todas
las rutas a nivel de router (`Depends(require_permission("PERM_DLQ_ADMIN"))`).

**Endpoints** (prefijo `…/admin/deadletter`):
- `GET /` — lista resúmenes paginados (`?status=&origin_stream=&page=&size=`),
  `X-Total-Count` por ventana `COUNT(*) OVER()`.  El resumen **no expone**
  `payload`, `tracking_number` ni `last_error` (no filtra la existencia de objetos
  clasificados).
- `GET /{event_id}` — detalle completo (sobre + failure), tras gate de clearance.
- `POST /{event_id}/replay` — reprocesa el evento.
- `POST /{event_id}/discard` — descarta con motivo obligatorio (`reason`).

**Gate de clearance fail-closed (no-read-up, RF-SEG-08).**
`assert_can_read_deadletter` resuelve el nivel del objeto sobre el **valor VIVO**
de `radicados.nivel_seguridad` (no confía solo en el nivel embebido en el sobre,
que pudo quedar obsoleto): `nivel_objeto = max(nivel_vivo, nivel_embebido)`.  Ante
cualquier duda (sin identificadores, radicado no hallado, tabla `radicados`
ausente) **degrada a CLASIFICADA=3**, nunca a público.  Si el llamante no alcanza
el nivel responde **404 neutro** (no 403): no revela existencia ni nivel.

**Atomicidad del replay.**  Toda la operación corre en una sola `conn.transaction()`:
`SELECT … FOR UPDATE` (predicado de tenant), assert tenant match (422 si difiere),
gate de clearance, re-invocación del **handler-core** (`apply_reclassification` en
signature, `apply_distribution` en workflow) sobre la MISMA conexión, transición
`pending → resolved`, y `audit.append`.  Si el handler falla se responde **502** y
la transacción revierte → el evento permanece `pending` (no se pierde).  El
handler-core se extrajo del worker (`_handle`) sin cambiar su comportamiento; ambos
caminos comparten la misma lógica.

**Concurrencia.**  La transición es `UPDATE … WHERE status='pending'`: dos admins
que reprocesen el mismo evento serializan por el `FOR UPDATE`; el segundo obtiene
0 filas y recibe **409** (`already_resolved`).

**Aislamiento de tenant.**  Toda consulta lleva predicado explícito
`tenant_slug = $caller` además del `search_path`, defensa en profundidad contra
fuga cross-tenant (el DLQ es por servicio y agrega todos los tenants en Redis, pero
la tabla Postgres y la API están acotadas por tenant).

**Aislamiento por `origin_group` (tabla compartida).**  `evento_dead_letter` la
crean signature-service y workflow-service en el MISMO schema de tenant (ver
detalle en MVP-3 arriba); toda consulta de este router pasa además
`origin_group = GROUP` (constante propia del servicio, `app/core/constants.py`)
de modo que `list`, `detail`, `replay` y `discard` solo ven/operan sobre los
dead-letters generados por ESTE servicio, nunca los del otro consumidor de la
misma tabla.

**Gateway.**  Se añadió la ruta `/api/v1/workflow/admin/` al proxy
(`signature/admin` ya estaba cubierto por `/api/v1/signature/`).

### Cierre de seguimientos no bloqueantes (signature-service, 2026-07-01)

Al cerrar la Fase 5 quedaron documentados tres seguimientos no bloqueantes en
signature-service (el listado no filtraba por clearance, el detalle no dejaba
rastro de auditoría, y `origin_group` era nullable sin índice compuesto).  Los
tres se cerraron en esta iteración:

1. **Filtro de clearance en el listado.**  `assert_can_read_deadletter` se
   refactorizó para delegar en una función no-lanzadora
   `resolve_deadletter_level(conn, row) -> int` (single source of truth de la
   resolución de nivel).  El listado (`GET /`) usa ahora
   `DeadLetterRepository.list_scoped_for_clearance` (candidatas acotadas por
   tenant+group+status, **incluye** `payload`, sin `LIMIT`/`OFFSET` de BD pero
   con tope de seguridad `cap=1000`) y filtra fila-a-fila con la misma función
   de nivel que el detalle: una fila cuyo objeto excede el clearance del
   llamante se omite por completo (no aparece ni cuenta en `X-Total-Count`),
   fail-closed a CLASIFICADA ante cualquier error de resolución.  La
   paginación (`page`/`size`) se aplica en la capa de aplicación **después**
   del filtrado, porque el tamaño del conjunto visible solo se conoce tras
   evaluar cada fila.  Se descartó materializar un nivel-columna o snapshot en
   `evento_dead_letter`: el gate usa deliberadamente el nivel **vivo** del
   radicado (una reclasificación hacia arriba tras el dead-letter debe
   ocultarse de inmediato), y el volumen de dead-letters es bajo por diseño
   (fallos excepcionales tras agotar reintentos), así que la resolución
   por-fila es proporcionada.
2. **Auditoría de vista.**  `GET /{event_id}` autorizado (tras el gate) deja
   un asiento `evento.dead_letter.viewed` en `audit_log` con actor humano y
   payload mínimo (`previous_status`, `event_type`, `origin_stream` — sin
   contenido de negocio ni `failure`, para no filtrar detalle clasificado vía
   auditoría).  Una vista bloqueada (404 neutro) **no** audita, de lo
   contrario confirmaría la existencia del evento a un admin sin clearance.
3. **Migración `007_deadletter_origin_group_notnull_index.sql`.**
   `origin_group SET NOT NULL` (segura sin backfill: los tres consumidores
   nunca insertan `NULL`) + `CREATE INDEX IF NOT EXISTS
   ix_edl_tenant_group_status ON evento_dead_letter (tenant_slug,
   origin_group, status)`.  El nombre del índice es único y **compartido**
   entre signature/workflow/notification sobre la misma tabla física:
   idempotente por diseño (`SET NOT NULL` es no-op si ya lo es en PG15,
   `CREATE INDEX IF NOT EXISTS` lo crea el primer servicio que corra, los
   demás no-op).  Validado en un Postgres 15 desechable antes de integrar
   (re-ejecución sin error, `INSERT` con `origin_group=NULL` rechazado,
   índice creado una sola vez).

### Cierre de seguimientos no bloqueantes (notification-service, 2026-07-01)

Los mismos tres seguimientos no bloqueantes quedaban abiertos en
notification-service (documentados al estrenar su superficie admin en el
Incremento 2, § Fase 5). Se cerraron con el mismo diseño aplicado en
signature-service:

1. **Filtro de clearance en el listado.** `assert_can_read_deadletter` delega
   en `resolve_deadletter_level(conn, row) -> int` (no-lanzadora, single
   source of truth compartida con el gate de detalle). `GET
   /api/v1/notification/admin/deadletter` usa
   `DeadLetterRepository.list_scoped_for_clearance` (candidatas acotadas por
   tenant+`origin_group="notification-service"`+status, con `payload`, sin
   `LIMIT`/`OFFSET` de BD, tope de seguridad `cap=1000`) y filtra fila-a-fila
   con la misma resolución de nivel que el detalle: fila excluida
   (no aparece ni cuenta en `X-Total-Count`) si su objeto excede el clearance
   del llamante, fail-closed a CLASIFICADA ante cualquier error de resolución
   (incluida la ausencia de la tabla `radicados`, el caso más frecuente en
   notification: fail-closed a CLASIFICADA para todas las filas hasta que el
   admin tenga clearance 3 — mismo comportamiento que el gate de detalle ya
   tenía, el listado lo replica en vez de divergir). Paginación aplicada en
   app **después** del filtrado. Mismas razones que signature-service para
   descartar una columna/snapshot de nivel materializado (nivel vivo, volumen
   bajo de dead-letters).
2. **Auditoría de vista.** `GET /{event_id}` autorizado deja un asiento
   `evento.dead_letter.viewed` en `audit_log` (actor humano, payload mínimo
   `previous_status`/`event_type`/`origin_stream`, dentro de una transacción
   para su durabilidad). Una vista bloqueada (404 neutro) no audita.
3. **Migración `007_deadletter_origin_group_notnull_index.sql`.** Idéntica en
   contenido y nombre de índice (`ix_edl_tenant_group_status`) a la de
   signature/workflow sobre la misma tabla física compartida; idempotente por
   diseño. Validado en un Postgres 15 desechable (re-ejecución sin error,
   `INSERT` con `origin_group=NULL` rechazado, índice creado una sola vez).

13 tests nuevos/actualizados, 113 verdes en Docker.

### Cierre de seguimientos no bloqueantes (workflow-service, 2026-07-01)

Los mismos tres seguimientos se cerraron en workflow-service con el diseño
idéntico al de signature-service: filtro de clearance fila-a-fila en el listado
(`resolve_deadletter_level` como single source of truth compartida con el gate de
detalle, `DeadLetterRepository.list_scoped_for_clearance` con tope `cap=1000`,
paginación en la capa de aplicación después del filtrado, fail-closed a
CLASIFICADA), asiento `evento.dead_letter.viewed` en la vista de detalle
autorizada (una vista bloqueada no audita), y la migración
`011_deadletter_origin_group_notnull.sql` (`origin_group SET NOT NULL` + índice
compartido `ix_edl_tenant_group_status`, idempotente sobre la tabla física
común). 121 tests verdes en Docker.

### Pulido de seguimientos Baja del listado (los 3 servicios, 2026-07-01)

Tres observaciones Baja de la auditoría sobre el listado admin se pulieron en los
tres servicios: (1) **cap señalizado** — `list_scoped_for_clearance` acota a
`DEADLETTER_CANDIDATE_CAP=1000` candidatas antes del filtro de clearance; al
alcanzarlo el listado deja de truncar en silencio y emite `logger.warning` + el
header `X-Truncated: true`. (2) **resolución de nivel en lote** — nuevo
`resolve_deadletter_levels(conn, rows)` que resuelve el nivel de todas las
candidatas con una sola consulta a `radicados` (`= ANY($1)`), byte-idéntico
fila-a-fila al `resolve_deadletter_level` per-row (intacto para el gate del
detalle), preservando las cuatro ramas fail-closed a CLASIFICADA. (3) **contrato
de identidad** — el listado de workflow pasa a `get_actor` (401 si falta
`X-User-Id`), en paridad con signature/notification. Auditado APTO/APTO.
Seguimiento Baja restante: `X-Truncated` se evalúa sobre el conjunto pre-filtro de
clearance y tiene un falso positivo en el borde exacto `len==cap`; keyset
pagination si el volumen creciera; unificar `get_actor` también en
detail/replay/discard de workflow.

## Alternativas descartadas

**XAUTOCLAIM como decisor del dead-letter.**
`XAUTOCLAIM` transfiere entradas idle en una sola llamada pero no expone
`times_delivered` en su respuesta: no se puede distinguir un mensaje entregado 1 vez
de uno entregado 50 veces. Además incrementa implícitamente el contador, lo que
interferiría con el umbral de dead-letter. Descartado.

**Dead-letter stream único y global por stream fuente.**
Un stream `orpycamcp.document.events.deadletter` compartido por todos los grupos
consumidores mezclaría dead-letters de servicios con semánticas distintas y dificultaría
la observabilidad y el replay por servicio. Descartado; se usa un stream por servicio.

**Broker externo con DLX (RabbitMQ, Kafka).**
ADR-010 ya compromete Redis Streams como bus de eventos para evitar dependencias
adicionales de infraestructura. Cambiar de broker sólo para el dead-letter sería
desproporcionado. Descartado.

**Reintento infinito sin dead-letter.**
Los poison-pills acumularían PEL indefinidamente, degradando las consultas `XPENDING` y
ocultando el problema. Sin dead-letter no hay trazabilidad del evento no entregado.
Descartado: viola ADR-008 (auditoría inmutable) y el principio de Ley 594/2000.

**Descarte silencioso (XACK sin reprocesar).**
Un `XACK` sin registrar el evento fallido elimina la posibilidad de auditar o repetir
el procesamiento. Violaría directamente la exigencia de trazabilidad de Ley 594/2000.
Descartado.

## Consecuencias

**Positivas:**
- Poison-pills dejan de acumularse en la PEL; quedan en un stream inspeccionable y
  auditado.
- Consumidores caídos ya no retienen mensajes: el barrido los transfiere al consumidor
  activo.
- La lógica reutilizable en `orpycamcp_common.events` garantiza que todos los servicios
  siguen el mismo protocolo (cadenas de hash, sobre canónico, estadísticas uniformes).
- `ReclaimStats` permite instrumentar el barrido con métricas / alertas (Fase futura).

**Negativas:**
- Cada servicio debe configurar el contrato de cadencias explícitamente (invariante
  `min_idle > interval > block`). Un error de configuración podría provocar
  pre-emciones innecesarias.
- Los servicios con efectos no idempotentes (Fase 3) necesitan una tabla de dedup
  adicional, lo que eleva la complejidad local.

**Limitaciones conocidas y trabajo futuro (diferidos):**

- **R-4 — Atomicidad audit↔DLQ**: **RESUELTO**.  El callback `_persist_dead_letter`
  escribe `evento_dead_letter` y `audit_log` en la **misma transacción Postgres**
  (outbox-style), y el `XACK` en Redis queda **gateado** por el éxito de esa
  escritura: si la BD está caída, el mensaje permanece en la PEL y se reintenta en el
  próximo barrido.  No hay ventana entre el XACK y la traza durable → no hay
  dead-letter sin registro inmutable.

- **R-6 — Redis como soporte volátil**: **RESUELTO** (mitigación completa).  El
  callback `_persist_dead_letter` persiste el sobre íntegro en la tabla
  `evento_dead_letter` (Postgres, durable) y en `audit_log` dentro de la misma
  transacción.  El stream Redis `orpycamcp.signature.deadletter` pasa a ser el
  **buffer operativo para replay/inspección**; la **copia autoritativa** es Postgres.
  Trabajo futuro pendiente: política de retención del DLQ Redis, mecanismo de replay,
  acta de eliminación al purgar el stream (Ley 594 art. 23), gobernanza del lector
  del DLQ (filtro por tenant).

- **R-5b — Huérfanos sin `last_error`**: cuando un mensaje llega al umbral sin un
  intento fresco en el barrido actual (rama de dead-letter directo), `dead_letter_last_error`
  es `""`. El error concreto del último fallo ya no está disponible en ese momento.
  Esto es una limitación aceptada; la fila de auditoría incluye `dead_letter_delivery_count`
  que permite correlacionar con logs del periodo anterior.

---

## Gobernanza del futuro lector del DLQ

El DLQ (`orpycamcp.signature.deadletter`, etc.) es un stream global por servicio que
agrega payloads de **todos los tenants**. Cualquier futuro endpoint de inspección o
replay del DLQ:

- Debe exigir rol de **plataforma** (ej. `plataforma-admin`), nunca rol de tenant.
- Debe **filtrar por `tenant_slug`** antes de devolver entradas: nunca exponer el
  stream DLQ crudo a un tenant (contendría payloads de otros tenants).
- Debe implementar deduplicación por `event_id` en el lector si se usa semántica
  at-least-once (XADD ok + XACK falla puede duplicar entradas en el DLQ).

---

## Nota 2026-10 (AREA-10-08): el gate de clearance vive en el `WHERE`

Sustituye al filtro en Python descrito en el seguimiento de la Fase 5 (`resolve_deadletter_level[s]`,
`DEADLETTER_CANDIDATE_CAP`, `X-Truncated`), que se retiraron. `orpycamcp_common.deadletter` construye una
consulta única (`LEFT JOIN LATERAL` contra `radicados` vivo; `max(nivel_vivo, nivel_embebido)`; duda = 3) usada
por lista, recuento, detalle, replay y descarte, con paginación y total exactos en una transacción
`REPEATABLE READ` de solo lectura. Se descartó guardar el nivel al encolar (una reclasificación posterior hacia
arriba abriría un read-up). Lo no visible responde el mismo `404` que un `event_id` inexistente. Radicado purgado
o sin identificadores sigue siendo nivel 3 (decisión del usuario pendiente).

## Notas de operación

- **`CONSUMER` fijo** (`signature-service-1`): hoy el nombre del consumidor está
  hardcodeado.  Antes de escalar horizontalmente (varias réplicas del worker), debe
  derivarse por réplica/hostname (ej. `f"signature-service-{socket.gethostname()}"`),
  porque `XCLAIM` transfiere la propiedad al `consumer` indicado — si todas las
  réplicas usan el mismo nombre, la propiedad no se transfiere de forma útil.
- **Dedup por `event_id`**: la semántica at-least-once de Redis Streams puede producir
  entradas duplicadas en el DLQ si `XADD` ok pero `XACK` falla.  El lector del DLQ
  debe deduplicar por `event_id` antes de actuar.

---

## Nota PREMIS (E10)

Semánticamente, un dead-letter es un **processing event** en el modelo PREMIS
(Preservation Metadata: Implementation Strategies): un intento de procesar un evento
que fracasó y fue aparcado.  En el plan E10 (preservación), este tipo de acto debería
poder representarse como evento de preservación
(`premis:eventType = "message-delivery-failure"`, `premis:linkingObjectIdentifier`
apuntando al `event_id` del sobre).  La fila en `audit_log` (campo `action=
"evento.dead_letter"`, `payload.envelope.event_id`) proporciona los datos necesarios
para esa representación futura sin cambio de esquema.

## Relacionados

- [ADR-010](ADR-010-libreria-compartida.md) — `orpycamcp_common.events` donde vive el helper.
- [ADR-008](ADR-008-auditoria-inmutable.md) — `audit.append` usado por `_audit_dead_letter`.
- [ADR-016](ADR-016-firma-electronica.md) — signature-service (primer servicio en aplicar este patrón).
- [ADR-009](ADR-009-historial-flujos-eventos.md) — flujos como eventos; workflow-service (Fase 2).
