# ADR-035: Estado de la entidad — cumplimiento en el borde, defensa en cada puerta de servicio, lectura pública durante la suspensión

**Estado**: Aceptado
**Fecha**: 2026-10-16
**Autores**: Giampiero (mantenedor principal); triage B9
**Relacionados**: Ley 527/1999 art. 12 num. 1; Ley 594/2000 arts. 14 y 27; Ley 1712/2014 arts. 9-11; Acuerdo AGN 004/2019 (publicidad de la eliminación); [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) y ADR-012 (schema por tenant, `search_path`), [ADR-010](ADR-010-libreria-compartida.md) (librería compartida: auditoría y sobre de eventos), triage B7 (`get_optional_tenant_conn`)

---

## Contexto

`public.tenants` tiene una columna `status` (`active | inactive | suspended`) y otra `provisioning_status`
(`pending | active | provisioning_failed`). El triage de B7 declaró que **ningún servicio hacía cumplir un tenant `suspended`**.
Era parcialmente falso: document-service sí lo hacía (`403 tenant_inactivo` en su `get_tenant_conn`), sin ningún test. En los demás:

- los `get_tenant_conn` de auth, knowledge, notification, signature y tenant solo validaban la **forma** del slug;
- archive, workflow y storage no tienen `get_tenant_conn`: fijan el `search_path` en repositorios y en `core/clearance.py`
  (más de veinte puntos);
- `SET search_path` sobre un schema inexistente **no falla**, así que la primera consulta lanzaba `UndefinedTableError` y respondía
  `500` (auth solo lo había resuelto en `get_optional_tenant_conn`);
- las rutas públicas (`/api/v1/public/*`, sin JWT, con el slug en el path) usaban cada una su propio predicado de existencia, o
  ninguno: `to_regclass` en CMIS y OAI, `SELECT 1 FROM public.tenants` (que ignora `status`) en la eliminación pública de archive.

Suspender una entidad era un interruptor que no apagaba nada: el schema seguía ahí y las credenciales seguían siendo válidas.

## Decisión

### 1. Un solo veredicto (`orpycamcp_common.tenant`)

`estado_entidad(conn, slug)` hace **una** consulta a `public.tenants` (más `to_regnamespace` del schema) y devuelve
`OPERATIVA | INACTIVA | NO_PROVISIONADA`. No captura excepciones: quien llama decide (HTTP: `503`, fail-closed; job: saltar la
vuelta). Un slug mal formado o una entidad inexistente cuentan como `INACTIVA`, el mismo veredicto que una suspendida: no es un
oráculo de existencia. `to_regnamespace` hace que una fila `active/active` sin schema (un alta a medias) sea `NO_PROVISIONADA`
en vez de un `500`. `veredicto_entidad` lo envuelve con el cuerpo de error común.

### 2. Punto de cumplimiento: el borde (opción C) y profundidad en cada `get_tenant_conn` (opción D)

Alternativas consideradas: (A) el gateway lee `public.tenants` con un pool propio — pone credenciales de base en el borde, que hoy
no las tiene; (B) el gateway llama a tenant-service con una caché propia — un salto síncrono y una segunda caché; (D) solo defensa
por servicio — archive, workflow y storage no tienen dependencia común.

Elegida: **C + D**.

- **C**: `GET /auth/validate` devuelve `tenant_estado`; el api-gateway lo hace cumplir al principio de `_continuar_con_identidad`
  (el camino con caché y el camino sin ella pasan por el mismo sitio) y lo lleva en la caché de validación existente (MAC, TTL de
  15 s). Cubre los doce servicios y el mcp-server porque todo pasa por el gateway. **Coste declarado: la suspensión tarda hasta
  `AUTH_CACHE_TTL` (15 s) en surtir efecto**, y la reactivación otro tanto. Un estado `desconocido` (la base de auth-service no
  respondió) **no se cachea**, para que un fallo transitorio no bloquee a la entidad todo el TTL. Un campo ausente (un auth-service
  anterior) se trata como `503 tenant_estado_no_disponible`: fail-closed, no abre la puerta.
