# ADR-046: La capa de conocimiento no llama a modelos — la inteligencia la pone el proveedor y el respaldo es la búsqueda de texto completo

**Estado**: Propuesto
**Fecha**: 2026-10-28
**Autores**: Camilo Romero; borrador para revisión del responsable de producto y del mantenedor
**Revisión 2026-10-28 (sin despliegues previos)**: Docuboto y el proveedor no tienen instalaciones ni datos que migrar, así que no hace falta compatibilidad hacia atrás. `knowledge_chunk`, `kb_suggestions` y la extensión `vector` se eliminan en D0 en vez de quedar como histórico, y no hay nota de migración. Se resuelven las preguntas 2 y 3.
**Revisión 2026-10-28 (el asistente)**: se retira el asistente conversacional de mcp-server (§6). Con eso, ningún componente de Docuboto llama a un modelo. El servidor MCP y su gate de escritura se quedan. Se resuelve la pregunta 1.
**Relacionados**: [ADR-045](ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md) (pasarela de inteligencia y PPI; **se enmienda**), [ADR-006](ADR-006-proveedor-ia-configurable.md) (proveedor de IA configurable; **deja de aplicar a la capa de conocimiento**), [ADR-037](ADR-037-asistente-activable-por-entidad.md) (bandera por entidad y `PERM_CONOCIMIENTO`; sigue gobernando), [ADR-014](ADR-014-busqueda-postgresql-fts.md) (búsqueda con PostgreSQL FTS; es el respaldo), [ADR-019](ADR-019-capa-mcp.md) (capa MCP), [ADR-020](ADR-020-sistema-diseno-orpyca-asistente.md) (asistente conversacional; fuera de alcance)

---

> **Nada de lo que este ADR decide está construido.** Lo que existe se cita en «Punto de partida» con su archivo, verificado contra `main` en `1aa6402`. Las piezas de ADR-045 que este ADR toca (el puerto `local`, el respaldo y la bandeja) tampoco están construidas.

## Contexto

ADR-045 hace de knowledge-service la pasarela hacia un proveedor interno (el primero previsto es Proboto) y deja `local` como implementación por defecto y como respaldo: «el índice de hoy (pgvector) y la generación configurada por ADR-006». Así, Docuboto conserva **dos** caminos de inteligencia que hacen lo mismo con controles distintos:

- **`local`**: embeddings propios y generación propia con cuatro proveedores, elegidos por despliegue (ADR-006), con la barrera fija `is_generatable` / `is_embeddable`.
- **`ppi`**: el proveedor, con su pasarela de modelos, su verificación de citas, una ruta de modelos por nivel y una declaración `capabilities` firmada que el administrador acepta nivel por nivel.

Mantener los dos cuesta: elegir y operar modelos, prompts, defensas contra inyección, validación de citas, re-embebido, dos barreras de residencia y un índice propio que hay que mantener al día por eventos. Y lo que `local` aporta es poco: indexa un fragmento por radicado con asunto y remitente, algo que document-service ya indexa con texto completo.

La dirección del producto es que Docuboto sea el sistema de registro y que la inteligencia la ponga el proveedor. Este ADR lleva esa dirección hasta el final en la capa de conocimiento: **knowledge-service no llama a ningún modelo**.

### Punto de partida (verificado en el código)

