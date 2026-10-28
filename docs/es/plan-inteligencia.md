# Plan de inteligencia

> **Estado: D0 construida (2026-10-28); D1 a D3, planeadas.** knowledge-service ya no
> llama a modelos y el asistente conversacional está retirado. La decisión
> de arquitectura es [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md)
> (Propuesto) y el protocolo es PPI v1, en borrador, en `docs/contratos/ppi/`.
> Actualizado al 2026-10-27, con la revisión de ADR-045 que deja a cada entidad decidir
> qué niveles salen. [ADR-046](adr/ADR-046-conocimiento-sin-modelos.md) (Propuesto, 2026-10-28)
> propone que knowledge-service no llame a modelos: `local` pasa a ser la búsqueda de texto
> completo de document-service, sin generación. Donde este plan dice «envoltorio de lo
> actual» o «índice local», léase ese respaldo.

## Para qué

Docuboto evoluciona para incorporar un **proveedor interno de inteligencia**: un programa
aparte, con su propia licencia, que corre en sus propios contenedores dentro de la
instalación, bajo contrato con la entidad, y que habla con Docuboto solo por PPI. Docuboto sigue siendo el sistema de registro y funciona
entero sin proveedor.

El plan tiene una fase previa, D0, que solo quita código ([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)), y tres fases que coinciden con las del primer proveedor para poder medir
ambos lados contra los mismos hitos:

| Fase | Qué gana el funcionario | Hito del proveedor |
|---|---|---|
| **D0 — Retirar la capa local de modelos** | Nada nuevo: buscar y antecedentes siguen, ahora por texto completo; «Sugerir texto» se oculta hasta que haya proveedor | Ninguno |
| **D1 — Camino de lectura** | Buscar y preguntar sobre el cuerpo y los anexos de los radicados, con citas | Hito D1 |
| **D2 — Triage de entrada** | Sugerencias de tipificación, plazo, reparto, antecedentes y remitente, que acepta o rechaza en la bandeja | Hito D2 |
| **D3 — Borradores de respuesta** | Un borrador de respuesta citado, que se convierte en borrador de Docuboto a su nombre | Sin hito propio del proveedor; criterios de Docuboto |

**Arranque del piloto (2026-10-28).** D1 y D2 salen a producción solo con PÚBLICA.
RESERVADA y CLASIFICADA se habilitan después, con el acto del administrador que ya existe,
cuando firmen asesoría jurídica (ADR-045, pregunta 8) y la revisión de seguridad del
proveedor. Ninguna fase espera por esto.

## Reglas que valen para las tres fases

1. **Solo salen los niveles que la entidad habilita.** Por defecto, solo PÚBLICA.
   RESERVADA y CLASIFICADA salen solo si el administrador habilita ese nivel con una
   declaración que nombra la ruta de modelos del proveedor. Un radicado de un nivel no
   habilitado no llega nunca al proveedor, por ningún camino. Lo decide una sola función y
   lo hace cumplir un solo punto de paso, que relee el nivel vivo y los niveles habilitados
   al enviar.
2. **Con el interruptor apagado no hay llamadas.** Si la bandera de ADR-037 está apagada
   o la entidad está en `local`, Docuboto no hace ninguna llamada al proveedor.
3. **Lo aceptado lo ejecuta el funcionario.** Cada aceptación pasa por el gate de
   escritura con la identidad de la persona y deja su asiento con la sugerencia en
   `correlacion`. El proveedor no tiene credencial de la API de Docuboto.
4. **El código no nombra a ningún proveedor.** El nombre del proveedor es un dato que
   llega en su declaración `capabilities` firmada; su URL se configura por variable
   (`PPI_PROVIDER_URL`).