- **D**: los seis `get_tenant_conn` que existen (auth, document, knowledge, notification, signature, tenant) llaman a
  `veredicto_entidad` **antes** del `SET search_path`; en document sustituye a `_tenant_activo`. archive, workflow y storage
  no tienen `get_tenant_conn`: el punto común por el que pasa toda petición con tenant es `get_db_conn`
  (`core/database.py`), y llaman ahí a `veredicto_entidad` antes de ceder la conexión (B9-N3). Se eligió `get_db_conn` y
  no `require_permission` porque las lecturas de bandeja de workflow, entre otras, no piden permiso: con el gate solo en
  `require_permission`, `GET /workflows/inbox` seguía respondiendo 200 con la entidad suspendida (comprobado en vivo). Un guardián por forma en `shared/` falla si
  alguno de ellos deja de hacerlo o si aparece un generador nuevo sin declararlo.
- `/auth/validate` no aprovisiona (alta perezosa de `auth_users`) dentro de una entidad que no es operativa.

### 3. Rutas públicas: solo lectura durante la suspensión, 404 neutro para lo demás

Sin JWT, el slug viaja en el path. `atencion_publica` distingue tres casos:

| Entidad | Lecturas públicas | Escrituras públicas |
|---|---|---|
| **Operativa** (`active`/`active`, con schema) | se sirven | se sirven |
| **Suspendida** (`suspended`, aprovisionada y con schema) | **se sirven** | `503 canal_no_disponible` + `Retry-After` |
| Dada de baja (`inactive`), sin aprovisionar o inexistente | `404 not_found` neutro | `404 not_found` neutro |

- **Lecturas que siguen abiertas en suspensión**: `GET /public/{t}/verify/{code}`, la lista y el detalle de la
  eliminación pública de archive, el OAI de document y de archive y el CMIS de lectura.
- **Escrituras cerradas**: PQRS (`POST /public/{t}/pqrs`) y la ventanilla (`POST /public/{t}/ventanilla` y sus
  consultas previas `tipos` y `estado`, que solo existen para alimentar el formulario) responden
  `503 canal_no_disponible`, no «No encontrado»: quien radica merece saber que el canal está cerrado, no creer
  que se equivocó de dirección.
- **`GET /public/{t}/ventanilla/politica` es lectura** (re-auditoría B9 D): la política de tratamiento de datos
  (Ley 1581) es un aviso público que no recibe datos del ciudadano; se sirve con la entidad operativa o suspendida
  (`get_public_tenant_conn`). Lo cerrado es *radicar*, no *leer el aviso*. Se lee **directo de `sgd_param_admin`** del schema de la entidad (la misma tabla y extracción que usa tenant-service) y no por tenant-service, que veta a la entidad suspendida en su `get_tenant_conn`: comprobado en vivo, pasar por él devolvía `503 politica_no_verificable` y cerraba de hecho el aviso. La radicación sigue leyendo la política por tenant-service (entidad operativa).
- **Callback postal**: la firma HMAC se verifica **antes** de decidir por el estado (el secreto vive en el
  schema de la entidad, que existe aunque esté suspendida). Firma válida con la entidad suspendida: `503` con
  `Retry-After` (el operador reintenta al reactivarse y no pierde el evento). Firma inválida, operador sin
  credencial o cuerpo ilegible: el mismo `404 not_found` neutro. El callback **no «oculta» el estado**: quien
  posee el secreto (el operador postal) lo averigua por el `503`, y es intencional; un
  anónimo no. Con la entidad operativa se conserva el `401 firma_invalida` de siempre.

Resumen de rutas públicas (`atencion_publica`):

| Ruta | Operativa | Suspendida | Inactiva / sin aprovisionar / inexistente |
|---|---|---|---|
| `GET /public/{t}/verify/{code}` | sirve | sirve | `404` neutro |
| `GET /public/{t}/ventanilla/politica` | sirve | **sirve** | `404` neutro |
| `GET /public/{t}/ventanilla/tipos`, `/estado` | sirve | `503 canal_no_disponible` | `404` neutro |
| `POST /public/{t}/pqrs`, `POST /public/{t}/ventanilla` | sirve | `503 canal_no_disponible` | `404` neutro |
| Eliminación pública, OAI, CMIS (lectura) | sirve | sirve | `404` / `badArgument` / `objectNotFound` |
| Callback postal (firma válida) | procesa | `503` + `Retry-After` | `404` neutro |

