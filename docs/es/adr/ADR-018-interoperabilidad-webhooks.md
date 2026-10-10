# ADR-018: Interoperabilidad saliente por webhooks firmados

**Estado**: Aceptado
**Fecha**: 2026-06-22
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

E11 (interoperabilidad) debe permitir que sistemas externos —portales, ERPs, otros SGD, operadores postales (E20)— reaccionen a lo que ocurre en OrpycaMCP (radicado creado, trámite asignado/vencido, expediente cerrado/transferido…) sin acoplarse al bus interno (Redis Streams) ni sondear la API. El SGDEA ya emite un **sobre de evento canónico** (`orpycamcp.*.events`, ADR-010) que notification-service consume.

La pregunta es cómo exponer esos eventos al exterior de forma segura, multi-tenant y desacoplada.

## Decisión

**Entregar los eventos de dominio a sistemas externos mediante webhooks salientes HTTP, firmados con HMAC-SHA256, suscritos por tenant, construidos sobre el bus de eventos existente.**

- **Suscripciones por tenant** — tabla `webhook_subscription` en `tenant_{slug}`: `url`, `event_types` (lista; vacía = todos), `secret` (clave HMAC), `active`. CRUD bajo `/api/v1/webhooks` (notification-service, que ya consume el bus).
- **Entrega** — al consumir un evento, notification-service hace `POST` del **sobre canónico** (event_type, tenant_slug, payload, ts) a cada suscripción activa cuyo `event_types` coincida, con cabecera `X-OrpycaMCP-Signature: sha256=<hmac(secret, body)>` para que el receptor verifique autenticidad e integridad.
- **Desacople y resiliencia** — la entrega es **best-effort con reintentos** y **nunca bloquea** el procesamiento del evento (un webhook caído no afecta al SGDEA). El bus interno sigue siendo la fuente de verdad.
- **Propietario** — notification-service (es el servicio de entrega saliente: ya consume los streams y tiene reintentos). No se crea un servicio nuevo.
- **Importación / pull** (entrada de datos externos, exportaciones masivas FUID/índice) se abordan como incrementos separados de E11; este ADR fija el mecanismo **saliente** event-driven.

## Consecuencias

**Positivas:**
- Integración externa **desacoplada**: los terceros se suscriben a eventos; no acceden al bus interno ni sondean.
- **Seguridad**: la firma HMAC permite al receptor verificar origen e integridad sin exponer credenciales; el `secret` es por suscripción.
- **Multi-tenant nativo**: las suscripciones viven por `tenant_{slug}` (ADR-002); un tenant no ve ni recibe los eventos de otro.
- Reutiliza el sobre canónico (ADR-010) y la infraestructura de reintentos de notification-service; cero servicios nuevos.
- Habilita E20 (operadores postales) y futuras integraciones como suscriptores más, sin cambiar el núcleo.

**Negativas / límites:**
- Entrega **at-least-once** best-effort: el receptor debe ser idempotente (se incluye un id de evento). No hay garantía de orden estricto entre webhooks.
- Webhooks salientes implican una superficie SSRF (URLs arbitrarias). **El SSRF está implementado** (`notification-service/app/core/webhook_security.py`, `validate_webhook_target`): se valida al crear la suscripción (esquema, host literal, IP literal; sin DNS) y **en cada intento de entrega** con resolución DNS real (`webhook_service._deliver_one`); se rechazan los pares de la red Docker y los rangos privados de `orpycamcp_common.red_interna`; `httpx` no sigue redirecciones; el kill-switch `webhook_allow_private_targets` existe solo para despliegues que lo necesitan a conciencia. El TOCTOU de DNS está cerrado (AREA-10-11): se conecta a la IP ya validada, con `Host` y SNI originales.
- **Constancia de entregas (B4)**: cada intento de `_deliver_one` deja dos filas en la tabla tenant append-only `webhook_entregas` (migración `012`): `envio` ANTES del POST —si no puede escribirse, no se envía (fail-closed)— y `resultado` después (`entregado`, `http_error`, `error_red`, `destino_rechazado`, `secreto_inutilizable`). Sin FK a la suscripción (borrarla no borra la constancia); solo el sha256 del cuerpo y el destino reducido a `scheme://host:port` (+ `url_sha256`), porque el sobre puede llevar datos personales y Slack/Teams ponen el token en la ruta; se lee con `GET /api/v1/webhooks/{id}/entregas` (`USUA_PERM_ADMIN`). **Sigue pendiente, declarado**: la **retención y purga** de esa tabla (al ser append-only exigirá un rol de operaciones y su propio asiento), la cola de reintentos persistente (DLQ) y un panel; el MVP reintenta en línea (3 intentos). Un `USUA_PERM_ADMIN` que suscriba una URL externa sin `event_types` hace salir todo el contenido no clasificado (lo clasificado se recorta en `recortar_si_clasificado`); ahora queda constancia de cada salida.

## Alternativas consideradas

- **Exponer Redis Streams directamente al exterior**: descartado; acopla a los terceros al transporte interno y rompe el aislamiento.
- **Solo polling (los terceros consultan la API)**: descartado como única vía; no es reactivo y carga la API; se mantiene la API REST como complemento.
- **Bus externo / broker dedicado (Kafka, RabbitMQ) para terceros**: sobredimensionado para la escala objetivo; webhooks HTTP firmados son el estándar de facto y suficientes (coherente con [ADR-014](ADR-014-busqueda-postgresql-fts.md): empezar simple, puerta de salida abierta).

## Relacionados

- [ADR-010](ADR-010-libreria-compartida.md) — el sobre canónico de evento es lo que se entrega.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — suscripciones por `tenant_{slug}`.
- E20 (operadores postales) será un consumidor de webhooks / integración saliente sobre este mecanismo.