Cada regla tiene su guardián (ver [Guardianes transversales](#guardianes-transversales)).

---

## Fase D0 — Retirar la capa local de modelos

**Objetivo.** Docuboto deja de llamar a modelos antes de que empiece D1
([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)): knowledge-service pierde su capa local y
mcp-server pierde el asistente. Es una fase que solo quita código y
no necesita al proveedor. Con ella se corta el egreso por defecto a ollama.com.

| Servicio | Cambio |
|---|---|
| knowledge-service | `/search` y `/antecedentes` consultan `GET /api/v1/search` de document-service con el token del usuario, con la misma forma de respuesta y la etiqueta «índice local». `/rag` responde 503 `rag_unavailable`. Ruta nueva `GET /api/v1/knowledge/availability`, que dice si hay generación para la entidad. |
| | Se borran `app/services/llm.py`, `app/services/embeddings.py`, `app/services/ingestion.py`, el consumidor `knowledge-ingest`, `app/jobs/reconciliacion_nivel.py`, `app/ops/reembed.py`, `requirements-ml.txt` y el build `WITH_ML`, y la dependencia `anthropic`. |
| | Una migración elimina `knowledge_chunk`, `kb_suggestions` y la extensión `vector`, y se retira `/suggestions/{id}/aceptar`. No hay instalaciones con datos, así que no se conserva histórico. |
| mcp-server | Se retira el asistente ([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md) §6): `assistant.py`, `asistente_gate.py`, `stt.py`, su router y sus esquemas, `assistant_model` y la dependencia `anthropic`. El catálogo MCP, sus transportes y el gate de escritura se quedan. |
| frontend | «Sugerir texto» en radicar y borradores se muestra solo si `availability` dice que hay generación. Se borran `AssistantDock.svelte` y `VoiceInput.svelte`. |
| infra | `docker-compose.yml` deja de pasar `AI_PROVIDER`, `MODEL_GENERATION`, `OLLAMA_*` y `EMBEDDING_PROVIDER` a knowledge-service, y `ANTHROPIC_API_KEY` a mcp-server. Se borran `whisper-service` y el perfil `assistant`. |

**Guardián.** Un test de importación falla si knowledge-service o mcp-server importan un SDK de modelos
(`anthropic`, `openai`, `sentence_transformers`, `ollama`) o lee una variable `AI_*`,
`EMBEDDING_*` u `OLLAMA_*`.

**Criterios de éxito.**

- [x] Buscar y antecedentes devuelven, para el mismo usuario, los mismos radicados que la
      búsqueda de la pantalla de consulta, y nunca uno fuera de su clearance. Se cumple por
      construcción: consultan esa misma búsqueda, como el usuario.
- [x] `/rag` responde 503 `rag_unavailable`, y el botón «Sugerir texto» no aparece.
- [ ] Con la red de salida cortada, knowledge-service arranca y responde igual. Falta
      comprobarlo con la pila levantada; el código ya no tiene ningún egreso a modelos.
- [x] Las tools MCP `buscar_conocimiento`, `buscar_antecedentes` y
      `redactar_con_antecedentes` siguen respondiendo sin cambios en mcp-server.

---

## Fase D1 — Camino de lectura

**Objetivo.** En la bandeja y en `/busqueda`, el funcionario pregunta y busca sobre el
cuerpo y los anexos de los radicados de niveles habilitados. Cada afirmación lleva cita, o la
respuesta es «evidencia insuficiente». Si el proveedor no está, responde el índice local.

### Cambios por servicio

| Servicio | Cambio |
|---|---|
| knowledge-service (la pasarela) | **Puerto de proveedor** con dos implementaciones: `local` (la búsqueda de texto completo que deja D0) y `ppi` (cliente HTTP de PPI v1). |
| | **Punto de paso único** de todo envío al proveedor, con la función «¿puede salir?» (nivel vivo entre los niveles habilitados de la entidad y envío no suspendido) en `app/core/clearance.py`. Ningún otro módulo usa el cliente PPI. |
| | **Consumidor `intelligence-gateway`** sobre `orpycamcp.document.events` y `orpycamcp.workflow.events`, con reclamación de la PEL y dead-letter (ADR-021). Empuja altas y cambios, y ordena borrados por reclasificación hacia un nivel no habilitado, anulación, purga y nivel deshabilitado. |
| | **Traducción a PPI**: radicado → `record` con una lista cerrada de campos (número, fecha, tipo E/S/I, tipo documental, dependencia, asunto, remitente, vencimiento oficial, estado), y anexo → `attachment` por localizador. Los metadatos de plantilla salen aplanados en `metadata` (PPI 1.2) solo si la entidad tiene `intelligence_send_metadata` encendido. |
| | **Libro de envíos** de solo inserción y **reconciliación periódica** contra el nivel vivo y contra el inventario PPI (`GET /v1/ingest/inventory`). |
| | **Carga inicial y reanudación**: al pasar a `ppi`, o al volver a encender la bandera, empuja lo pendiente de los niveles habilitados por lotes reanudables con cursor. |
| | **Conexión PPI**: declara niveles, vocabularios, URL de retorno y claves (`PUT /v1/connection`); lee y verifica `capabilities` con la clave fijada en configuración; guarda su huella y suspende el envío si cambia. |
| | **Ruta interna de retorno `content`**, firmada con HMAC por entidad (ventana de 300 s, nonce de un solo uso en Redis): vuelve a pasar la barrera y sirve los bytes desde document-service. No la enruta el gateway. |
| | **Delegación de `/search` y `/rag`**, con alcance opcional a un radicado y sus anexos. Re-comprobación viva de cada acierto y cada cita, y respaldo `local` con su etiqueta. Plazos: buscar 3 s (p95 2 s), preguntar 30 s (p95 15 s), borrador 5 min (ADR-045 §5). Las tres tools de conocimiento del asistente (E21) pasan por aquí y heredan todo esto sin cambios en mcp-server. |
| | **`on_behalf_of` seudónimo** (HMAC del `auth_users.id` con clave de la entidad) y su clearance real, recortada al nivel habilitado más alto. |
| | **Métricas** sin etiqueta de tenant (ADR-041): envíos, rechazos de barrera, respaldos activados y suspensiones. |
| document-service | **Dos rutas internas** (`X-Internal-Token`): el registro de un radicado y los bytes de un anexo (mediados por storage-service, como D-02). Ambas comprueban en el `WHERE` que el nivel esté entre los habilitados y dejan constancia en el historial de acceso del radicado («transmitido al proveedor de inteligencia»). |
| | **Eventos nuevos por el outbox** (`042_eventos_salientes`): anexo añadido y datos del radicado cambiados. |
| tenant-service | **`GET` y `PUT /api/v1/config/intelligence`** (`USUA_PERM_ADMIN` nivel 3, `FOR UPDATE` y asiento `tenant.intelligence_changed` en una transacción). Pasar a `ppi` exige bandera encendida, política publicada y `transmission_statement` con el proveedor y sus subencargados tal como los declara `capabilities`, y habilita PÚBLICA. El mismo `PUT` enciende o apaga `intelligence_send_metadata` (asiento `tenant.intelligence_metadata_changed`). |
| | **`PUT /api/v1/config/intelligence/levels`** (mismo patrón y asiento `tenant.intelligence_level_enabled`): habilita RESERVADA o CLASIFICADA, nivel por nivel, con una declaración que nombra la ruta de modelos, los modelos y los subencargados de ese nivel según `capabilities`. Deshabilitar es inmediato y ordena el borrado de lo transmitido de ese nivel (`POST /v1/ingest/purge` con `levels`, PPI 1.1.0). |
| | **`POST /api/v1/config/intelligence/withdraw`**: pide al proveedor el borrado de todo lo de la entidad (`POST /v1/ingest/purge`) y deja asiento. |
| auth-service | `/auth/me` publica `intelligence_provider` junto a `asistente_habilitado`, para que la UI decida qué mostrar. `ALLOWED_AZP` **no cambia**. |
| `scripts/init_tenant.py` | **Paso nuevo e idempotente** que crea el cliente de servicio de la entidad en el realm (confidencial, solo `client_credentials`), guarda su secreto cifrado con la KEK e imprime el par *(issuer, client_id)* que registra el proveedor. Se puede correr para una entidad existente. |
| api-gateway | Enruta `/api/v1/config/intelligence*`. **No** enruta ninguna ruta interna de la pasarela. |
| frontend | **Panel «Preguntar»** en el drawer de detalle de la bandeja (`frontend/src/routes/(app)/bandeja/+page.svelte`): solo con bandera, `PERM_CONOCIMIENTO` y radicado de un nivel habilitado. Muestra las citas con enlace al anexo y la página, dice cuándo responde el índice local y avisa de que la pregunta y el radicado se transmiten, y por qué ruta. |
| | **Pantalla de administración** «Inteligencia»: proveedor, subencargados y enrutamiento según `capabilities`, declaración, niveles habilitados con la ruta de cada uno, estado (suspendido o activo) y retiro de datos. En `local` muestra cuántos radicados conserva el proveedor (inventario PPI), y la confirmación del cambio a `local` ofrece «retirar también los datos». |
| infra | **Perfil de Compose `intelligence`** en `docker-compose.yml` e `infra/docker-compose.prod.yml`: no arranca ningún servicio del proveedor. Pasa knowledge-service a `ppi`, lo conecta a la red externa `ppi` (`docker network create --internal ppi`, creada una vez por host fuera de los dos proyectos) y le da `PPI_PROVIDER_URL` sin valor por defecto. El proveedor es otro proyecto Compose con su propia versión (ADR-045 §8). |

### Permisos nuevos

Ninguno. Consultar sigue siendo `PERM_CONOCIMIENTO` nivel 1 y configurar es
`USUA_PERM_ADMIN` nivel 3, como en ADR-037.

### Migraciones

| Servicio | Migración | Contenido |
|---|---|---|
| tenant-service (global) | siguiente número libre | `public.tenants.intelligence_provider TEXT NOT NULL DEFAULT 'local'` con `CHECK (… IN ('local','ppi'))`. Todas las entidades quedan en `local`. |
| tenant-service (global) | siguiente número libre | `public.tenants.intelligence_levels SMALLINT[] NOT NULL DEFAULT '{1}'`, con `CHECK` de niveles válidos. |
| tenant-service (global) | siguiente número libre | `public.tenants.intelligence_send_metadata BOOLEAN NOT NULL DEFAULT false`. |
| knowledge-service (tenant) | siguiente número libre | `intelligence_send_log`: libro de envíos de solo inserción, con trigger que niega `UPDATE` y `DELETE`. Se retiene como `audit_log` (ADR-008): sin purga automática, y nunca las filas de un radicado que el proveedor aún conserva. |
| knowledge-service (tenant) | siguiente número libre | `intelligence_credentials`: secretos cifrados con la KEK y AAD de entidad y finalidad (cliente de servicio, claves HMAC de retorno, clave de seudónimo). |
| knowledge-service (tenant) | siguiente número libre | `intelligence_state`: huella de `capabilities` aceptada, suspensión y cursor de la carga inicial. |

### Eventos

- **Consume**: `document.radicado.created`, `reclassified`, `annulled` y `purged`;
  `workflow.step.created` y `workflow.step.completed`; `workflow.radicado.estado_cambiado`.
- **Nuevos, emitidos por document-service**: anexo añadido y datos del radicado
  cambiados. Los nombres se fijan al implementarlos, con la forma
  `document.<objeto>.<acción>` del sobre canónico (ADR-010).

### Guardianes de la fase

- Los cuatro [transversales](#guardianes-transversales), que nacen en esta fase.
- **Las citas se re-comprueban.** Una cita a un radicado anulado, reclasificado o fuera de
  la clearance se descarta. Una afirmación sin citas se retira, y una respuesta sin
  afirmaciones queda en «evidencia insuficiente».
- **Las llamadas del proveedor van firmadas.** `content` con firma mala, instante viejo o
  nonce repetido da 401, y un localizador de un nivel no habilitado da 410.
- **El cliente de la entidad no es credencial de la API.** El cliente creado por
  `init_tenant.py` no aparece en `ALLOWED_AZP` y solo tiene cuenta de servicio.
- **El perfil no arranca al proveedor.** Un guardián de compose comprueba que el perfil
  `intelligence` no define servicios nuevos, que solo knowledge-service se conecta a la
  red `ppi`, que esa red es `external` y que `PPI_PROVIDER_URL` no tiene valor por
  defecto.
- **La especificación PPI cumple su contrato.** Los ejemplos de
  `docs/contratos/ppi/examples/manifest.json` dan el resultado declarado contra su
  esquema. La carpeta solo tiene `.md`, `.json` y `.yaml`, bajo la AGPL del
  repositorio. (Requiere añadir `jsonschema` a las dependencias de prueba de
  `shared`.)
- `test_knowledge_checks_flag.py` y `test_toda_ruta_lleva_gate` se extienden a las
  rutas nuevas.

### Criterios de éxito (equivalen al hito D1 del proveedor)

- [ ] En una instalación de staging de Docuboto, los funcionarios buscan y preguntan
      desde la bandeja y `/busqueda` sobre el cuerpo y los anexos. Cada respuesta va
      citada o es «evidencia insuficiente», y cada cita mostrada pasó la re-comprobación
      viva.
- [ ] Con el proveedor detenido, Docuboto sigue funcionando: buscar y preguntar caen a
      `local` con su etiqueta, y radicar, tramitar y firmar no se enteran. Al volver, el
      consumidor recupera lo pendiente sin pérdida.
- [ ] La reconciliación queda registrada: en el proveedor no existe nada fuera de los
      niveles habilitados (inventario PPI contra nivel vivo), y el libro de envíos de
      Docuboto no tiene ningún envío de un nivel no habilitado al enviar.
- [ ] Una reclasificación hacia un nivel no habilitado, una anulación, una purga y un nivel
      deshabilitado terminan en un borrado verificado en el inventario del proveedor.
- [ ] La ruta de modelos de cada nivel habilitado coincide con la `capabilities` firmada y
      con la declaración del administrador.
- [ ] La instalación corre desde una versión publicada, con la imagen del proveedor fijada
      por digest.
- [ ] Con la bandera apagada o la entidad en `local`, el registro de acceso del proveedor
      no muestra ninguna llamada de esa entidad durante la ventana de prueba.
- [ ] Firma de una persona responsable, registrada en el changelog.

---

## Fase D2 — Triage de entrada

**Objetivo.** Lo que entra con un nivel habilitado por ventanilla y por correo recibe sugerencias de
tipificación, plazo, reparto, antecedentes y remitente. El funcionario las acepta o las
rechaza en la bandeja, y cada aceptación es un acto suyo, auditado con su sugerencia.

Esta fase incluye **arreglos de plazo que hacen falta con o sin proveedor**: hoy los
radicados de ventanilla y de correo nacen sin vencimiento, porque nadie pasa
`response_days` (`document_service.py:365-376`, `ventanilla_service.py:539`,
`email_bandeja_service.py:504`).

### Cambios por servicio

| Servicio | Cambio |
|---|---|
| knowledge-service | **Ruta interna de retorno `recommend`** (HMAC por entidad). Guarda en `suggestions` de forma idempotente por `(provider, ref)`. Rechaza la clase desconocida, el código fuera de vocabulario, el radicado cuyo nivel ya no está habilitado o la versión vieja. Registra los «ninguna» explícitos con su motivo para medir cobertura. |
| | **Rutas de la bandeja**: listar por radicado (`PERM_CONOCIMIENTO` nivel 1 y clearance en el `WHERE`), `accept` y `reject` (bandera, `PERM_CONOCIMIENTO` nivel 2 y permiso propio de la operación de destino; rechazar lleva motivo de catálogo cerrado). |
| | **Reglas de vida**: `superseded` cuando llega un reemplazo o cambia el valor oficial; `expired` a los 30 días sin decidir (igual para todas las clases), o por cierre, anulación o subida de nivel. |
| | **Outbox de decisiones** hacia `POST /v1/decisions`, con reintentos, bajo la barrera y el interruptor. |
| | **Vocabularios**: tipos documentales activos (archive) y dependencias activas por código (tenant-service), leídos por rutas internas y redeclarados en la conexión PPI cuando cambian. |
| mcp-server | **Ruta interna para acuñar una propuesta** a nombre del funcionario (`X-Internal-Token` e identidad firmada de la persona). La llama la pasarela al aceptar, y el token lleva dentro la referencia de la sugerencia. |
| | **Al ejecutar** esa propuesta: cabecera firmada con la sugerencia (mismo mecanismo que `X-Origen-Canal`) y aviso del resultado a la pasarela. |
| | **Tools nuevas** en el catálogo: fijar o corregir el vencimiento, `link_related_records`, y conciliar la ficha provisional o vincular actores (`/api/v1/terceros/…`). |
| shared (`orpycamcp_common`) | **Firma y verificación** de la cabecera de sugerencia (`identidad`), `ContextVar` de sugerencia (`request_log`) y bloque `correlacion.suggestion` con identificador, proveedor y modelos en `audit.append`. El canal sigue siendo `mcp` y el `CHECK` de `canal` no cambia. |
| api-gateway | Reenvía la cabecera de sugerencia **solo** si su firma verifica, igual que `X-Origen-Canal`. Enruta `/api/v1/knowledge/suggestions*`. |
| document-service | **Vínculos entre radicados**: `POST /api/v1/documents/{id}/links` y su borrado, para un vínculo `related` simétrico. Lleva `PERM_RADI` nivel 2, clearance sobre los dos radicados en el `WHERE` y asiento. El detalle lo devuelve, filtrado por clearance, y la pasarela lo envía en `record.links` de PPI. |
| document-service | **Plazo por defecto**: si un radicado de Entrada llega sin `response_days`, toma el plazo por defecto de su tipo documental. Aplica a ventanilla, correo manual y automático, y lotes. El importador conserva el vencimiento que traiga el paquete. |
| | **El sondeo de correo construye `DocumentService` con cliente de tenant-service.** Hoy lo hace con `None` (`email_sondeo.py:216`), y calcular el plazo lanzaría `RuntimeError`. |
| | **Operación auditada para fijar o corregir el vencimiento** (`PUT /api/v1/documents/{id}/due-date`, con fecha o días hábiles, y motivo). Rechaza la ampliación si quien la pide es el asignado actual del radicado, o si pasa del doble del término inicial. Lleva `PERM_DUE_DATE` nivel 3, clearance en el `WHERE`, `FOR UPDATE` y asiento con `antes`, `despues`, `motivo` y `origen` (manual o sugerencia). Emite por el outbox el evento de vencimiento cambiado. |
| archive-service | `tipos_documentales.default_response_days` (días hábiles, opcional) en la administración de tipos documentales. Es configuración de cada entidad, no una ley escrita en el código: el término legal (Ley 1755 de 2015, art. 14 [confirmar]) lo pone la entidad. |
| workflow-service | Consume el evento de vencimiento cambiado y alinea `due_at` del paso activo. |
| | Corrige los dos hallazgos de [endurecimiento](#endurecimiento-encontrado-al-leer-el-codigo) que afectan a esta fase: `_publish` y la regla en el asiento del reparto. |
| auth-service | Permiso nuevo `PERM_DUE_DATE`, sin concesión automática: lo reparte el administrador. La pantalla de administración avisa si ningún grupo lo tiene. `scripts/seed_demo.py` lo incluye en su grupo de todos los permisos. |
| frontend | **Panel de sugerencias** en el drawer de la bandeja: por clase, con evidencia citada; «Aceptar» abre `ProposalCard` con los valores sugeridos editables (tipo documental, fecha, dependencia) y «Rechazar» pide motivo. La decisión vuelve con `applied_arguments`. |
| | **Sección «Radicados relacionados»** en el detalle del radicado: lista los vínculos que el funcionario puede ver, permite quitarlos y ofrece «incluir en el expediente de este radicado» como acción manual aparte. |
| | **Insignias en la lista de la bandeja**: sugerencias pendientes por radicado. |
| | **Campo de vencimiento** editable con motivo, y **plazo por defecto** en la administración de tipos documentales. |

### Permisos nuevos

- **`PERM_DUE_DATE`** (nivel 3: fijar o corregir el vencimiento). Va aparte de
  `PERM_RADI` porque ampliar el propio plazo es un conflicto de interés. **No el propio**
  (ADR-045, pregunta 3): el funcionario que tiene asignado el radicado no puede ampliar su
  vencimiento, aunque tenga el permiso; lo hace un colega o un superior, con motivo.
  Acortar siempre se permite. La ampliación no pasa del doble del término inicial
  (Ley 1755 de 2015, art. 14 [confirmar]). No hay segunda persona.
  Queda como pieza aparte de D2 avisar al peticionario de la nueva fecha antes de que
  venza el plazo inicial, como pide ese mismo artículo.
- Decidir una sugerencia **no** es un permiso nuevo: es `PERM_CONOCIMIENTO` nivel 2
  (ADR-037) más el permiso propio de la operación.

### Migraciones

| Servicio | Contenido |
|---|---|
| knowledge-service (tenant) | `suggestions`: clases y estados con `CHECK`, `UNIQUE (provider, ref)`, trigger de transiciones (solo desde `pending`, terminales inmutables) e índices por radicado y estado. |
| knowledge-service (tenant) | `suggestion_decisions_outbox` (outbox) y `intelligence_triage` (último «ninguna» por radicado y clase, con su motivo). |
| archive-service (tenant) | `tipos_documentales.default_response_days INT NULL CHECK (… BETWEEN 1 AND 3650)`. |
| document-service (tenant) | `record_links`: `(record_a, record_b, relation, created_by, created_at)`, con `relation` en `CHECK (relation IN ('related'))`, un par ordenado único (`record_a < record_b`) y `ON DELETE CASCADE`. |
| auth-service (tenant) | Alta de `PERM_DUE_DATE` en el catálogo, sin concederlo a ningún grupo. |

### Eventos

- **Nuevo**: vencimiento del radicado cambiado (document-service, por outbox). Lo
  consumen workflow-service y la pasarela.
- La pasarela usa «datos del radicado cambiados» (D1) para marcar `superseded` una
  sugerencia cuyo valor oficial fijó una persona.

### Guardianes de la fase

- **Toda aceptación queda auditada con su sugerencia** (transversal; se completa aquí).
- **Aceptar exige las tres condiciones**, al acuñar y otra vez al ejecutar. La matriz
  cubre que falte cada una, incluido retirar el permiso entre acuñar y confirmar.
- **La cabecera de sugerencia no se puede forjar**: el gateway la descarta sin firma
  válida.
- **Las clases son cerradas y coinciden** en el `CHECK`, la constante, la UI y la
  traducción a `kind` de PPI.
- **Transiciones**: solo desde `pending`; recibir dos veces el mismo `(provider, ref)`
  no duplica.
- **Ningún radicado de Entrada con plazo por defecto nace sin vencimiento**, por ventanilla,
  por correo (manual y automático) ni por lote.
- **Corregir el vencimiento deja asiento** con `antes`, `despues` y `motivo`, y respeta el
  clearance.
- **El modo automático del correo radica**: una prueba que hoy fallaría (ver
  endurecimiento).

### Criterios de éxito (equivalen al hito D2 del proveedor)

- [ ] En una instalación de Docuboto, lo que entra con un nivel habilitado por ventanilla y por correo
      recibe sugerencias de tipificación, plazo, reparto y antecedentes, o un «ninguna»
      explícito, con p95 de 10 minutos desde la radicación y el 99 % en menos de 60
      minutos, para anexos de hasta 50 páginas en total, visibles con su insignia en la
      bandeja. Lo que pasa de 50 páginas se tramita igual, marcado fuera del objetivo.
- [ ] Los funcionarios aceptan o rechazan en Docuboto. Cada aceptación aparece en la cadena
      de `audit_log` con la persona como actor y `correlacion.suggestion` con
      identificador, proveedor y modelo. Lo comprueba `verify_audit_chains` más una
      consulta.
- [ ] La auditoría de Docuboto no muestra ningún cambio de estado hecho por el proveedor:
      no hay actor fuera de `auth_users` y del sistema, ni aceptación sin sugerencia
      correlada.
- [ ] Sobre el conjunto de oro de la entidad (radicados cerrados ya transmitidos, sin una
      exportación aparte), medido fuera de línea antes de encender D2:

      | Clase | Métrica | Umbral |
      |---|---|---|
      | Tipo documental | top-1 / top-3 | ≥ 80 % / ≥ 95 % |
      | Plazo | fecha exacta | ≥ 95 % |
      | Reparto (dependencia) | top-1 / top-3 | ≥ 70 % / ≥ 90 % |
      | Antecedentes | precisión (revisión humana por muestra) | ≥ 70 % |
      | Remitente (vínculo de tercero) | precisión | ≥ 95 % |

      Una clase que no llega a su umbral no se activa: el proveedor no la emite, y Docuboto
      no necesita un interruptor por clase. La entidad puede ajustar los umbrales antes de
      la corrida.
- [ ] Al menos el 95 % de lo que entra con un nivel habilitado recibe sugerencias o un «ninguna» explícito
      con motivo, medido sobre `intelligence_triage`.
- [ ] Apagar la inteligencia en Docuboto (bandera o `local`) detiene toda llamada y todo
      egreso hacia el proveedor.
- [ ] Firma de una persona responsable, registrada en el changelog.

---

## Fase D3 — Borradores de respuesta

**Objetivo.** Desde el drawer, el funcionario pide un borrador de respuesta. El proveedor
lo prepara con citas, y al aceptarlo se convierte en un borrador de Docuboto a nombre del
funcionario, que sigue su circuito normal de revisión, visto bueno y firma.

### Cambios por servicio

| Servicio | Cambio |
|---|---|
| knowledge-service | **Ruta «pedir borrador»**: bandera, `PERM_CONOCIMIENTO` nivel 1 y radicado de un nivel habilitado. Lanza `POST /v1/runs` (`draft_reply`) bajo la barrera, con el radicado y sus anexos como alcance. |
| | El resultado llega como recomendación `draft` y se guarda como sugerencia `reply_draft`. |
| | Al aceptar, acuña la propuesta de `crear_borrador` con `radicado_padre_id` y el cuerpo sugerido. `crear_respuesta_vinculada` queda como opción, porque consume un número de radicado (pregunta abierta). |
| mcp-server | La tool `crear_borrador` expone `radicado_padre_id`. `BorradorCreate` ya lo admite (`schemas/borrador.py`), pero la tool no lo ofrece hoy. |
| frontend | **Botón «Pedir borrador»** y **vista del borrador sugerido** con sus citas. El cuerpo se pinta como texto, nunca como HTML: lo redactó un modelo a partir de texto de un tercero. Al aceptar se abre `ProposalCard`, y el borrador se abre en el editor de borradores. |

### Permisos nuevos, migraciones y eventos

- **Permisos**: ninguno. Crear el borrador exige el permiso propio de `crear_borrador`
  (`PERM_RADI`) o de `crear_respuesta_vinculada` (`PERM_RESPUESTA_VINCULADA`).
- **Migraciones**: ninguna. La clase `reply_draft` ya está en el `CHECK` desde D2.
- **Eventos**: ninguno nuevo.

### Guardianes de la fase

- **Aceptar un borrador crea un borrador**, no un radicado, a nombre del funcionario y con
  la sugerencia en `correlacion`.
- **El cuerpo sugerido se muestra como texto**: ningún `{@html}` en los componentes de
  sugerencias.
- **`run` solo sobre niveles habilitados** y solo con el interruptor encendido.

### Criterios de éxito

- [ ] Un funcionario pide un borrador, lo recibe con citas, lo edita y lo acepta. El
      borrador queda a su nombre, enlazado al radicado de entrada y con la sugerencia
      correlada en el asiento.
- [ ] Ningún radicado se crea sin la confirmación explícita del funcionario en
      `ProposalCard`.
- [ ] Sobre una muestra acordada con la entidad, la proporción de borradores aceptados
      (con o sin edición) se mide y se registra. No hay umbral hasta tener la línea base.

---

## Guardianes transversales

Nacen en D1 y se completan en D2. Son pruebas de `shared/tests` o de cada servicio, según
lo que miren.

| Guardián | Qué comprueba | Cómo |
|---|---|---|
| **Ningún proveedor se nombra en el código** | Ni `services/`, ni `frontend/src`, ni `shared/`, ni `scripts/`, ni los compose ni `infra/` nombran a un proveedor de inteligencia. | La lista prohibida se guarda como **huellas SHA-256** de los nombres normalizados, para que el guardián tampoco los nombre; se compara token a token. Exentos: `docs/` (los ADR y este plan pueden contar la historia) y los ajustes de modelos que ADR-006 ya admite. |
| **Nada de un nivel no habilitado llega a un proveedor** | Ningún camino envía un radicado de un nivel que la entidad no habilitó: registro, anexo, alcance de consulta, tarea `run`, descarga por localizador y outbox de decisiones. | Transporte falso que falla ante cualquier petición con un nivel no habilitado. Incluye la carrera de una reclasificación entre el evento y el envío. Un control AST asegura que solo el punto de paso usa el cliente PPI. |
| **Con el interruptor apagado no hay llamadas ni egreso** | Con la bandera apagada, o la entidad en `local`, ni el consumidor, ni buscar, ni preguntar, ni la carga inicial, ni la reconciliación, ni el outbox de decisiones, ni `run` llaman al proveedor. | Transporte falso que falla ante cualquier petición. En compose, el proveedor solo está en una red `internal: true`. |
| **Toda aceptación queda auditada con su sugerencia** | Cada sugerencia `accepted` tiene un asiento de la operación de destino con la persona como actor y `correlacion.suggestion` (identificador, proveedor, modelos), unido por el `jti` al asiento `intelligence.suggestion_accepted` de la pasarela. | Prueba de integración del recorrido completo, más un control que recorre las sugerencias aceptadas de un tenant de prueba y busca su asiento. |

---

## Endurecimiento encontrado al leer el código

Verificado leyendo el código del commit `7510257`, **sin ejecutarlo y sin corregirlo**.
Cada punto se corrige en su propio cambio. Los tres primeros los señaló la revisión del
lado del proveedor; los demás aparecieron al preparar este plan.

1. **El modo automático del correo no radica ningún correo** (casi seguro, por lectura).
   - `email_sondeo.py:209-217` llama a `servicio.radicar(..., datos={})`.
   - `radicar` comprueba los datos mínimos de una Entrada **antes** de nada:
     `faltantes("E", doc_class=None, dest_dept_code=None)` devuelve los dos campos y
     lanza `ErrorDeCuenta("datos_minimos_incompletos", 422)`
     (`email_bandeja_service.py:305-318`; `core/datos_minimos.py`).
   - El sondeo la atrapa (`email_sondeo.py:218-226`), suma un fallo y sigue: **todo**
     correo falla igual.
   - Efecto lateral probable: el correo queda reservado a nombre del responsable durante
     `RESERVA_MINUTOS` (15) y vuelve a fallar en la siguiente vuelta.
   - Además, `DocumentService(conn, self._tenant, None, None)` va sin cliente de
     tenant-service, así que en cuanto haya plazo por defecto lanzaría un `RuntimeError`
     que ese `except` no atrapa.
2. **`_publish` del flujo se traga los errores**
   (`workflow_service.py:1362-1391`, `except Exception: pass`). Ni registra ni reintenta.
   Un `workflow.step.created` perdido deja sin enterarse a notification-service y, con
   este plan, a la pasarela. document-service ya resuelve esto con un outbox
   (`042_eventos_salientes`); el flujo debería hacer lo mismo, o al menos registrar.
3. **El asiento del reparto automático no dice qué regla lo decidió.**
   - `auto_distribute` llama a `WorkflowService.assign(..., sistema=True)`
     (`distribution.py:176-189`).
   - El asiento `workflow.assign` (`workflow_service.py:1343-1359`) lleva dependencias y
     estado, pero no la regla.
   - La regla solo aparece en un `logger.info` (`distribution.py:194-199`) y en el asiento
     de la ruta de dependencia inválida (`distribution.py:153-168`).

   Para D2 importa: una sugerencia de reparto y una regla automática tienen que poder
   distinguirse en la auditoría.
4. **El job `test-unit-shared` de CI no instala `fastapi`**, y
   `shared/tests/test_gate_any_of.py` lo importa: la colección falla antes de correr
   nada. En local, los 696 tests de `shared` pasan solo si se añaden `fastapi` y `httpx`.
5. **`docs/es/integraciones-externas.md` §5** dice que no hay opt-in por entidad (OMI-04),
   pero ADR-037 lo implementó con `asistente_habilitado`.
6. **knowledge-service lee `radicados`** para el nivel vivo y esa lectura no está en la
   lista cerrada de ADR-043 §5. ADR-045 propone declararla.

## Orden y dependencias

1. **D0** primero: quita la capa local de modelos y no depende del proveedor.
2. **D1**, en este orden:
   1. migraciones y aprovisionamiento;
   2. punto de paso y barrera, con sus guardianes **antes** de cualquier envío real;
   3. consumidor y carga inicial;
   4. delegación con respaldo;
   5. panel «Preguntar», configuración y perfil de Compose.
3. **Los arreglos de plazo de D2** (plazo por defecto, operación de vencimiento, sondeo
   de correo) no dependen del proveedor y pueden adelantarse.
4. **El resto de D2** necesita D1 y, del lado del proveedor, la entrega de
   recomendaciones.
5. **D3** necesita la bandeja de D2 y, del lado del proveedor, la tarea `draft_reply`
   por `run`.

## Preguntas abiertas

Las del responsable de producto están en
[ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md#preguntas-abiertas-para-el-responsable-de-producto).
Este plan añade cuatro:

1. ~~**Concesión inicial de `PERM_DUE_DATE`**~~. Resuelta el 2026-10-28: a ninguno. Un
   tenant nuevo no tiene grupos cuando corren sus migraciones, así que lo reparte el
   administrador, como en ADR-037. La pantalla de administración avisa si ningún grupo
   lo tiene, y el grupo de todos los permisos de `seed_demo.py` lo incluye.
2. ~~**p95 de D2**~~. Propuesta de Docuboto fijada el 2026-10-28, a acordar con el
   proveedor: p95 de 10 minutos desde la radicación hasta las sugerencias o un «ninguna»
   explícito, y el 99 % en menos de 60 minutos, para anexos de hasta 50 páginas.
3. ~~**Umbrales de exactitud de D2**~~. Propuesta de Docuboto fijada el 2026-10-28, a acordar
   con la entidad: la tabla de los criterios de éxito de D2, medida fuera de línea sobre el
   conjunto de oro, sin modo sombra. Una clase que no llega no se activa: el proveedor no la
   emite.
4. ~~**Plazo de retención del libro de envíos**~~. Resuelta el 2026-10-28: la misma
   política que `audit_log` (ADR-008). No hay purga automática; se purga solo cuando la
   TRD lo permite, con la cuenta administrativa aparte y su propio asiento, y nunca las
   filas de un radicado que el proveedor aún conserva. `webhook_entregas` (ADR-018) sigue
   la misma regla.