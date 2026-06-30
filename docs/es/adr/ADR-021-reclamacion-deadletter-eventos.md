# ADR-021: Reclamación de PEL y dead-letter por grupo consumidor en Redis Streams

**Estado**: Aceptado — Fases 1, 2, 3, 4-MVP-1/MVP-2 y 5 (replay/discard API) implementadas (signature-service, workflow-service, notification-service; R-4 resuelto; retención operacional de dedup activa; trim MINID del DLQ Redis activo; API de inspección/replay/discard con `PERM_DLQ_ADMIN`)
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
  2. Abre transacción: `INSERT INTO evento_dead_letter (...) ON CONFLICT (event_id) DO NOTHING
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
| 4 | notification-service + shared | **MVP-1 implementado** (dedup TTL); **MVP-2 implementado** (XTRIM DLQ); MVP-3 pendiente (ver abajo) | MVP-1: Purga periódica de `notification_processed_events` por TTL configurable (default 7 d). MVP-2: XTRIM MINID por edad (30 d) en los tres DLQ streams; helper en `orpycamcp_common.events.trim_deadletter`; cableado en los tres workers con cadencia diaria |
| 5 | signature-service + workflow-service | **Implementado** | Replay/discard API con `PERM_DLQ_ADMIN`; clearance fail-closed sobre nivel vivo (404 neutro); atomicidad replay+estado+audit en una transacción; assert tenant match; concurrencia por `UPDATE … WHERE status='pending'`; columna `status` (pending/resolved/discarded) en `evento_dead_letter` |

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

### MVP-3 — Retención de `evento_dead_letter` (pendiente)

La columna `status` (`pending` / `resolved` / `discarded`) ya está implementada
(Fase 5, ver abajo) y habilita los flujos de inspección y replay.  Lo que queda
pendiente de MVP-3 es el **job de retención** que elimine (o archive) filas en
estado `resolved`/`discarded` tras un periodo configurable.  Requiere:
  1. Ratificación de la clasificación archivística por compliance: ¿son los dead-letters
     parte del registro legal de comunicaciones oficiales o solo infraestructura?
  2. Si son parte del registro: procedimiento de acta de eliminación (Ley 594 art. 23).
**Job de retención no implementado; pendiente de decisión de compliance.**

## Fase 5 — API de inspección, replay y discard (implementado)

**Servicios:** signature-service y workflow-service.  notification-service
(Incremento 2) y el job de purga (MVP-3) quedan pendientes.

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

**Gateway.**  Se añadió la ruta `/api/v1/workflow/admin/` al proxy
(`signature/admin` ya estaba cubierto por `/api/v1/signature/`).

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
