# ADR-044: La identidad firmada es de un solo uso y el `search_path` es solo el tenant

**Estado**: Aceptado
**Fecha**: 2026-10-22
**Autores**: Giampiero (mantenedor principal); tanda C-7 (deuda B10 g, h)
**Relacionados**: [ADR-036](ADR-036-identidad-atada-a-metodo-y-ruta.md) (identidad atada a método y ruta; enmendado), [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) (schema por tenant; enmendado), [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (la identidad caduca a los 120 s), [ADR-003](ADR-003-asyncpg-sin-orm.md) (SQL crudo)

---

## Contexto

Dos garantías declaradas en B10 seguían abiertas:

- **(g)** Firma, método, ruta y ventana de 120 s dejaban una identidad capturada en la red interna **reproducible muchas veces** dentro de la ventana, con los permisos de esa persona.
- **(h)** El `search_path` de cada petición era `"tenant_x", public`. Si una tabla de tenant faltaba, la consulta se resolvía **en silencio** contra la homónima de `public`. No era teórico: la verificación halló **14 tablas de tenant dentro de `public`** (`causales`, `formas_envio`, `soportes`, `mensajes_rapidos`, los nueve `pinar_*` y `admin_audit`) con datos, creadas por migraciones de tenant que corrieron con un path cuyo primer schema no existía: el `CREATE TABLE` cae en el primero que sí existe.

## Decisión

### (g) Nonce de un solo uso

1. `VERSION_FIRMA = "v3"`. Nueva cabecera `X-Identity-Nonce` (128 bits, `secrets.token_urlsafe(16)`, 22 caracteres `[A-Za-z0-9_-]`) **dentro** de `CAMPOS_FIRMADOS`, al final. Una forma distinta es `IdentidadNoAutentica` (el mismo mensaje que una firma mala).
2. `firmar()` genera **siempre** uno nuevo y descarta cualquier `X-Identity-Nonce` entrante (sin distinguir mayúsculas). El llamante no puede aportar un nonce: todo reintento es una llamada nueva a `firmar`, es decir, una identidad nueva.
3. `verificar()` sigue síncrona y pura; ahora devuelve `IdentidadVerificada(tenant, nonce, caduca_en)` (o `None` sin secreto).
4. **Registro**: `IdentidadFirmadaMiddleware` reclama el nonce en Redis con `SET clave 1 NX EX ttl`, clave `orpycamcp:identidad:nonce:v3:{tenant}:{nonce}` (sin servicio: la misma identidad no vale en dos destinos). TTL = vida restante de la identidad en el reloj del verificador + el desfase tolerado (techo `max_age + 2·skew` = 180 s). Se reclama **solo después** de verificar la firma: sin el secreto, nadie llena Redis ni quema nonces ajenos.
5. **Respuestas**: reutilizada, `401 identidad_no_autentica` (mismo cuerpo) y `identidad_reutilizada` en el registro (con `sha256(nonce)[:12]`, nunca el nonce); registro no disponible, **`503 identidad_no_verificable`** con `Retry-After: 1`. Cualquier excepción del cliente Redis es `RegistroNoDisponible`, nunca un booleano.
6. **Alcance del fail-closed**: solo peticiones **con identidad de persona** (`X-User-Id`). La ventanilla pública, `/health`, los webhooks públicos y las llamadas internas sin `X-User-Id` siguen vivos con Redis caído: ningún plazo legal queda sin canal.
7. **Arranque**: con `IDENTITY_SIGNING_SECRET` y sin `REDIS_URL` el middleware responde `lifespan.startup.failed`: un fallo determinista de despliegue, no un 503 por petición. Un Redis que no responde al arrancar **no** impide arrancar.
8. **Servicio a servicio**: siguen **re-firmando** en cada llamada (gateway→archive consume n1, archive→signature n2, signature→storage n3). Se descartó reenviar la firma (está atada al método y la ruta del primer salto y su nonce ya estaría consumido) y exentar las llamadas con `X-Internal-Token` (abriría justo el canal que se quiere cerrar). Guardián: en un bucle, el `firmar` va en el mismo cuerpo que la llamada HTTP, y solo el middleware compartido lee `X-Identity-*`.
9. **storage-service pasa a depender de Redis** (dev y prod): `REDIS_URL` + `depends_on: redis` y `redis` en su `requirements`. Es la única dependencia nueva.

### (h) `search_path` = solo el tenant

1. `SET search_path = "tenant_x"` en los nueve servicios, los runners y los scripts. `SET search_path = public` a secas (conexión sin tenant) se queda. No se toca el rol `orpycamcp_app` ni la configuración de Postgres.
2. **Todo lo global se califica**: `public.audit_log`, `public.tenants`, `public.schema_migrations`, `public.divipola_*`... Las extensiones **viven en `public`** y se nombran calificadas: `$1::public.vector`, `OPERATOR(public.<=>)`, `public.gin_trgm_ops`, `public.vector_cosine_ops`, `public.unaccent`, `'public.spanish_unaccent'::regconfig`; `CREATE EXTENSION ... WITH SCHEMA public`.
3. **Migraciones**: tenant-service global `005` retira las 14 tablas huérfanas de `public` (y `pinar_seguimiento_append_only`); tenant-service tenant `016` re-fija con `ALTER FUNCTION ... SET search_path` toda función del schema cuyo `proconfig` nombre `public` (eran 9 en `tenant_demo`; la lista creció desde el diseño, por eso la migración es genérica); workflow tenant `021` retira `_b5_jsonb_o_null` (ayudante del backfill 020 sin otros usos); document global `005` y knowledge global `002` comprueban, fail-closed, que `pg_trgm`/`unaccent` y `vector` siguen en `public`, con un mensaje que remite a este ADR.
4. **Guardián estático** `shared/tests/test_el_search_path_es_solo_el_tenant.py`: ningún `search_path` lleva coma (los tests pueden llevar dos tenants a propósito, nunca `public`); los objetos globales, **derivados** de `migrations/global/`, no se nombran sin `public.`; las extensiones se nombran calificadas; `CREATE EXTENSION` dice `WITH SCHEMA public`; con controles positivos sobre fuentes sintéticas. Una comprobación de integración contra Postgres real cruza la lista de extensiones con `pg_depend`.
5. Efecto buscado: la ausencia de una tabla de tenant pasa de «lee la de `public`» a `UndefinedTable` (503); un runner contra un schema inexistente falla con `3F000` en vez de crear la tabla en `public`.

## Alternativas descartadas

| (g) | Motivo |
|---|---|
| Registro en memoria por proceso | Correcto con una réplica, pero la garantía se rompe en silencio al escalar (2 réplicas o `--workers>1`): justo el olvido que no se ve |
| Época + nonce (script Lua) | Cierra el residuo de un vaciado de Redis, que en producción (AOF `everysec`) mide ~1 s; no compensa el costo |
| Exentar llamadas con token interno | Abriría el canal que se quiere cerrar y crearía dos regímenes |

| (h) | Motivo |
|---|---|
| Schema `extensions` y path `"tenant", extensions` | Exige `ALTER EXTENSION ... SET SCHEMA` como superusuario en cada despliegue y descalificar lo que ya dice `public.unaccent`/`public.digest`; la configuración de Postgres queda fuera del alcance |
| Mantener `, public` y vaciar `public` de tablas | Cierra el fallback solo mientras nadie cree otra tabla allí; no hay invariante verificable |
| Helper único `fijar_search_path()` en shared | No añade garantía que el guardián no dé y multiplica el diff; queda como mejora si el guardián vuelve a hallar divergencias |

## Residuos declarados

- La carrera: quien captura una identidad y llega **primero** gana; la legítima recibe 401. El nonce protege contra la reproducción de una captura, no contra un atacante que gana la carrera.
- La firma sigue sin cubrir la query ni el cuerpo (residuo de ADR-036).
- Un **servicio comprometido** con el secreto maestro puede emitir cualquier identidad con nonces nuevos.
- Un reinicio de Redis pierde los nonces vivos: ≤ 1 s con AOF en producción; en desarrollo (sin `appendonly`) hasta 180 s.
- Redis no debe tener política de desalojo (`allkeys-*`): con ella podría desalojar un nonce y reabrir la reproducción. Hoy es `noeviction` por defecto.
- (h) acopla el sistema a que las extensiones vivan en `public`; se hace explícito en las migraciones de comprobación.

## Consecuencias

- **Corte atómico**: el formato v2 → v3 cambia la base del HMAC, sin rama de compatibilidad. El api-gateway y los nueve servicios se reconstruyen y arrancan **a la vez**; si conviven versiones, toda petición autenticada responde 401. Despliegue: `docker compose up -d --build api-gateway auth-service tenant-service document-service archive-service storage-service workflow-service notification-service signature-service knowledge-service`.
- Redis pasa a ser dependencia **dura** de toda petición con identidad de persona (el gateway sigue siendo fail-open en su limitador; este registro es el primer consumidor fail-closed en el camino síncrono). Una indisponibilidad de Redis es `503 identidad_no_verificable`, observable, no un 401 mudo.
- (h) **no requiere corte atómico**: el SQL calificado funciona con el path viejo y con el nuevo. Orden seguro: migraciones, luego servicios.
