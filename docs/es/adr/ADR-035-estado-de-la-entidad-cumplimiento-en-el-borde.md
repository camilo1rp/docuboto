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

### 7. Callback postal con la entidad suspendida: se difiere (B10)

Antes (deuda declarada en B9, Media): firma válida con la entidad suspendida => `503` con un `Retry-After` fijo. Si
la suspensión duraba más que la ventana de reintentos del operador postal, se perdía el aviso del operador (p. ej.
la devolución que abre un plazo). **Decisión (B10)**: la firma se verifica igual y antes de cualquier efecto, y

- cuerpo ilegible, operador sin credencial o firma inválida => el **404 neutro** de siempre (un anónimo no averigua el estado);
- firma válida con esquema inválido => `422 payload_invalido`; estado no mapeable => `422 estado_operador_no_mapeable`; guía
  desconocida => `404 envio_no_encontrado` (las mismas respuestas que con la entidad activa: el emisor ya está autenticado);
- firma válida y todo en orden => el cuerpo crudo se **encola** (tabla de tenant `postal_callback_diferido`, migración `048`)
  junto con el asiento `document.envio_callback_diferido`, en **una** transacción, y se responde **`202 {"status":"deferred"}`**.
  Un `event_id` repetido recibe el mismo 202 sin segundo asiento;
- con más de `POSTAL_DIFERIDOS_MAX` (10000) pendientes en el tenant, vuelve el `503 canal_no_disponible` + `Retry-After`
  como contrapresión. El conteo se hace bajo un `pg_advisory_xact_lock` por tenant (sin él, inserciones concurrentes superaban
  el tope en tantas como hubiera en vuelo);
- el cuerpo no puede pasar de 64 KiB **con la entidad activa y con la suspendida** (`413 cuerpo_demasiado_grande`, tras el HMAC,
  así que no es un oráculo para un anónimo): lo que se acepta hoy no puede perderse mañana por no caber en la cola.

**Drenaje**: fase 5 del barrido, que solo recorre tenants **activos**: reactivar la entidad es lo que lo dispara. Exclusivo por
tenant entre réplicas (advisory lock), en orden de recepción (`recibido_at, id`), cada fila en su propia transacción
(aplicación + cierre + asientos). **No se vuelve a verificar el HMAC**: el secreto pudo rotar durante la suspensión y la
verificación quedó asentada al recibir; en su lugar se coteja `sha256(cuerpo)` con la huella guardada al recibir (una fila
reescrita después no se aplica: `descartado`, `huella_no_coincide`). El `ts` del historial del envío es el instante de
**recepción del aviso del operador**, no el del drenaje.

**Qué es y qué no es esa fecha.** Es la fecha en que el operador *avisó*, no la del hecho postal: no equivale por sí sola a la
certificación de entrega del operador que exigen los arts. 67-69 del CPACA. Por eso el cuerpo admite un `ocurrido_at` opcional
(ISO 8601 con zona; va dentro del cuerpo firmado, así que lo autentica el HMAC) que se guarda aparte en
`postal_shipment_event.ocurrido_operador_at`; `ts` no se sustituye. Si el operador no la manda, queda `NULL` y el sistema no
inventa una.

Un 4xx determinista al drenar (la guía desapareció) deja la fila `descartado` con el asiento
`document.envio_callback_diferido_descartado`; un error no determinista la deja `pendiente` (+1 intento) y corta el lote, pero
solo hasta `POSTAL_DIFERIDO_MAX_INTENTOS` (10): pasado el tope se descarta (`error_persistente`) con el mismo asiento y un
WARNING, y el drenaje continúa con la siguiente fila (una fila envenenada no bloquea la cola del tenant).

**Riesgo aceptado**: si tras reactivar entra un callback en vivo antes de que se drene uno diferido más viejo, la máquina de
estados (`registrado → en_transito → entregado|devuelto|fallido`) impide retroceder y el viejo queda `recorded_no_change`
(se registra la notificación sin cambiar el estado).

### 8. Excepción de conservación: la entidad suspendida sigue conservando (B10)

La suspensión detiene la **operación** de la entidad, no su **obligación de conservar** (Ley 594/2000 art. 46; Ac. AGN 003/2015): el
plazo de retención WORM, el `ArchiveTimeStamp` de una firma LTA y la fixity siguen corriendo. Con el veto del punto 2 aplicado también
a las rutas internas, el job de renovación LTA de signature-service y las fases WORM/AIP de archive-service recibían 403 de storage
y la conservación se detenía **en silencio** (un plazo que corre sin que nadie lo vea: el mismo defecto que B9 corrigió en lo público).