- La existencia de una entidad operativa o suspendida **no es un secreto** (AUD-SEG #5): es información pública de
  la institución. Lo que sigue siendo neutro es el contraste entre inexistente, dada de baja y sin aprovisionar.
- El `404 not_found` neutro es el mismo cuerpo exacto que `/verify` con un código inexistente; OAI responde el
  mismo `400 badArgument` que un slug mal formado y CMIS su `objectNotFound`. Si el registro no responde: `503`.

### 3 bis. Alternativa rechazada: bloqueo total de lo público durante la suspensión

La primera versión de este ADR cerraba **todo** lo público a una entidad suspendida («fail-closed total»). Se
rechaza, y el fundamento es normativo, no de comodidad:

- el plazo de oposición del inventario de eliminación documental (Acuerdo AGN 004/2019, publicidad previa;
  Ley 594/2000 arts. 14 y 27) **corría con el inventario invisible**, y el acta de eliminación sellada después
  certificaba una publicidad que en los hechos no fue efectiva;
- la verificación de un radicado es la prueba de integridad y autenticidad de la comunicación electrónica
  (Ley 527/1999 art. 12 num. 1): apagarla por una suspensión administrativa quita al ciudadano el medio de
  comprobar lo que ya recibió;
- el acceso a la información pública (Ley 1712/2014 arts. 9-11, vía OAI y CMIS) no se suspende porque la
  plataforma suspenda la operación de la entidad.

Suspender detiene la **operación** (todo lo autenticado y toda recepción nueva), no la **publicidad ya
debida**.

### 3 ter. El plazo de oposición no corre con la ventanilla cerrada (re-auditoría B9 A)

La ventanilla es el **único canal de oposición** a un lote de eliminación publicado, y durante la suspensión
responde `503`. El aval solo miraba `publicado_hasta < CURRENT_DATE`, de modo que un plazo podía vencer con el canal
cerrado y el acta certificar una publicidad sin ocasión real de oponerse.

- **Regla**: el plazo se cuenta en **días operativos**. `avalar_lote_eliminacion` reconstruye de `public.audit_log`
  los intervalos en que la entidad no fue operativa (asientos `tenant.updated` de tenant-service, con
  `payload.antes/despues.status`; cada cambio de `status` abre un tramo hasta el siguiente, no operativo si el estado
  resultante no es `active`; antes del primer asiento se supone operativa) y exige, **en el `WHERE` de la misma
  sentencia que la transición**, que los días operativos entre `publicado_at` y ayer alcancen la longitud del plazo
  original. Un día tocado por una no-operatividad, aunque sea parcialmente, no corre (conservador a propósito).
- **Sin endpoint nuevo ni extensión persistida**: el plazo efectivo se **deriva** de los asientos cada vez; no hay
  fecha que reeditar. Si el aval se intenta antes: `409 plazo_oposicion_interrumpido` con `dias_suspendidos` y
  `plazo_efectivo_hasta`.
- **El acta lo declara**: si el plazo se extendió, `publicidadPrevia` añade `plazoOposicionEfectivoHasta` y
  `diasPlazoInterrumpido`; sin interrupción el acta es idéntica a la de siempre.
- **Límite conocido**: los días futuros se suponen operativos al calcular el plazo efectivo (es una proyección; la
  guarda real solo mira días ya transcurridos).

### 4. Fuera a propósito

- **Consumidores de eventos** (`*/workers/document_consumer.py`, `notification/workers/stream_worker.py`) **no se bloquean**:
  procesan la convergencia interna de las copias derivadas (el `reclassified` que sube el nivel en `firma_solicitud` y en el ACL de
  knowledge). Mandarlos a la cola de fallidos con la entidad suspendida dejaría el ACL de RAG y el nivel de las cadenas **por
  debajo** del real tras reactivar: convertiría un cierre de acceso en una fuga.
- **Tareas en segundo plano** (`acquire_tenant`): las lanzó una petición ya autorizada.
- **Jobs**: ya filtran por `status='active' AND provisioning_status='active'`; `signing_key_maintenance` incluye `suspended` a
  propósito (custodia de claves y reloj de purga).

### 5. La identidad firmada caduca (B9-N3)

`archive`, `workflow` y `storage` no consultaban el estado y la identidad firmada del gateway **no llevaba
instante**: un juego de cabeceras capturado dentro de la red valía para siempre y, reproducido contra uno de ellos,
evadía una suspensión (AUD-SEG #2). Se cierra en dos capas:

- `X-Identity-Ts` (epoch, segundos) entra en `CAMPOS_FIRMADOS` y lo estampa `identidad.firmar`: cambiarlo rompe la
  firma. `identidad.verificar` rechaza, con el mismo mensaje que una firma mala, una identidad con más de
  `IDENTITY_MAX_AGE_SECONDS` (120 s) de antigüedad o más de `IDENTITY_MAX_SKEW_SECONDS` (30 s) en el futuro, y la
  que no trae instante entero.
- El veredicto de la entidad se consulta también en `get_db_conn` de archive, workflow y storage (ver D, arriba): solo
  cuando la petición trae un `X-Tenant-Slug` válido (las rutas públicas llevan el slug en el path, y un slug mal
  formado conserva el 400 de `tenant_schema`).

### 6. Sin token para una entidad que no se atiende (B9-N4)

`POST /api/v1/auth/token` y `/refresh` no entregan token si la entidad **del claim del token** (la que el gateway
verá, no la del cuerpo) no es operativa: `403 tenant_inactivo`, `503 tenant_no_provisionado` o `503
tenant_estado_no_disponible`, los mismos códigos del gateway. Un token sin `tenant_slug` (superadmin global) no se
comprueba. El mcp-server propaga esos códigos desde `/auth/me` en vez de plegarlos a `503 auth_service_error`.

**Defensa en profundidad (re-auditoría B9 G).** Keycloak ya emitió el par de tokens cuando se consulta el estado de
la entidad; rechazar sin más dejaba viva la sesión y su refresh token en el realm. Al rechazar el login o el refresh
por entidad no operativa, `AuthService` **revoca el refresh token recién emitido** por el endpoint de logout del
realm (best-effort: un fallo al revocar se registra y no sustituye al rechazo). No es la barrera real —esa es el
gateway, que veta cada petición por el slug del claim, y la vía PKCE del front no pasa por auth-service—: es una
capa más para que una sesión rechazada no quede abierta.

### 7. Deuda declarada (no se implementa en esta tanda)

- **Callback postal con la entidad suspendida (re-auditoría B9 B, Media)**: responde `503` con un `Retry-After`
  fijo. Si la suspensión dura más que la ventana de reintentos del operador postal, se pierde la prueba de entrega.
  Pendiente: aceptar la firma válida, **diferir** el evento (cola durable por entidad) y procesarlo al reactivarse.

## Consecuencias

- Una entidad suspendida recibe `403 tenant_inactivo` en toda la API autenticada y no recibe token nuevo; en las rutas públicas conserva las lecturas y sus escrituras responden `503 canal_no_disponible`.
- Un schema inexistente es `503 tenant_no_provisionado`, no un `500`.
- Los tests que dan por operativa a la entidad sin atravesar la dependencia real lo declaran (`patch` de `estado_entidad`); el
  veredicto lo cubren pruebas propias con una base real (`estado_entidad` sobre cinco filas) y con dobles por servicio.
- El frontend debe tratar un `403 tenant_inactivo` en `/auth/me` y en el login como «Entidad suspendida».
- La identidad firmada que viaja entre servicios se verifica con una ventana de 120 s: todo cliente interno debe
  firmar **al enviar** (`identidad.firmar` ya estampa el instante), no reutilizar cabeceras firmadas guardadas.
- **Riesgo conocido**: un token de superadmin que lleve el `tenant_slug` de la entidad suspendida no podrá reactivarla por el
  gateway; si el superadmin opera sin entidad (`tenant_slug` vacío), sí.
