# ADR-051: PPI se queda en lo construido y vive dentro de la app; se retira el MCP

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-019](ADR-019-capa-mcp.md) (capa MCP; **se retira**), [ADR-045](ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md) (pasarela de inteligencia; **se reduce**), [ADR-046](ADR-046-conocimiento-sin-modelos.md) (conocimiento sin modelos; **se conserva**), [ADR-037](ADR-037-asistente-activable-por-entidad.md) (bandera por entidad; **se reduce a PPI**), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-048](ADR-048-identidad-emitida-por-docuboto.md)

---

## Contexto

El interés declarado es **conectar con proboto-agent**. Eso hace que el destino de dos componentes distintos dependa de un mismo hecho: qué parte de la capa de conocimiento sirve a esa conexión y qué parte solo servía a los funcionarios.

### Lo que PPI tiene construido, en los dos lados

El contrato es de este repositorio (`docs/contratos/ppi/`, versión **1.3.0**) y tiene 12 rutas más 2 callbacks del host. Lo implementado en ambos lados es:

| Ruta | En Docuboto | En proboto |
|---|---|---|
| `GET /v1/capabilities` | cliente | sí (`ppi/routes.py:124`) |
| `PUT`/`GET /v1/connection` | cliente | sí (`:135`, `:149`) |
| `POST /v1/ingest` | sí | sí (`:159`) |
| `POST /v1/ingest/purge` | sí | sí (`:167`) |
| `GET /v1/ingest/inventory` | sí | sí (`:190`) |
| `GET /v1/ingest/{ref}/status` | sí | sí (`:200`) |
| `POST /v1/extract` | sí | sí (`:210`) |
| `POST /v1/query` | sí | sí (`:176`) |
| `POST /v1/runs` | **no** | **no** |
| `GET /v1/runs/{ref}` | **no** | **no** |
| `POST /v1/decisions` | **no** | **no** |
| callback `recommend_url` | **no hay ruta** | **no lo llama** |
| callback `content_url` | sí (`routers/retorno.py:119`) | sí (`adapters/source_content/host.py`) |

El bucle de recomendaciones —el que convertiría a proboto en un asistente que propone y aprende— **no existe en ninguno de los dos lados**. `recommend_url` se declara y se guarda; nadie la llama. `kb_suggestions` se eliminó en D0.

### La versión está rota

Docuboto publica 1.3.0 (`ppi-v1.openapi.yaml:14`). proboto declara `protocol_version = "1.2.0"` (`backend/src/proboto/config/settings.py:190`) y ancla su conformidad al commit `9b82c9c` de Docuboto (`backend/tests/ppi_spec.py:27`), anterior a 1.3.0. A la vez **ya implementa** las dos adiciones de 1.3.0 (`status`, `extract`). Es decir: el contrato y las dos implementaciones no coinciden sobre cuál es la versión vigente.

### La ingesta no corre en la topología que se envía

- `services/docuboto/docuboto/core/lifespan.py:11`: *«The intelligence consumer does not start here.»*
- El manejador de la cola para inteligencia **lanza**:

```78:84:services/docuboto/docuboto/core/trabajo.py
    if not fila_entidad["asistente_habilitado"] or fila_entidad["intelligence_provider"] != "ppi":
        return
    raise RuntimeError("intelligence gateway parked until a provider is configured")
```

Con el perfil por defecto, una entidad en `ppi` no tiene consumidor y su trabajo de cola revienta. Solo el perfil `split` con `knowledge-service` vivo empuja algo. Conectar proboto hoy exige justo el perfil que este rediseño retira.

### Y el MCP no aporta a esa conexión

`mcp-server` son 10.381 líneas, de las cuales 6.604 son un catálogo de 217 herramientas escrito a mano que refleja la API HTTP (`app/tools/catalog.py`). Su propósito (ADR-019) es que **clientes MCP externos** actúen con el token del usuario. Ninguna de las dos partes de PPI lo usa. El propio roadmap le reconoce **85 brechas reales**.

## Decisión

### 1. Se retira `mcp-server`

| Pieza | Destino |
|---|---|
| `services/mcp-server/**` (10.381 líneas) | Se borra |
| Catálogo de 217 herramientas | Se borra; la API HTTP es el catálogo |
| Gate de escritura, propuestas firmadas, transportes HTTP y stdio | Se borra |
| `MCP_ORIGIN_SECRET`, `MCP_PROPOSAL_SECRET` y su canal de auditoría | Se borra |
| Contenedor, migraciones y entradas en CI | Se borra |
| ADR-019 | Se marca **retirado** |

Quien quiera que un agente actúe sobre Docuboto usa la API HTTP con una cuenta de servicio de alcance acotado (ADR-048), o el proveedor por PPI.

### 2. PPI se queda, y se queda dentro del proceso

`knowledge-service` (10.186 líneas) se reparte en dos destinos:

| Parte | Destino |
|---|---|
| PPI saliente: cliente, punto de paso, traducción, libro de envíos, credenciales, nonces, callback `content` (~6.000 líneas) | Paquete `ppi/` dentro de la app |
| Búsqueda y antecedentes locales sobre `document-service` (~1.500 líneas) | Se convierten en rutas nativas de `document` (ADR-055 §7) |
| `/rag` y su 503 `rag_unavailable` | Se retira, con las tres afordancias de UI que lo llaman |
| `/availability` y `/transmission` | Se retiran; la pantalla de administración ya lee la configuración de la entidad |
| Consumer, workers, dead-letter propios | Se integran en el bus único (ADR-049) |