- **Generación local.** `knowledge-service/app/services/llm.py` (451 líneas) implementa `ollama_local`, `ollama_cloud`, `openai_compatible` y `anthropic`, elegidos por `AI_PROVIDER`. La usa `POST /api/v1/knowledge/rag` (`app/routers/knowledge.py`), que guarda cada respuesta en `kb_suggestions` (migraciones `008`, `009`, `012`) y la acepta quien la pidió por `/suggestions/{id}/aceptar`.
- **El valor por defecto ya egresa.** `docker-compose.yml` fija `AI_PROVIDER=${AI_PROVIDER:-ollama_cloud}` con `MODEL_GENERATION=gpt-oss:120b`: cada `/rag` envía a ollama.com los fragmentos de nivel inferior a RESERVADA.
- **Embeddings.** `app/services/embeddings.py` ofrece `local_st` (sentence-transformers, ~400 MB, capa opcional `requirements-ml.txt`), `ollama` y `stub`. Compose usa `stub` por defecto, un vectorizador por hashing: búsqueda léxica disfrazada de vectorial.
- **Índice propio.** `knowledge_chunk` (pgvector, `vector(384)` con HNSW, migración `007`) guarda **un** fragmento por radicado con asunto, nombre y entidad del remitente (`app/services/ingestion.py:239-247`). Lo alimenta el consumidor `knowledge-ingest` sobre `orpycamcp.document.events`, y lo mantienen `ingestion.py` (422 líneas: nivel efectivo, tombstones, anulación), `app/jobs/reconciliacion_nivel.py` y `app/ops/reembed.py`.
- **Ese texto ya está indexado.** `radicados.search_vector` es una columna generada con `public.spanish_unaccent` sobre número, asunto, nombre y entidad del remitente, dependencia destino y observaciones (`document-service/migrations/tenant/020_search_unaccent.sql`). La consulta es `GET /api/v1/search` (`document-service/app/routers/search.py`, `websearch_to_tsquery` con `ts_rank`), con la autorización y la clearance de document-service. Es un superconjunto del texto de `knowledge_chunk`, siempre al día porque es una columna generada.
- **Quién llama a `/rag`.** Dos proxies del frontend (`frontend/src/routes/(app)/radicar/api/rag/suggest/+server.js` y `borradores/api/rag/suggest/+server.js`, que alimentan `RagSuggestButton.svelte`) y la tool MCP `redactar_con_antecedentes`. `/search` y `/antecedentes` los usan `busqueda/api/semantica`, `busqueda/api/antecedentes`, `radicar/api/rag/antecedentes` y las tools `buscar_conocimiento` y `buscar_antecedentes`.
- **El asistente es otro llamador de modelos.** `mcp-server/app/services/assistant.py` habla con la API de Anthropic (`assistant_model`, ADR-020) y aplica su propio recorte de residencia. No pasa por knowledge-service.

## Decisión

### 1. knowledge-service no llama a modelos

Ni de generación ni de embeddings, en ningún modo. No tiene credenciales de modelos, ni dependencias de SDK de modelos, ni configuración de proveedores de IA. Toda inferencia de la capa de conocimiento la hace el proveedor, detrás de PPI.

knowledge-service queda como la **pasarela** de ADR-045, con estas responsabilidades y ninguna otra:

- consumir eventos de documentos y de flujo para el proveedor (grupo `intelligence-gateway`, ADR-045 §2);
- decidir qué sale (niveles habilitados y huella de `capabilities`, ADR-045 §3 y §4), con su libro de envíos y su reconciliación;
- hablar PPI: empujar, borrar, consultar y recibir recomendaciones;
- volver a comprobar en vivo cada acierto y cada cita que devuelve el proveedor (ADR-045 §5);
- la bandeja `suggestions` y su paso al gate de escritura del MCP (ADR-045 §6 y §7);
- el respaldo local sin modelos (§2 de este ADR);
- la bandera, el permiso y la dead-letter que ya tiene.

### 2. El respaldo local es la búsqueda de texto completo de document-service

`local` deja de ser «índice pgvector más generación». Pasa a ser:

| Ruta | Con `ppi` | Con `local`, o si el proveedor falla |
|---|---|---|
| `/search` | Delegada al proveedor, con re-comprobación viva | La pasarela consulta `GET /api/v1/search` de document-service **con el token del usuario**, a través del gateway, y devuelve los aciertos con la misma forma de respuesta y la etiqueta «índice local» |
| `/antecedentes` | Delegada al proveedor | Igual, con una consulta construida a partir del asunto y el remitente del radicado pivote (términos unidos con `or` en `websearch_to_tsquery`), excluyendo el propio pivote |
| `/rag` | Delegada al proveedor | **503 `rag_unavailable`**, el contrato de degradación honesta que ya existe |

Por qué así:

- **No hay índice que mantener.** `search_vector` es una columna generada: no hay consumidor, ni retraso, ni reconciliación de niveles, ni tombstones, ni re-embebido.
- **La autorización la pone quien ya la pone.** Al consultar como el usuario, document-service aplica su RBAC y su clearance sobre el nivel vivo del radicado, como en cualquier búsqueda. La pasarela no duplica la barrera.
- **No hay egreso que vigilar.** Sin modelo, el respaldo no transmite nada, así que `is_embeddable`, `is_generatable` y `egreso_con_bandera_apagada` dejan de tener objeto.
- **La pérdida es pequeña.** Sin vectores no hay sinónimos ni paráfrasis, pero eso ya pasa hoy con `stub`, el valor por defecto. Lo que se gana con texto completo (números de radicado, nombres, entidades, sin acentos) es lo que más se busca en un SGDEA.