**Decisión.** `get_db_conn` de storage-service admite una petición pese al veto cuando se cumplen, **juntas**:

1. la entidad está `suspended` **y** aprovisionada con schema (`atencion_publica == SOLO_LECTURA`): nunca `inactive`, inexistente ni sin
   aprovisionar;
2. método + ruta están en una **lista explícita** (`app/core/conservacion.py::RUTAS_DE_CONSERVACION`, `fullmatch` sobre `scope["path"]` —la ruta que enruta Starlette y firma ADR-036—, de modo que `%0A`, `%3F` o un salto de línea final no casan; una ruta nueva
   queda vetada hasta que alguien la añade a propósito):
   `POST /api/v1/storage/internal/artefacto`, `POST /api/v1/storage/internal/files/hashes`,
   `GET /api/v1/storage/files/{id}/download`, y `POST /api/v1/preservacion/{renovar-artefacto,renovar-retencion,proteger-indice,
   proteger-artefacto,aip,eventos}-internal`;
3. `X-Internal-Token` válido (tiempo constante). Las rutas de la lista ya lo exigían; aquí es condición de la excepción, no solo de la ruta.

Las admisiones dejan el asiento `entidad.conservacion_admitida` (canal `system`, payload con `estado_entidad`, método y ruta normalizada), **uno por (entidad, método, ruta normalizada, día UTC) y proceso**: las fases de fixity llaman a la misma ruta una vez por expediente en cada pasada y el audit_log no se puede borrar; el detalle por petición ya lo dejan los asientos propios de cada operación (WORM, PREMIS). Si el registro de entidades falla al decidir, se mantiene el veto (fail-closed); el token se compara en tiempo constante sobre bytes (un token no ASCII da 403, no 500).

Todo lo demás —`/upload`, `legal-hold-internal`, `purgar-huerfanos`, cualquier lectura o escritura de usuario— sigue vetado. En el lado de
los llamadores, las fases de **conservación** de archive-service (`run_once_worm`, `_actas_worm`, `_firmas_personales_worm`,
`_actas_eliminacion_worm`, `_worm_renovacion`, `_fixity_premis`) y el job LTA de signature-service recorren las entidades `active` y
`suspended`; las que **operan** (sellar índices y actas, origen legado) siguen solo con las activas.

**Por qué no más ancha.** Abrir storage entero a las entidades suspendidas habría reabierto la brecha de B9 (identidad reproducida dentro
de la red). Por qué no más estrecha: sin estas rutas la conservación no tiene cómo ejecutarse. **Residuos declarados.**
- **El empaquetado de un AIP nuevo NO está entre las fases que recorren suspendidas** (`run_once_aip` sigue con las activas): necesita los anexos del expediente y document-service veta `fetch_anexos_internal` a una entidad suspendida; visitarla solo agotaría reintentos (`archive.aip_agotado`, terminal hasta un reintento manual). El empaquetado espera a la reactivación; lo que no puede esperar —la retención del AIP ya existente— sí corre (`run_once_worm_renovacion`).
- `POST /api/v1/storage/internal/artefacto` queda admitida como **escritura genérica** de artefactos del sistema: la lista se escribe por ruta, no por llamador (con un token compartido no se puede otra cosa), y esa ruta la usan también otros servicios. Hoy los flujos que no son de conservación los veta document-service, donde nace la petición del usuario; la defensa en profundidad del punto 2 en storage no cubre esa escritura para una entidad suspendida. Estrecharlo exigiría una ruta propia del job LTA.

## Consecuencias

- Una entidad suspendida recibe `403 tenant_inactivo` en toda la API autenticada y no recibe token nuevo; en las rutas públicas conserva las lecturas y sus escrituras responden `503 canal_no_disponible` (salvo el callback postal firmado, que se difiere con `202`, §7).
- Un schema inexistente es `503 tenant_no_provisionado`, no un `500`.
- Los tests que dan por operativa a la entidad sin atravesar la dependencia real lo declaran (`patch` de `estado_entidad`); el
  veredicto lo cubren pruebas propias con una base real (`estado_entidad` sobre cinco filas) y con dobles por servicio.
- El frontend debe tratar un `403 tenant_inactivo` en `/auth/me` y en el login como «Entidad suspendida».
- La identidad firmada que viaja entre servicios se verifica con una ventana de 120 s: todo cliente interno debe
  firmar **al enviar** (`identidad.firmar` ya estampa el instante), no reutilizar cabeceras firmadas guardadas.
- **Riesgo conocido**: un token de superadmin que lleve el `tenant_slug` de la entidad suspendida no podrá reactivarla por el
  gateway; si el superadmin opera sin entidad (`tenant_slug` vacío), sí.