**El consumidor de ingesta arranca en el `lifespan` de la app.** Ese es el arreglo del fallo de arriba: no es una promesa, es una línea en el único sitio donde se arrancan los bucles.

Con `PPI_PROVIDER_URL` vacío, el paquete es inerte: ninguna llamada sale y ningún bucle se registra.

### 3. El contrato se reduce a lo construido y se publica inmutable

Se publica **PPI v1.4.0** con dos cambios y ninguno de comportamiento:

1. **Se eliminan** `POST /v1/runs`, `GET /v1/runs/{ref}`, `POST /v1/decisions` y la declaración de `recommend_url`. Publicar un bucle de recomendaciones que nadie construyó es un contrato que miente.
2. **El directorio pasa a ser versionado**: `contracts/ppi/v1.4.0/` con copia congelada del OpenAPI y los esquemas. Un consumidor anclado a una versión lee esa versión y no lo que haya en `HEAD` hoy. Eso arregla la causa del desajuste 1.2.0/1.3.0: proboto ancla por directorio, no por commit.

`PPI-Version` sigue aceptando `^1\.\d+\.\d+$`, así que un consumidor en 1.2.0 sigue siendo compatible (las retiradas eran opcionales y nadie las llamaba). proboto re-ancla su `PROBOTO_PPI_SPEC_DIR` a `v1.4.0` y declara 1.4.0.

### 4. La entidad sigue empezando en `local`

`public.tenants.intelligence_provider` continúa con valor por defecto `'local'`, que significa *no enviar nada a ningún sitio*. Una alcaldía sin proveedor no tiene consentimiento ni motivo para exportar documentos. Cambiar a `ppi` sigue exigiendo el acto administrativo que hoy registra `PUT /config/intelligence` con `transmission_statement`, y sigue siendo por niveles.

Lo único que cambia: el token de servicio deja de venir de Keycloak y lo emite Docuboto (ADR-048).

### 5. El MCP no se sustituye por nada equivalente

Se declara explícitamente que Docuboto **no es un servidor de herramientas para agentes**. Es un sistema de registro con una API HTTP y una pasarela hacia un proveedor de inteligencia. Si dentro de un año hace falta, se diseña en su propio ADR con el catálogo generado desde OpenAPI, no escrito a mano.

## Alternativas consideradas

- **Conservar el MCP y retirar PPI.** Descartado: es lo contrario de lo pedido. Además, el MCP es 10.381 líneas con 85 brechas; PPI es 6.000 con un consumidor que funciona (en `split`).
- **Mantener `knowledge-service` como contenedor aparte.** Descartado: su superficie de usuario desaparece con el MCP y con `/rag`, y su superficie PPI no tiene por qué ser un proceso. Dos contenedores para hablar con un proveedor que recibe un push cada varios minutos es coste sin función.
- **Publicar PPI 2.0.0 al retirar rutas.** Descartado: rompería a cualquier consumidor por rutas que nadie llamaba. 1.4.0 con retiradas es honesto y compatible; si algún día se rehace el bucle de recomendaciones, eso sí es 2.0.0.
- **Dejar el directorio del contrato en `HEAD` y anclar por commit.** Descartado: es exactamente lo que produjo el desajuste actual.
- **Sustituir el MCP por un catálogo generado desde OpenAPI.** Descartado por ahora: sigue siendo una superficie de herramientas para agentes que nadie pidió, y el problema del MCP no era escribirlo a mano sino que no aportaba.

## Consecuencias

- **~13.900 líneas menos** (10.381 del MCP y ~3.500 del conocimiento de usuario) y un contenedor menos.
- **La conexión con proboto deja de depender de un perfil de compose.** Funciona en la topología que se envía, que es el punto de todo el ADR.
- **El contrato se vuelve verificable.** Una copia congelada por versión más los 63 *fixtures* de conformidad permiten a proboto probar contra una versión y no contra un commit.
- **Se pierde la capacidad de que un cliente MCP (Claude Desktop, un IDE) opere el SGDEA.** Se declara como no disponible, no como pendiente.
- **Se pierde «Preguntar» y los dos botones «Sugerir con antecedentes».** Se ocultan; no se dejan apuntando a un 404. Buscar y antecedentes por texto completo se quedan (ADR-055 §7, ADR-056 §4).
- **Pendiente de decisión de producto**: si proboto algún día implementa el bucle de recomendaciones, hará falta una tabla de sugerencias y una pantalla para aceptarlas. Hoy no existe en ninguno de los dos lados y este ADR no la inventa.

## Preguntas abiertas

1. **Confirmar con proboto que la retirada de `runs`/`decisions`/`recommend` es aceptable.** Nadie los implementó, pero la decisión es de las dos partes. Si el plan de proboto los contempla a corto plazo, la alternativa es mantenerlos declarados y marcarlos `deprecated` en 1.4.0, y retirarlos en 2.0.0.
2. **Verificar que proboto acepta el emisor OIDC propio** (ADR-048) antes de dar la integración por cerrada.
