# ADR-021: Reclamación de PEL y dead-letter por grupo consumidor en Redis Streams

**Estado**: Aceptado — Fase 1 implementada (signature-service); Fases 2 y 3 pendientes
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
     `XADD deadletter_stream {sobre + failure_meta}`; `XACK`; invocar
     `on_dead_letter(envelope, failure_meta)` en try/except (un fallo del callback
     nunca bloquea el ack); `stats.dead_lettered += 1`.
   - Si no: **reclamar y reprocesar** —
     `XCLAIM` (si vacío: carrera, saltar); `stats.reclaimed += 1`; invocar
     `on_reclaim(msg_id)` (try/except); ejecutar `handler(fields)`:
     * Éxito: `XACK`, `stats.reprocessed_ok += 1`.
     * Excepción (R-5b): capturar `str(exc)` como `last_error`; calcular
       `effective_count = times_delivered + 1` (XCLAIM ya incrementó el contador
       internamente); si `effective_count >= max_deliveries` → **dead-letter en el mismo
       barrido** con `dead_letter_last_error=last_error` (xadd + xack + callback +
       `stats.dead_lettered += 1`); si no → dejar en PEL sin ackear para el próximo
       barrido; en ambos casos `stats.reprocessed_fail += 1`.

El helper es **libre de BD**: no importa `asyncpg` ni `audit`.  La auditoría del
dead-letter es responsabilidad del servicio a través del callback.

### Contrato de cadencias (invariante que todos los callers deben respetar)

```
min_idle_ms  (60 s)  > RECLAIM_INTERVAL  (30 s)  > block timeout  (5 s)
```

Un consumidor activo que tarda hasta 55 s en procesar un mensaje nunca sufrirá
una pre-emción por el barrido de reclamación.

### Cableado en signature-service

- `DEADLETTER_STREAM = "orpycamcp.signature.deadletter"` — un stream por servicio, no uno
  global, para evitar que los dead-letters de servicios distintos interfieran entre sí.
- `RECLAIM_INTERVAL = 30.0 s`, `min_idle_ms = 60 000`, `max_deliveries = 5`.
- Callback `_audit_dead_letter(envelope, failure_meta)` →
  `audit.append(..., action="evento.dead_letter", canal="system", actor="system")`:
  - **R-9** (`actor`): el acto de sistema lo ejecuta el worker, no el usuario original.
    El actor original se conserva en `payload["envelope"]["actor"]`.
  - **R-5/R-6** (`payload`): la fila inmutable de `audit_log` incluye el sobre íntegro
    (`event_type`, `correlation_id`, `occurred_at`, `payload` del evento) más los
    identificadores de negocio (`tracking_number`, `doc_id`) y el `failure_meta`
    (incluyendo `dead_letter_last_error` cuando viene de la ruta R-5b).
    Esto hace que la fila de Postgres sea **autosuficiente para reconstrucción forense**
    (Ley 594/2000 art. 4 y art. 19) sin depender del DLQ de Redis, que es volátil.
- El barrido se ejecuta en el bucle principal de `run()` con `time.monotonic()`;
  un fallo del barrido loguea WARNING y continúa sin tumbar el loop.

### Idempotencia

La idempotencia en el reproceso es **responsabilidad del handler**, no del helper.
En signature-service `_handle` ya es idempotente: el `UPDATE ... WHERE nivel_seguridad <> $1`
es un guard natural; una segunda ejecución con el mismo nivel no produce cambios (0 filas).
Para servicios donde el efecto no es idempotente por naturaleza (ej. notificación de email
en notification-service, Fase 3), se deberá añadir una tabla de deduplicación por tenant.

## Fases de implementación

| Fase | Servicio | Estado | Nota |
|------|----------|--------|------|
| 1 | signature-service | **Implementado** | Handler idempotente por guard SQL |
| 2 | workflow-service | Pendiente | Handler idempotente por guard SQL / `flow_events` |
| 3 | notification-service | Pendiente | Requiere tabla de dedup (envío de email no idempotente) |

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

- **R-4 — No atomicidad audit↔DLQ**: la auditoría en `audit_log` es best-effort
  _post-XACK_: si el proceso muere entre el XACK y el `audit.append`, el evento queda
  aparcado en el DLQ pero sin fila inmutable en Postgres.  Trabajo futuro: un job de
  reconciliación DLQ→`audit_log` (o patrón outbox transaccional, ya en el roadmap)
  que garantice que todo `event_id` aparcado tenga su fila inmutable.  Por ahora se
  acepta la limitación; la probabilidad es baja y el DLQ permite detección manual.

- **R-6 — Redis como soporte volátil**: Redis Streams no ofrece garantías de retención
  a largo plazo equivalentes a un soporte electrónico archivístico.  Mitigación
  implementada: el callback `_audit_dead_letter` persiste el **sobre íntegro** del
  evento en `public.audit_log` (Postgres, durable), convirtiendo la fila inmutable
  en la copia autoritativa para efectos de Ley 594/2000 art. 19.  El DLQ de Redis
  queda como buffer operativo para replay/inspección.  Trabajo futuro: definir
  política de retención del DLQ, revisión periódica, mecanismo de replay y — si se
  purga — acta de eliminación (Ley 594 exige acta para eliminación de documentos de
  archivo).

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
