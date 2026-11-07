# ADR-049: Los eventos y las colas viven en PostgreSQL; se retira Redis

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-009](ADR-009-historial-flujos-eventos.md) (historial como eventos append-only; **se conserva**), [ADR-010](ADR-010-libreria-compartida.md) (librería compartida; **se conserva el sobre firmado**), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (reclamación de PEL y dead-letter; **se reimplementa sobre SQL**), [ADR-041](ADR-041-metricas-operativas-sin-tenant-y-migraciones-con-cerrojo.md), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md)

---

## Contexto

Redis cumple nueve funciones distintas, pero en la topología que se envía (el monolito, ADR-047) **la mitad ya no se usa**:

| Uso | Clave / patrón | ¿Vivo en el proceso único? |
|---|---|---|
| Límite de tasa por IP, usuario, entidad y global | `ratelimit:*` | **No.** El gateway es `split` y sus middleware no están montados en `app` (`rg RateLimit services/docuboto/` → 0). |
| Caché de `/auth/validate` sellada con MAC | `authvalidate:*` | **No.** El monolito valida en proceso contra `app.state.auth_service`. |
| Nonce de identidad firmada de un solo uso | `orpycamcp:identidad:nonce:v3:*` | **No.** `IdentidadFirmadaMiddleware` no está montada y un guardián prohíbe montarla. |
| Antirreplay de ALTCHA | `altcha:usado:{tenant}:{challenge}` | **Sí**, y falla cerrado con 503 si falta. |
| Streams de eventos (3) con 4 grupos consumidores | `orpycamcp.{document,workflow,signature}.events` | **Sí**, y es el bus entre procesos. |
| Hash de último error por grupo | `orpycamcp:{group}:lasterr` | Sí, canal lateral del dead-letter. |
| Dead-letter de streams | `orpycamcp.{svc}.deadletter` | Sí. |
| Cola de trabajo | — | **No está en Redis.** Es `trabajo_fuera` en PostgreSQL con `FOR UPDATE SKIP LOCKED`. |
| Cerrojos de trabajos periódicos | — | **No está en Redis.** Es `pg_try_advisory_lock`. |

Ya hay precedente explícito de mover una pieza de Redis a PostgreSQL: el nonce del callback PPI *«Replace[d] Redis orpycamcp:ppi:nonce:*»* (`knowledge-service/migrations/tenant/021_ppi_nonces.sql:12`), y un guardián (`shared/tests/test_un_solo_proceso.py:153`) impide que la clave vuelva.

Con el proceso único desaparecen los tres usos que sí dependían de tener varios servicios: el límite de tasa por usuario y entidad del gateway, la caché de validación y el nonce de identidad. Quedan tres, y los tres tienen una implementación razonable en PostgreSQL.

La carga que hay que soportar es la de una alcaldía: 3.000–12.000 radicados al año es del orden de un evento cada pocos minutos en el pico, no de miles por segundo.

## Decisión

### 1. Un bus de eventos en PostgreSQL, con el mismo sobre

Se conserva intacto el contrato de `orpycamcp_common.events`: el sobre con `event_id`, `event_type`, `version`, `tenant_slug`, `occurred_at`, `actor`, `correlation_id`, `dedup_key`, `payload` y `firma` HMAC. **Cambia el transporte, no la forma.** Los consumidores no se tocan; solo cambia de dónde leen.

```sql
CREATE TABLE eventos (
    seq          BIGSERIAL PRIMARY KEY,
    stream       TEXT        NOT NULL,
    event_id     UUID        NOT NULL,
    sobre        JSONB       NOT NULL,   -- el sobre firmado, byte a byte
    publicado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON eventos (stream, seq);

CREATE TABLE evento_entregas (            -- equivale al PEL de Redis
    group_name    TEXT        NOT NULL,
    event_id      UUID        NOT NULL,
    reclamado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    entregas      SMALLINT    NOT NULL DEFAULT 1,
    PRIMARY KEY (group_name, event_id)
);
```

- **Publicar** es un `INSERT` en la misma transacción que el cambio de dominio. Es *mejor* que el outbox actual: hoy hay `eventos_salientes` que se reenvía a Redis y puede quedarse pendiente; con la tabla, el evento y el hecho son atómicamente el mismo commit y desaparece el barrido de reenvío.
- **Consumir** es una consulta por cursor: cada grupo guarda su `seq` y avanza. La entrega es *at-least-once* con deduplicación por `(group_name, event_id)`, como hoy.
- **Latencia**: `LISTEN/NOTIFY` sobre un canal por stream despierta a los consumidores; el bucle de respaldo consulta cada 5 s por si se perdió una notificación (que es lo que hace PostgreSQL si el cliente estaba desconectado). No se usa el payload de `NOTIFY` para el evento — lleva solo el `stream` — para no chocar con el límite de 8.000 bytes.
- **Reclamación**: un `UPDATE … FROM` sobre `evento_entregas` donde `reclamado_en < now() - interval '60 seconds'`, con `SKIP LOCKED`, devuelve lo atascado. Equivale a `XAUTOCLAIM`, sin las sutilezas de los IDs de mensaje.
- **Dead-letter**: se conserva la tabla `evento_dead_letter` que ya existe y que ya es el destino final. El stream de dead-letter de Redis desaparece; el *buffer* que hoy se escribe en Redis era solo para no perder el evento entre el fallo y el `INSERT`, y con una tabla ya no hace falta.