### 3. Lo generado viene solo del proveedor y va a `suggestions`

- `kb_suggestions` y `/suggestions/{id}/aceptar` se retiran. Todo lo generado lo origina el proveedor y entra por la bandeja `suggestions` de ADR-045 §6. Con eso desaparece la pregunta de tener dos tablas de sugerencias.
- La tabla `kb_suggestions` se elimina en D0. No hay instalaciones con datos, así que no hay histórico que conservar.
- **«Sugerir texto»** en radicar y borradores, y la tool `redactar_con_antecedentes`, siguen llamando a `/rag`. Con `ppi`, la pasarela lo delega como una consulta PPI con alcance; en D3, la redacción pasa a la recomendación `draft` del plan. Con `local`, el botón no se muestra: el frontend pregunta a la pasarela si la generación está disponible para la entidad (`GET /api/v1/knowledge/availability`, que responde sin llamar al proveedor), en vez de mostrar el botón y fallar.

### 4. Qué se retira de knowledge-service

| Pieza | Destino |
|---|---|
| `app/services/llm.py`, `AI_PROVIDER`, `MODEL_GENERATION`, `OLLAMA_*`, la dependencia `anthropic` | Se borran |
| `app/services/embeddings.py`, `EMBEDDING_PROVIDER`, `EMBEDDING_DIM`, `requirements-ml.txt` y el build `WITH_ML` | Se borran; la imagen queda sin PyTorch ni modelos |
| `app/services/ingestion.py`, el consumidor `knowledge-ingest`, `app/jobs/reconciliacion_nivel.py`, `app/ops/reembed.py` | Se borran; el consumidor de la pasarela es el único |
| `knowledge_chunk` | Una migración de D0 la elimina, con su índice HNSW |
| `is_embeddable`, `is_generatable`, `is_local_llm_provider`, `is_local_embedding_provider` (`app/core/clearance.py`) | Se borran. `require_asistente_habilitado`, `require_permission` y `get_actor` se quedan |
| La extensión `vector` (migraciones globales `001` y `002`) | Se retira en D0: ningún otro servicio usa pgvector |
| `kb_suggestions` y `KbSuggestionRepository` | Se eliminan en D0 (§3) |

Guardián nuevo: un test de importación falla si algún módulo de knowledge-service importa un SDK de modelos (`anthropic`, `openai`, `sentence_transformers`, `ollama`) o lee una variable `AI_*`, `EMBEDDING_*` u `OLLAMA_*`.

### 5. Enmiendas

- **ADR-045 §1.** La tabla del puerto de proveedor queda así: `local` es «el respaldo sin modelos: búsqueda de texto completo de document-service, sin generación»; `ppi` no cambia. La frase «el índice `local` se sigue alimentando siempre» se retira: el respaldo no tiene índice propio.
- **ADR-045 §3.** «`is_generatable` y `is_embeddable` siguen decidiendo para `local`» se retira: en `local` no sale nada.
- **ADR-045 §5.** El respaldo es el de §2 de este ADR. La etiqueta «respuesta del índice local» se mantiene; preguntar sin proveedor responde 503 `rag_unavailable`.
- **ADR-045 §6.** «`kb_suggestions` sigue como traza de lo que genera `local`» se sustituye por §3 de este ADR.
- **ADR-006** deja de aplicar a la capa de conocimiento: no hay proveedor de IA configurable por despliegue en knowledge-service. Con el asistente retirado (§6), CC-03 ya no tiene a quién aplicarse en Docuboto.
- **ADR-037** no cambia: la bandera y `PERM_CONOCIMIENTO` siguen gobernando `/search`, `/antecedentes` y `/rag` en los dos modos.

### 6. Se retira el asistente

El asistente conversacional de mcp-server (ADR-020) era el último componente de Docuboto que llamaba a un modelo: hablaba con Anthropic y aplicaba su propio recorte de RESERVADA y superior. Se retira en D0, junto con la capa local de modelos:

| Pieza | Destino |
|---|---|
| `mcp-server/app/services/assistant.py`, `asistente_gate.py`, `stt.py`, `app/routers/assistant.py` y `app/schemas/assistant.py` | Se borran, con `assistant_model`, `ANTHROPIC_API_KEY` y la dependencia `anthropic` |
| `whisper-service` y el perfil `assistant` de Compose | Se borran |
| `AssistantDock.svelte` y `VoiceInput.svelte` en el frontend | Se borran |
| El catálogo MCP, sus transportes y el gate de escritura (`write_gate.py`, `proposals.py`) | **Se quedan**: los usan los clientes MCP externos y la aceptación de sugerencias (ADR-045 §7) |
| `ProposalCard.svelte` | Se queda, para aceptar sugerencias |

Quien quiera actuar con IA sobre Docuboto usa un cliente MCP externo, con el modelo que ese cliente elija, o las sugerencias del proveedor. Llevar un asistente al proveedor exigiría que este actuara con la identidad del funcionario en la API de Docuboto, algo que ADR-045 prohíbe; si algún día hace falta, se diseña en su propio ADR.

## Alternativas consideradas

- **Mantener `local` como está (ADR-045 original).** Descartado: dos caminos de inteligencia con controles distintos, el de `local` más débil (proveedores externos elegidos por despliegue, sin declaración por nivel), y un valor por defecto que ya egresa a un tercero.
- **Quitar solo la generación y conservar pgvector con `local_st`.** Descartado: sigue exigiendo un modelo, una imagen de varios cientos de MB, re-embebido, el consumidor y la reconciliación de niveles, para indexar el mismo texto que `search_vector` ya cubre.
- **Un índice propio de texto completo en knowledge-service.** Descartado: duplicaría `search_vector` y su autorización, y volvería a necesitar consumidor y reconciliación.
- **Quitar también `/search` y `/antecedentes` en `local` y mandar a los clientes a `/api/v1/search`.** Descartado: obliga al frontend y a las tools MCP a saber en qué modo está la entidad. Mantener las rutas en la pasarela deja el cambio de modo invisible para quien llama.

## Consecuencias

- **Una sola fuente de inteligencia.** Modelos, prompts, citas, verificación y rutas por nivel viven en el proveedor. Docuboto no elige ni opera modelos para conocimiento.
- **knowledge-service adelgaza mucho.** Se van unas 1.550 líneas de servicio (generación, embeddings, ingesta, reconciliación, re-embebido), sus tests, la capa ML y una tabla con su índice HNSW.
- **El respaldo es exacto y siempre al día**, porque es la búsqueda que ya usa la pantalla de consulta.
- **Sin proveedor no hay respuestas ni borradores generados.** Una entidad en `local` conserva buscar y antecedentes por texto completo; preguntar y «sugerir texto» no se ofrecen. La condición 2 de ADR-045 («Docuboto funciona entero sin proveedor») sigue valiendo para la gestión documental; la generación pasa a ser algo que exige el proveedor.
- **Los funcionarios pierden el chat y la voz del asistente.** Preguntar queda en el panel «Preguntar» del proveedor (ADR-045 D1), y actuar, en las sugerencias o en un cliente MCP externo.
- **Se cierra el egreso por defecto** a ollama.com que hoy configura `docker-compose.yml`.
- **Búsqueda sin sinónimos en el respaldo.** Es lo que ya da `stub` hoy.

## Residuos declarados

Ninguno: no hay instalaciones previas que obliguen a conservar tablas, variables o servicios.

## Preguntas abiertas para el responsable de producto

1. ~~**El asistente.**~~ Resuelta el 2026-10-28: se retira en D0 (§6). El servidor MCP y su gate se quedan.
2. ~~**Retención de `kb_suggestions`.**~~ Resuelta el 2026-10-28: no hay instalaciones con datos, así que la tabla se elimina en D0.
3. ~~**La extensión `vector`.**~~ Resuelta el 2026-10-28: se retira en D0. Ningún otro servicio usa pgvector (`radicados.search_vector` es un `tsvector`).
4. ~~**Orden frente a D1.**~~ Resuelta el 2026-10-28: se hace **antes** de D1, como fase D0 corta ([plan de inteligencia](../plan-inteligencia.md), fase D0). La pasarela de D1 nace sobre el `local` sin modelos, y el egreso por defecto a ollama.com se corta en cuanto D0 se despliega.
