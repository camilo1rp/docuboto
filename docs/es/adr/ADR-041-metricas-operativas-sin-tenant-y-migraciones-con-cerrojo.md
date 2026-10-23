# ADR-041: Métricas operativas sin tenant (`/metrics` propio) y migraciones con un cerrojo único que existe de verdad

**Estado**: Aceptado
**Fecha**: 2026-10-21
**Autores**: Giampiero (mantenedor principal); tanda C-8 del triage (C18, C19)
**Relacionados**: [ADR-010](ADR-010-libreria-compartida.md) (librería compartida y auditoría inmutable), [ADR-012](ADR-012-migraciones-por-tenant.md) (migraciones), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (dead-letter), [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM), [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (estado de la entidad)

---

## Contexto

**C18 — nadie veía lo que se acumula en silencio.** Una dead-letter con eventos aparcados, un artefacto WORM cuyo
reintento se agotó o una cadena de auditoría rota no tenían ninguna superficie de supervisión: el estado vivía en
tablas por tenant y solo lo veía quien abría la pantalla de administración de esa entidad.

**C19 — migrar al arrancar no era simétrico, y el texto afirmaba un cerrojo que no existía.** Producción ya tenía un
`migrate-<servicio>` por servicio con `service_completed_successfully`; desarrollo no, y las migraciones se
aplicaban a mano. Peor: `infra/docker-compose.prod.yml` y `docs/es/deployment.md` decían que el runner «serializa
por `pg_advisory_lock`». `grep advisory` sobre los nueve `migrations/runner.py` y sobre
`orpycamcp_common/migrations.py` daba **cero**. Nueve migradores en paralelo se pisaban en
`CREATE TABLE IF NOT EXISTS public.schema_migrations`, en la `evento_dead_letter` que crean cuatro servicios en el
mismo schema y en los asientos-ancla (comprobar y luego escribir). Y las migraciones **globales** no iban en
transacción: un fallo a mitad dejaba DDL aplicado sin asiento en el libro.

## Decisión

### C19 — Un cerrojo global, real, y las globales en transacción

`orpycamcp_common.migrations.cerrojo(conn, timeout_s=…)`: `pg_try_advisory_lock` sobre **una** constante fija
(`CERROJO_MIGRACIONES`, los ocho bytes ASCII de «ORPYCAM1», legible en `pg_locks`), con reintento cada 0,5 s hasta
`MIGRATIONS_LOCK_TIMEOUT_S` (600 por defecto). Vencido el plazo: `RuntimeError` legible («otra migración tiene el
cerrojo desde hace N s (pid …)»), el proceso sale con código 1, no se cumple `service_completed_successfully` y el
servicio **no arranca** (fail-closed). Se suelta en `finally`; es de sesión, así que sobrevive a las transacciones
por archivo y el servidor lo libera si la conexión muere.

- **Uno solo para todos, no uno por servicio**: comparten el libro, `public` y tablas del schema tenant
  (`evento_dead_letter`). Coste: los nueve migradores se serializan (segundos cuando todo es *skip*).
- Los nueve `runner.py` envuelven **todo** (libro, importación de libros antiguos, globales y tenant) y
  `scripts/init_tenant.py` toma el mismo cerrojo: provisionar una entidad mientras arranca un migrador ya no los deja
  pisarse.
- `_apply_global` pasa a `async with conn.transaction()` por archivo, como ya hacía `_apply_tenant`. Verificado:
  ningún `.sql` global usa `CONCURRENTLY`, `ADD VALUE`, `VACUUM` ni `BEGIN;`; un guardián lo vigila.
- **Desarrollo** (`docker-compose.yml`): los nueve `migrate-<svc>` (misma imagen, `restart: "no"`, sin `ports`,
  conexión del propietario) y `depends_on … service_completed_successfully`. La imagen la construye el servicio
  (`image: orpycamcp/<svc>-service:dev` explícita) y el migrador la reutiliza por nombre con `pull_policy: never`,
  sin `build:` propio para no construirla dos veces. Los servicios **conservan** `MIGRATIONS_DATABASE_URL`:
  `scripts/init-tenant.sh` hace `docker compose exec` y los conftest de integración la leen.
- **`--no-deps` sigue permitido y salta el migrador a propósito**: recrear con la misma imagen no tiene hueco (el
  migrador ya la aplicó); con imagen nueva la receta es `build && run --rm --no-deps migrate-<svc> && up -d
  --no-build --no-deps <svc>` (si el migrador falla, el `&&` no recrea). No se impide; se documenta.

### C18 — `/metrics` mínimo, propio y sin identidad

**Exportador propio** (`orpycamcp_common.metricas`, texto 0.0.4) frente a `prometheus_client`: todo son *gauges* de
estado leídos de la base con caché, no contadores en proceso; el registro por defecto de la librería empuja a emitir
0 donde queremos **ausencia**; y añadirla serían seis dependencias nuevas (txt + lock) para nueve líneas. Si un día
hacen falta histogramas de latencia, se revisa esta decisión.

**Frontera por admisión.** `FAMILIAS` es el registro único de las nueve familias; lo que no está no se puede emitir y
cada etiqueta tiene un conjunto cerrado de valores (`signal` ∈ {dlq, worm, audit_chain}; `artifact` ∈ 7; `phase` ∈
{reconcile, renewal, packaging}). **Ninguna etiqueta de tenant, slug, usuario, radicado ni id.** Los números son
agregados sobre todos los tenants (incluidos los suspendidos: su DLQ y su WORM siguen existiendo; solo se omite una
entidad con aprovisionamiento pendiente).

| Familia | Servicio | Qué dice |
|---|---|---|
| `orpycamcp_signal_measured{signal}` | los seis | 1 = la última medición fue **completa**; 0 = **no verificado** |
| `orpycamcp_signal_last_success_timestamp_seconds{signal}` | los seis | epoch de la última medición completa (ausente si nunca) |
| `orpycamcp_dlq_pending_events` | workflow, signature, notification, knowledge | pendientes del `origin_group` propio |
| `orpycamcp_dlq_oldest_pending_timestamp_seconds` | idem | el más antiguo (ausente si no hay pendientes) |
| `orpycamcp_worm_exhausted_artifacts{artifact,phase}` | archive | artefactos con el reintento WORM agotado |
| `orpycamcp_audit_chain_breaks` / `_declared_breaks` / `_verification_ok` / `_last_verification_timestamp_seconds` | auth | última corrida **completa** de `verify_audit_chains` |

**Ausencia ≠ 0 (regla única).** Si la medición de una señal falla en **cualquier** tenant (tabla ausente, timeout,
error SQL, valor fuera del registro) se emite `signal_measured{signal} 0`, se conserva el `last_success` del último
éxito y **no se emite ninguna serie de valor** de esa señal. Nunca una suma parcial (subestimaría). Cuando sí se midió,
cada combinación se emite también con 0: ahí el 0 es verdad. Un fallo también se cachea durante el TTL para no
martillar una base que ya sufre.

**Medición perezosa con TTL** (30 s, `METRICS_TTL_SECONDS`), una sola medición en vuelo (`asyncio.Lock`), plazo de
10 s y `SET LOCAL statement_timeout` en una transacción de solo lectura. Sin tarea de fondo: nadie scrapea ⇒ cero
carga, y no se tocan los `lifespan`.

**`audit_chain`: el valor del último job, nunca `verify_chain` en el scrape.** Alternativas: (A) tabla
`public.audit_chain_runs` que escribe el CLI; (B) un asiento `audit.chain.verified` en `audit_log`; (C) un job
periódico en el proceso de auth. (B) **cambia el extremo** que `backup.sh` ancla en la misma corrida; (C) corre
`verify_chain` (el registro entero) dentro del proceso que valida tokens, se duplica por réplica y tras cada
reinicio dejaría horas «no verificado». **Se elige A** (migración global `009_audit_chain_runs.sql`, con
`REVOKE UPDATE, DELETE, TRUNCATE` para el rol de la aplicación: solo se añade). El CLI anota **una** fila al final,
best-effort (si falla, aviso a stderr y el código de salida **no cambia**: un respaldo no se rompe por una métrica;
la ausencia la delata la alarma de antigüedad). Una corrida con `--slug` o `--excluir-prefijo` se anota `parcial` y
**nunca cuenta** como verificación. La antigüedad la juzga la alarma, no el exportador.

**Declarado (auditoría de cierre del lote C): `audit_chain_runs` es monitoreo, no prueba.** El `REVOKE UPDATE, DELETE, TRUNCATE`
impide reescribir o borrar lo anotado, pero el rol de la aplicación (`orpycamcp_app`) conserva `INSERT`: un proceso con
ese rol podría anotar una corrida «verificada» que no ocurrió. La métrica `audit_chain` dice **cuándo anotó el último
job**, no que la cadena sea íntegra; la integridad se demuestra volviendo a correr `verify_chain` (el CLI del respaldo,
que ancla el extremo en la misma corrida) y comparando con el ancla. Cerrarlo del todo exige un rol propio del job con
`INSERT` y la aplicación sin él: queda como mejora de despliegue, no se simula aquí. Por la misma razón la alarma de
antigüedad no debe leerse como certificado de integridad.

**Exposición.** `GET /metrics` en el puerto propio de auth, archive, workflow, notification, signature y knowledge
(`include_in_schema=False`); no lo exponen gateway, tenant, document, storage, mcp ni whisper. Tres barreras:
(1) el gateway solo reenvía `/api/v1/`; (2) toda petición con `X-Forwarded-For`, `Forwarded` o `X-Real-IP` recibe
404 (Prometheus no las manda, nginx y el gateway sí: defensa en profundidad, **no** sustituye a la red); (3) nginx
de producción responde `return 404` a `/metrics` y `/metrics/` en el host de la API. En producción los servicios no
publican `ports:`.

## Consecuencias

- Una alarma de `orpycamcp_signal_measured == 0` distingue «no sé» de «no hay problema»; sin ella, un 0 inventado
  habría tranquilizado exactamente cuando la medición estaba rota.
- En desarrollo `audit_chain` saldrá **no verificado** o con roturas: los 139+ asientos `itest-*` que dejaron las
  pruebas de integración no se pueden borrar (`audit_log` solo admite anexar) y `--excluir-prefijo` es, por
  construcción, una corrida parcial. Es lo honesto; no se esconde.
- Con réplicas, cada una reporta el mismo estado de la base: agregar con `max()`, no con `sum()`.
- El tamaño del `UNION ALL` crece con el número de tenants (una rama por entidad); con cientos de entidades conviene
  paginar por lotes de schemas. Hoy no aplica.
- Si una migración dependiera del orden respecto de otro servicio, el cerrojo global no lo resuelve (el orden es
  arbitrario, igual que antes). No se detectó ninguna; queda declarado.
- Los `reconcile_agotado` del **sellado XAdES** y `fixity_premis_agotado` no son WORM y quedan fuera; ampliación
  natural con `phase=sealing`.
- Guardianes: `shared/tests/test_metricas.py`, `test_metricas_frontera.py`, `test_metricas_worm_censo.py`,
  `test_cerrojo_migraciones.py`, `test_migraciones_al_arrancar.py` (prod y dev) y la prueba de integración del
  cerrojo contra Postgres real.