### 2. Los tres usos restantes

| Uso | Reemplazo |
|---|---|
| Antirreplay de ALTCHA | Tabla `altcha_usados (tenant, challenge, expira_en)` con `INSERT … ON CONFLICT DO NOTHING RETURNING`, y purga de expirados en el mismo statement. Idéntico al patrón de `ppi_nonces`. Deja de fallar cerrado con 503: falla cerrado con 503 *solo si la base no responde*, que es el mismo fallo que cualquier otro endpoint. |
| Hash de último error | Una tabla `evento_ultimo_error (group_name, event_id, mensaje, ocurrido_en)`, con la misma purga a 24 h. |
| Cerrojos y colas | Ya están en PostgreSQL. No cambia nada. |

### 3. Límite de tasa sin Redis

El límite de tasa del gateway desaparece con el gateway. En su lugar:

- **`edge`** aplica `limit_req` de nginx por IP sobre las rutas públicas (radicación ciudadana, consulta, OIDC). Es la primera línea y no cuesta código.
- **La app** aplica un contador por usuario en memoria del proceso (ventana fija de 60 s, `dict` con purga perezosa). Con un solo proceso, un contador en memoria **es** el contador correcto; la razón por la que estaba en Redis (varias réplicas) ya no existe.

Un guardián nuevo falla si aparece un `redis` en cualquier `requirements.txt` o un `REDIS_URL` en cualquier manifiesto.

### 4. Lo que se retira

| Pieza | Destino |
|---|---|
| Contenedor `redis` y su volumen | Fuera del manifiesto |
| `shared/orpycamcp_common/events.py` (769 líneas: envelope + grupos + reclamo + trim + supervisión) | Se **reduce**: sobre y firma se quedan (~200 líneas), transporte XADD/XREADGROUP/XACK/XCLAIM/XPENDING se va |
| `shared/deadletter.py`, `dead_letter_repository.py` ×3, `deadletter_retention.py` ×3, `routers/admin_deadletter.py` ×3 (2.510 líneas) | Una implementación y una consola |
| `IDENTITY_SIGNING_SECRET` y la exigencia de `REDIS_URL` en `identidad.py:596-608` | Se borran |
| `REDIS_URL` en 12 servicios | Se borra |
| Retención por `XTRIM MINID` y las cifras de 30 / 90 / 7 días por servicio | Una política única en la tabla `eventos`, con purga por antigüedad |

## Alternativas consideradas

- **Mantener Redis solo para eventos.** Descartado: para conservar un contenedor hay que mantener el cliente, los grupos, el reclamo y los manifiestos, y la ventaja real (10.000 msg/s) es ~10.000 veces la carga del cliente objetivo.
- **`pgmq` o `pg_cron`.** Descartado por ahora: son extensiones que hay que instalar en la imagen de PostgreSQL y versionar en el respaldo; la tabla con `SKIP LOCKED` que ya existe en el proyecto demuestra que no hace falta. Si algún día el volumen lo pide, la tabla es compatible con migrar a `pgmq` sin tocar consumidores.
- **Un `NOTIFY` con el payload completo.** Descartado: el límite de 8.000 bytes por notificación hace que funcione hasta que un evento grande lo rompa, y falla de forma silenciosa donde más duele.
- **Cola en un servicio externo (SQS, Pub/Sub).** Descartado: añade un proveedor, credenciales y residencia de datos para una cola interna.
- **Streams en PostgreSQL con `LISTEN/NOTIFY` solamente, sin tabla.** Descartado: `NOTIFY` no persiste; un consumidor caído pierde el evento y no hay forma de replay. La tabla es el registro, la notificación es solo el despertador.

## Consecuencias

- **Un contenedor menos y una dependencia de infraestructura menos.** El respaldo ya no tiene que preocuparse de un almacén con `appendonly` y `noeviction`.
- **El bus pasa a estar respaldado.** Un `pg_dump` incluye los eventos pendientes. Hoy, un respaldo de PostgreSQL no incluye el stream de Redis: los eventos en vuelo se pierden si el respaldo y el `dump` no coinciden.
- **Publicar deja de ser *best-effort*.** El `INSERT` en `eventos` va en la misma transacción que el cambio; hoy el `XADD` es posterior al commit y hay un barrido que reenvía. Se elimina una clase entera de fallo.
- **El rendimiento baja dos órdenes de magnitud y sobra margen.** De ~10.000 msg/s a ~1.000 msg/s en el mejor caso. Con la carga del cliente, el bus estará ocioso.
- **La purga hay que hacerla.** Un `DELETE` periódico por antigüedad con índice por `publicado_en`; sin él la tabla crece para siempre.
- **Se pierde el `XPENDING`/`XINFO` como herramienta de diagnóstico.** Se sustituye por dos consultas SQL sobre `evento_entregas`, que además se pueden poner en un panel.
- **La ventana de la deduplicación se ata a la purga.** Hay que purgar entregas y eventos con la misma política o un evento purgado puede reprocesarse; se declara en el ADR del esquema (ADR-053).
