# ADR-045: Pasarela de inteligencia — un proveedor interno elegido por entidad y hablado por un protocolo neutro (PPI)

**Estado**: Propuesto
**Fecha**: 2026-10-27
**Autores**: Camilo Romero; borrador para revisión del responsable de producto y del mantenedor
**Revisión 2026-10-27 (niveles por entidad)**: el proveedor interno corre dentro de la instalación y bajo contrato con la entidad, así que es un encargado, no un tercero externo. Cada entidad decide qué niveles le transmite (por defecto solo PÚBLICA), y el proveedor declara en su `capabilities` firmada con qué modelos procesa cada nivel. Cambian la condición 3, §2, §3, §4, §5, §9 y las consecuencias. El resto queda igual.
**Revisión 2026-10-28 (nombres en inglés)**: los identificadores nuevos que propone este ADR pasan a inglés, como pide la regla 1 de contribución: tablas (`suggestions`, `intelligence_send_log`…), columnas (`intelligence_provider`, `intelligence_levels`), clases y estados de sugerencia, rutas (`/api/v1/config/intelligence`), acciones de auditoría, el grupo consumidor, el perfil y el servicio de Compose, y `PERM_DUE_DATE`. Las decisiones no cambian.
**Revisión 2026-10-28 (empaquetado)**: §8 se alinea con el empaquetado que acordó el proveedor: dos proyectos Compose en el mismo host, una red externa `ppi` y un perfil `intelligence` que no arranca ningún servicio del proveedor. Se resuelve la pregunta abierta 7.
**Enmendado por**: [ADR-046](ADR-046-conocimiento-sin-modelos.md) (propuesto): knowledge-service no llama a modelos; `local` pasa a ser la búsqueda de texto completo de document-service, sin generación ni índice propio, y lo generado solo llega por `suggestions`. Afecta a §1, §3, §5 y §6.
**Relacionados**: [ADR-006](ADR-006-proveedor-ia-configurable.md) (proveedor de IA; **se enmienda**), [ADR-037](ADR-037-asistente-activable-por-entidad.md) (bandera por entidad y `PERM_CONOCIMIENTO`; esta decisión vive bajo ella), [ADR-019](ADR-019-capa-mcp.md) (capa MCP y gate de escritura), [ADR-018](ADR-018-interoperabilidad-webhooks.md) (webhooks; no se usan aquí), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (consumidores y dead-letter), [ADR-036](ADR-036-identidad-atada-a-metodo-y-ruta.md) y [ADR-044](ADR-044-identidad-de-un-solo-uso-y-search-path-sin-public.md) (firma con nonce de un solo uso), [ADR-043](ADR-043-retencion-legal-por-motivo-prestamo-identificado-y-soporte-citado.md) §5 (contrato SQL entre servicios), [ADR-008](ADR-008-auditoria-inmutable.md) (auditoría)

---

> **Nada de lo que este ADR decide está construido.** Las piezas nuevas —la pasarela, el puerto de proveedor, la bandeja `suggestions`, el perfil `intelligence`, las rutas internas y los eventos nuevos— son **propuestas**. Lo que ya existe se cita en «Punto de partida» con su archivo, verificado contra el código del commit `7510257`. El plan por fases está en [Plan de inteligencia](../plan-inteligencia.md) y la especificación del protocolo en `docs/contratos/ppi/`.

## Contexto

Docuboto evoluciona para incorporar inteligencia: búsqueda y preguntas sobre el cuerpo y los anexos de los radicados, y triage de lo que entra (tipificación, plazo, reparto, antecedentes, remitente, borrador de respuesta). La inteligencia la pone un **proveedor interno**: un programa aparte, con su propia licencia, que corre en sus propios contenedores dentro de la misma instalación. El primero previsto es un producto comercial independiente; Docuboto no lo nombra en su código.

Tres condiciones gobiernan el diseño:

1. **Docuboto sigue siendo el sistema de registro.** Radicación, flujo, expedientes, firma, archivo y auditoría siguen siendo suyos. El proveedor prepara y recomienda; quien decide es un funcionario en Docuboto, y lo aceptado se ejecuta **como ese funcionario**.
2. **Docuboto funciona entero sin proveedor**, y queda limpio de AGPL y neutral frente a proveedores.
3. **Cada entidad decide qué niveles salen.** Por defecto solo sale PÚBLICA (1). RESERVADA (2) y CLASIFICADA (3) salen solo si el administrador de la entidad habilita ese nivel, con una declaración que nombra cómo lo procesa el proveedor. El proveedor interno corre dentro de la instalación y bajo contrato con la entidad: es un encargado, no un tercero externo. Un modelo alojado por terceros solo se admite en planes de pago que no entrenan con los datos del cliente y no conservan ni las entradas ni las salidas, y figurando como subencargado. CC-03 sigue gobernando, sin cambios, a los proveedores de generación externos de la implementación `local` (ADR-006); para `ppi` la sustituye esta política por entidad (§3, §9).

### Punto de partida (verificado en el código)

- **knowledge-service ya es el dueño de la capa de conocimiento.** Tiene la bandera `asistente_habilitado` y `PERM_CONOCIMIENTO` (ADR-037, `require_asistente_habilitado` y `require_permission` en `app/routers/knowledge.py`), las barreras de residencia `is_embeddable` / `is_generatable` (`app/core/clearance.py`), la comprobación del nivel vivo (`GREATEST(k.nivel_seguridad, r.nivel_seguridad)` con `LEFT JOIN radicados`, `app/repositories/knowledge_repository.py:28-30`) y la tabla `kb_suggestions` (migraciones `008` y `012`).
- **El índice es pobre.** La ingesta indexa solo asunto, nombre y entidad del remitente, en **un** fragmento por radicado (`app/services/ingestion.py:239-247` y `upsert_chunk` con `ON CONFLICT (source_type, source_ref)`). Ni el cuerpo ni los anexos entran.
- **El consumidor existente** usa su propio grupo, `knowledge-ingest`, sobre `orpycamcp.document.events`, y atiende `document.radicado.created`, `reclassified`, `annulled` y `purged` (`app/workers/document_consumer.py`). El flujo publica en `orpycamcp.workflow.events` (`workflow_service.py:74`).
- **`kb_suggestions` no es una bandeja.** Es la traza de una generación que una persona pidió, y solo quien la pidió puede aceptarla (`creada_por`, migración `012`).
- **La lectura de un radicado la gobierna solo el clearance**: `get_by_id` filtra `nivel_seguridad <= max_clearance` y la ruta no pide permiso (`document_repository.py:187-212`, `routers/documents.py:286-312`).
- **document-service tiene rutas internas** con `X-Internal-Token` (`routers/internal.py`), pero ninguna entrega a knowledge-service el registro ni los bytes de un anexo.
- **No hay evento** cuando se añade un anexo ni cuando cambian los datos de un radicado: el stream de documentos solo lleva `created`, `reclassified`, `annulled`, `purged` y los de envíos.
- **El gate de escritura emite propuestas firmadas, no guardadas.** Cada propuesta es un token HMAC con el acto dentro y vence a los **120 s** (`mcp_proposal_ttl_seconds`, `mcp-server/app/core/config.py:78`). Lo único que vive en memoria del proceso es el registro de `jti` consumidos (`app/services/proposals.py`). Al ejecutar, el gate **reautoriza** el permiso de la operación (`write_gate.ejecutar`).
- **La auditoría** tiene el canal como enum cerrado `{api, ui, mcp, system}` (`orpycamcp_common/audit.py:65`), con su `CHECK` en la base (`auth-service/migrations/global/003_audit_log_hardening.sql`). En el canal `mcp`, `audit.append` añade `correlacion` con `request_id` e `idempotency_key` (el `jti` de la propuesta) y nada más.
- **Identidad**: hay **un solo realm** de Keycloak (`orpycamcp`) para todas las entidades, con tres clientes (`api-gateway`, `auth-service`, `orpycamcp-frontend`; `infra/keycloak/realm-dev.json`). `ALLOWED_AZP` (`auth-service/app/core/config.py:69-77`) lista los clientes **cuyos tokens se aceptan como credencial de la API**. `scripts/init_tenant.py` crea schema, migraciones y registro, pero ningún cliente de Keycloak.
- **Plazo**: ni la ventanilla (`ventanilla_service.py:539`) ni el correo (`email_bandeja_service.py:504`) pasan `response_days`, así que sus radicados nacen sin vencimiento (`document_service.py:365-376`). No existe una operación para fijar o corregir el vencimiento.

## Decisión

### 1. La pasarela de inteligencia crece desde knowledge-service

Se propone que knowledge-service se convierta en la **pasarela de inteligencia**: el **único** componente de Docuboto que habla con un proveedor. Se elige knowledge-service porque ya tiene la bandera, el permiso, las barreras, la comprobación del nivel vivo y la experiencia de la ingesta por eventos. Un servicio nuevo tendría que copiarlas, y dos copias de una barrera divergen.

La pasarela tendrá un **puerto de proveedor** con dos implementaciones:

| Implementación | Qué es | Coste |
|---|---|---|
| `local` | El índice de hoy (pgvector) y la generación configurada por ADR-006. **Es el valor por defecto** y el respaldo de `ppi`. | Libre, sin proveedor |
| `ppi` | Un cliente del protocolo PPI hacia el proveedor interno de la instalación | El del proveedor |

El índice `local` se sigue alimentando siempre, como hoy (ADR-037): es el respaldo, y un respaldo atrasado no sirve.

### 2. Modelo de empuje: Docuboto manda, el proveedor no lee

- **Grupo consumidor propio.** La pasarela lee `orpycamcp.document.events` y `orpycamcp.workflow.events` con su propio grupo (`intelligence-gateway`), distinto de `knowledge-ingest`. Usa el mismo patrón de reclamación de la PEL y dead-letter de ADR-021.
- **Qué empuja.** Por cada radicado elegible empuja:
  - un **registro**: una lista cerrada de campos declarada en la pasarela (número, fecha de radicación, tipo E/S/I, tipo documental, dependencia, asunto, remitente, vencimiento oficial y estado). Los metadatos de las plantillas por tipo documental (ADR-007) salen solo si la entidad enciende su interruptor (§4), aplanados en el campo `metadata` de PPI 1.2;
  - sus **anexos**, como bytes o como localizador más SHA-256.
- **De dónde saca los anexos.** De rutas internas nuevas de document-service con `X-Internal-Token`: una entrega el registro y otra los bytes de un anexo. Ambas vuelven a comprobar el nivel y dejan constancia en el **historial de acceso del radicado**, con actor de sistema y finalidad «transmisión al proveedor de inteligencia». El funcionario que mire ese historial verá que el radicado salió, cuándo y hacia qué proveedor.
- **Qué hace que se borre en el proveedor.** Una reclasificación hacia un nivel que la entidad no habilita, una anulación, una purga o que el administrador deshabilite un nivel provocan una **orden de borrado** al proveedor. Una reclasificación entre niveles habilitados se empuja como versión nueva del registro. Una desclasificación aprobada (ADR-039) que deje el radicado en un nivel habilitado lo vuelve elegible y se empuja.
- **Lo que el proveedor nunca hace:** leer la base, los buckets ni el bus de Docuboto. La topología lo impide (ver §8): el proveedor no comparte red con ellos. La única vía de vuelta es la descarga de un anexo por localizador, que entra por la pasarela y pasa la barrera (§3).
- **Libro de envíos.** Cada empuje y cada borrado quedan en una tabla de solo inserción de la pasarela, con referencia, nivel enviado, SHA-256 del cuerpo, destino y resultado, por el mismo motivo que `webhook_entregas` (ADR-018 B4). Sobre ese libro corre una **reconciliación periódica**: lo enviado y no borrado cuyo nivel vivo ya no está habilitado se borra y alerta. Esa es la evidencia de «nada fuera de los niveles habilitados existe en el proveedor».
- **Eventos nuevos** (por el outbox de document-service, `042_eventos_salientes`): anexo añadido, datos del radicado cambiados y vencimiento cambiado. Sin ellos, el proveedor trabajaría sobre una foto vieja.

### 3. La barrera de nivel: un solo sitio

- **Una función y un punto de paso.** «¿Puede salir?» lo responde una función de `knowledge-service/app/core/clearance.py`, con dos condiciones: el nivel vivo está entre los **niveles habilitados** de la entidad (§4), y el envío no está suspendido, o sea, la huella de `capabilities` vigente es la que el administrador aceptó al habilitar ese nivel. Todo envío al proveedor pasa por **un** punto de paso de la pasarela: registros, anexos, borrados, el alcance de una consulta, las entradas de una tarea y las descargas por localizador. Ese punto relee el nivel vivo y los niveles habilitados **en el momento de enviar**, no los del evento, y niega si no se cumplen.
- **La política es de la entidad, no del código ni del proveedor.** `is_generatable` y `is_embeddable` siguen decidiendo para `local`. Para `ppi` decide solo la lista de niveles habilitados de la entidad, que su administrador cambia con fricción (§4). No hay un nivel que el código habilite por su cuenta.
- **Lo que el proveedor declara no amplía nada.** Su declaración `capabilities` puede listar más niveles aceptados. La pasarela no la lee para decidir qué sale; la usa para mostrarla, para que el administrador declare sobre ella y para detectar cambios (§4).
- **Lo vigilan guardianes** (detalle en el plan): ningún módulo fuera del punto de paso usa el cliente PPI, y un radicado de un nivel no habilitado no sale en ningún camino, incluida la carrera en la que se reclasifica entre el evento y el envío.
- **Lo que escribe el funcionario no es un registro.** El texto de una pregunta lo escribe el funcionario y sí sale. Por eso el panel «Preguntar» solo se ofrece sobre radicados de un nivel habilitado y avisa de que la pregunta y el radicado se transmiten, y por qué ruta.

### 4. Configuración por entidad, bajo ADR-037

- **Elección por entidad.** Una columna nueva `public.tenants.intelligence_provider` (`local` | `ppi`, por defecto `local`) la cambia el administrador de la entidad con `PUT /api/v1/config/intelligence` en tenant-service. El patrón es el de `PUT /config/asistente`: `USUA_PERM_ADMIN` nivel 3, `FOR UPDATE` y asiento en una transacción.
- **Pasar a `ppi` es un acto con fricción.** Exige:
  - la bandera de ADR-037 encendida;
  - la política de tratamiento publicada;
  - la `transmission_statement` del administrador. La declaración **nombra al proveedor y a sus subencargados** tal como vienen en la declaración `capabilities` firmada del proveedor; el nombre es un dato que viene del proveedor, no del código.

  El asiento `tenant.intelligence_changed` guarda la declaración, la huella SHA-256 de la `capabilities` vigente y la versión de la política. Volver a `local` es inmediato y sin fricción, igual que apagar la bandera.
- **Niveles habilitados.** Una columna nueva `public.tenants.intelligence_levels` (lista de niveles, por defecto solo PÚBLICA) dice qué niveles se transmiten. Pasar a `ppi` habilita PÚBLICA. Habilitar RESERVADA o CLASIFICADA es otro acto con fricción, nivel por nivel, por `PUT /api/v1/config/intelligence/levels` con el mismo patrón (`USUA_PERM_ADMIN` nivel 3, `FOR UPDATE` y asiento):
  - la declaración del administrador nombra, para ese nivel, la ruta de modelos (alojados o locales), los modelos y los subencargados tal como los da la `capabilities` firmada;
  - el asiento `tenant.intelligence_level_enabled` guarda la declaración, el nivel y la huella de `capabilities`.

  Deshabilitar un nivel es inmediato y sin fricción: corta los envíos de ese nivel y ordena al proveedor el borrado de lo ya transmitido de ese nivel.
- **Un cambio del proveedor suspende el envío.** Si la huella de `capabilities` cambia (otro subencargado, otro enrutamiento por nivel), la pasarela suspende todo envío de esa entidad hasta que su administrador vuelva a declarar cada nivel habilitado.
- **Metadatos de plantilla: un interruptor.** Una columna nueva `public.tenants.intelligence_send_metadata` (por defecto `false`) decide si salen los metadatos de las plantillas (ADR-007) de los radicados de niveles habilitados. Es todo o nada, sin elegir campos. Lo cambia el administrador con `PUT /api/v1/config/intelligence`, con el mismo patrón y el asiento `tenant.intelligence_metadata_changed`. Por defecto está apagado porque PPI es un protocolo neutro: otro proveedor puede no tener la confianza que tiene el proveedor interno. Apagarlo deja de enviar metadatos en adelante; lo ya enviado se reemplaza cuando cada radicado se vuelve a empujar.
- **Retirar los datos es un acto aparte.** Pasar a `local` corta las llamadas. Retirar lo ya transmitido es un acto explícito del administrador: pide al proveedor el borrado de todo lo de la entidad y deja asiento. Para que no se olvide, la pantalla de administración muestra en `local` cuántos radicados conserva el proveedor, y la confirmación del cambio ofrece retirarlos en el mismo acto.
- **Aprovisionamiento.** `scripts/init_tenant.py` gana un paso que crea en el realm un **cliente de servicio por entidad**, confidencial y solo `client_credentials`, sin flujo estándar ni concesión directa. Su secreto se guarda cifrado con la KEK (`orpycamcp_common.kek`, AAD atada a entidad y finalidad) y el paso imprime el par *(issuer, client_id)* que el proveedor registra para esa entidad. El mismo paso se puede correr para una entidad ya existente. **El cliente no entra en `ALLOWED_AZP`**: su token solo se presenta al proveedor, nunca a Docuboto, y añadirlo lo convertiría en una credencial válida para toda la API.
- **El funcionario en cada llamada.** Viaja como `on_behalf_of` **seudónimo**: un HMAC de su `auth_users.id` con una clave de la entidad. Su clearance real viaja recortada al nivel habilitado más alto de la entidad, y el proveedor solo le muestra lo de los niveles que caben en ella. Ni la cuenta de servicio ni el funcionario amplían lo que el proveedor puede ver.
- **Las llamadas del proveedor a Docuboto se autentican con HMAC por entidad, no con un cliente de Keycloak.** Son dos: entregar recomendaciones y descargar un anexo por localizador. Motivos:
  1. **Superficie.** Esas llamadas llegan a rutas internas de la pasarela que el api-gateway no enruta. Un token de Keycloak obligaría a meter el cliente del proveedor en `ALLOWED_AZP`, lo que lo vuelve credencial de toda la API, o a construir una segunda vía de validación. Con HMAC, el proveedor no tiene ninguna credencial de la API de Docuboto, y eso es lo que permite afirmar que ninguna escritura de Docuboto la hace su cuenta.
  2. **Un realm para todas las entidades.** Con Keycloak habría que mapear cliente a entidad y validar audiencia por entidad. El secreto HMAC es por entidad por construcción, y la AAD de la KEK impide que una fila copiada a otra entidad descifre.
  3. **Integridad del cuerpo.** El HMAC firma método, ruta, instante, nonce y SHA-256 del cuerpo. Un token portador no firma el cuerpo.
  4. **Patrón conocido.** Es el de los webhooks (ADR-018) y el de la identidad firmada con nonce de un solo uso (ADR-036, ADR-044): ventana de ±300 s, nonce reclamado en Redis, y dos claves vigentes durante una rotación.

  El costo es que el secreto es simétrico: Docuboto no puede probar ante un tercero que una recomendación vino del proveedor. Se acepta porque las recomendaciones son consultivas. La declaración `capabilities` sí va firmada con la clave asimétrica del proveedor, que la instalación fija en su configuración. El secreto se entrega al proveedor al declarar la conexión PPI, por el canal ya autenticado de la cuenta de servicio, y nunca se devuelve.

### 5. Buscar y preguntar se delegan, con re-comprobación viva y respaldo local

- **Delegación.** Con `ppi`, `/search` y `/rag` (y el panel «Preguntar» con alcance a un radicado y sus anexos) se delegan al proveedor con `on_behalf_of`, la clearance recortada y el alcance.
- **Cada acierto y cada cita se vuelven a comprobar en vivo**, con la misma regla que `GET` de un radicado:
  - el radicado existe;
  - no está anulado ni purgado;
  - su nivel vivo está habilitado y cabe en la clearance del funcionario.

  Lo que no pasa se descarta, deja traza y dispara un borrado en el proveedor. Una afirmación que se queda sin citas se retira, y una respuesta que se queda sin afirmaciones se presenta como `insufficient_evidence`. Nunca se muestra una afirmación sin cita.
- **Respaldo.** Si el proveedor no responde, excede su plazo, devuelve 5xx o tiene el envío suspendido (§4), la consulta la atiende `local`, y la UI dice «respuesta del índice local». La respuesta local se basa en asunto y remitente, no en el cuerpo ni en los anexos.
- **Plazos propuestos al proveedor** (2026-10-28, a acordar en su paso 4.18):

  | Camino | p95 | Plazo | Al vencer o fallar |
  |---|---|---|---|
  | Buscar y antecedentes | 2 s | 3 s | Búsqueda de texto completo local, con su etiqueta |
  | Preguntar | 15 s | 30 s | «Respuesta no disponible, inténtelo de nuevo», con los resultados de la búsqueda local para la misma pregunta |
  | Borrador (D3, `run` asíncrono) | sin objetivo interactivo | 5 min | No se crea la sugerencia; el funcionario puede volver a pedirla |

  Buscar queda dentro de RF-BUS-09 (3 s una búsqueda simple). Se pueden ajustar tras medir el piloto de D1.

### 6. La bandeja de sugerencias (`suggestions`)

- **Una tabla nueva**, no `kb_suggestions`. Cambia el sentido (la origina el proveedor, no una persona), el ciclo de vida y quién puede decidir. `kb_suggestions` sigue como traza de lo que genera `local`.
- **Clases cerradas**, con `CHECK` y una constante compartida con su guardián. La clase del proveedor se traduce en la pasarela:

| `suggestions.kind` | `kind` de PPI | Operación del catálogo al aceptar | Permiso propio de esa operación |
|---|---|---|---|
| `document_type` | `classify` | `tipificar_radicado` | `PERM_RADI` nivel 3 |
| `due_date` | `deadline` | operación nueva para fijar o corregir el vencimiento (plan D2) | permiso nuevo `PERM_DUE_DATE` (plan D2) |
| `routing` | `route` | `asignar_radicado` sin paso activo; `trasladar_radicado` con paso activo | `PERM_TRAMITAR` nivel 1, más la tenencia que esas operaciones ya exigen |
| `related_records` | `relate` | operación nueva `link_related_records`: un vínculo `related` entre los dos radicados, no la inclusión en un expediente (plan D2) | `PERM_RADI` nivel 2, y clearance sobre los dos radicados |
| `party_link` | `link_party` | conciliar la ficha provisional o vincular actores (`/api/v1/terceros/…`); hoy sin tool en el catálogo (plan D2) | `PERM_RADI` nivel 3 |
| `reply_draft` | `draft` | `crear_respuesta_vinculada` o `crear_borrador` (plan D3) | `PERM_RESPUESTA_VINCULADA` o `PERM_RADI` |

- **Idempotente por `(provider, ref)`**, con `UNIQUE`. Recibir dos veces la misma recomendación no crea dos sugerencias.
- **Estados**: `pending`, `accepted`, `rejected`, `superseded` y `expired`. Solo `pending` se mueve, y un trigger niega toda otra transición, como hace ADR-039 con el nivel.
  - `accepted`: la operación se ejecutó con éxito.
  - `rejected`: el funcionario la rechazó, con motivo de un catálogo cerrado.
  - `superseded`: llegó otra que la reemplaza (`supersedes`), o un funcionario fijó el valor oficial por otra vía.
  - `expired`: pasaron 30 días sin decidirla, el trámite se cerró, el radicado se anuló o su nivel subió. El plazo es el mismo para todas las clases.
- **Un «ninguna» explícito no crea sugerencia.** Cuando el proveedor dice que no tiene nada que recomendar para un radicado y una clase, con su motivo (`no_recommendation` en PPI), la pasarela lo guarda como estado de triage del radicado. Así se distingue «no procesado» de «procesado, sin sugerencia» y se puede medir la cobertura.
- **Se muestra con las reglas del radicado**: la sugerencia solo la ve quien puede ver el radicado (clearance en el `WHERE`), y solo con la bandera encendida y `PERM_CONOCIMIENTO`.
- **El valor oficial manda.** Una sugerencia de plazo o de tipo es una diferencia propuesta, nunca un hecho guardado.

### 7. Aceptar una sugerencia

- **Tres condiciones**, comprobadas al pedir la tarjeta y **otra vez al ejecutar** (el gate reautoriza):
  1. la bandera de ADR-037 encendida;
  2. `PERM_CONOCIMIENTO` nivel 2;
  3. el permiso propio de la operación de destino (tabla de §6).

  Rechazar exige lo mismo: rechazar también es decidir, y quien no puede ejecutar la operación no debería quitarla de la vista de quien sí puede.
- **Aceptar con cambios.** La tarjeta de aceptación abre con los valores sugeridos ya puestos y editables, con los mismos controles de las pantallas normales (selector de tipo documental, fecha, dependencia). La propuesta se acuña con los valores finales del funcionario. La decisión vuelve al proveedor como `accepted` con `applied_arguments`, y el asiento guarda el valor sugerido y el aplicado. No hace falta un estado aparte: una aceptación con valores distintos de los sugeridos es una aceptación corregida.
- **La propuesta se acuña al aceptar, no al recibir.** Como vence a los 120 s, la sugerencia guarda la recomendación y, cuando el funcionario pulsa «Aceptar», la pasarela pide a mcp-server una propuesta firmada (`write_gate.preparar`) de la operación del catálogo, a nombre de ese funcionario. La UI la muestra con `ProposalCard` (`frontend/src/lib/components/layout/ProposalCard.svelte`). El texto de la tarjeta lo redacta el catálogo, no el proveedor.
- **Se ejecuta como el funcionario.** Al confirmar, la operación corre con su identidad por el gate de siempre. No hay escritura con la cuenta del proveedor ni con un actor de sistema.
- **La auditoría lo ata.** El asiento de la operación lleva en `correlacion`, además de `request_id` e `idempotency_key`, el bloque `suggestion` con su identificador, el proveedor y los modelos. Llega en una cabecera que mcp-server firma con el mismo mecanismo que `X-Origen-Canal` (`identidad.firmar_origen`) y que el gateway solo reenvía si la firma verifica. **No se añade un canal**: el acto entra por el gate, el canal sigue siendo `mcp` y el `CHECK` no cambia. La pasarela asienta además `intelligence.suggestion_accepted` con el `jti` de la propuesta, de modo que los dos asientos se unen por él.
- **La decisión vuelve al proveedor.** Aceptada (con el valor aplicado), rechazada (con su motivo), superada o expirada, cada decisión sale por un outbox de la pasarela con reintentos y bajo la misma barrera y el mismo interruptor. El proveedor la usa para aprender.

### 8. Empaquetado: un perfil de Compose neutral

- **Dos proyectos Compose, nunca uno.** Docuboto y el proveedor se despliegan como dos proyectos en el mismo host (`docker compose -p docuboto`, y el del proveedor con su propio nombre), cada uno desde su repositorio y con su versión. Ninguno incluye ni extiende el compose del otro. Cada uno se publica, se despliega y se revierte por su cuenta, y el único contrato de compatibilidad entre los dos es el semver de PPI.
- **Una red externa `ppi`.** Se crea una vez por host, fuera de los dos proyectos, con `docker network create --internal ppi`. Del lado de Docuboto solo se conecta la pasarela (knowledge-service). Qué servicios conecta el proveedor es asunto suyo.
- **El perfil `intelligence` no arranca ningún servicio del proveedor.** Solo pasa la pasarela a su implementación `ppi`, la conecta a la red `ppi` y le da la URL del proveedor (`PPI_PROVIDER_URL`, sin valor por defecto). Ningún nombre de proveedor aparece en el compose.
- **Aislamiento por red.** El proveedor no se conecta a la red de Docuboto: no alcanza Postgres, Redis ni el almacén de objetos. Su salida a modelos alojados es asunto suyo, por su propia red y su propio proxy.
- **Lo que hay detrás es del proveedor.** Su imagen, su fijación por digest, sus trabajadores, su base y su pasarela de modelos son su propio proyecto Compose.
- **Sin el perfil, Docuboto funciona como hoy**, con `local`.

### 9. Relación con otros ADR

- **Enmienda a ADR-006.** Para esta capa, el proveedor se elige **por entidad**, no por despliegue. La generación de `local` sigue configurándose por despliegue como dice ADR-006, y su barrera dura (CC-03) sigue igual. Para `ppi`, CC-03 se sustituye por los niveles habilitados de la entidad (§3, §4): el proveedor interno es un encargado dentro de la instalación, y la ruta de modelos de cada nivel queda declarada y firmada en su `capabilities`.
- **ADR-037** gobierna todo: con la bandera apagada no hay consulta, ni sugerencia visible, ni aceptación, ni envío al proveedor. A diferencia de `local`, `ppi` no indexa con la bandera apagada: indexar en `ppi` es transmitir.
- **ADR-019.** El MCP sigue siendo la fachada «como el usuario». Esta decisión reusa su gate de escritura y no le añade tools de proveedor.
- **ADR-018.** Los webhooks quedan como están. Este diseño no los usa: son salida de eventos a terceros, sin barrera por registro, sin borrado y sin vuelta.
- **ADR-043 §5.** La lista cerrada de lecturas SQL entre servicios gana una fila: `radicados` (nivel vivo), dueño document, lector knowledge (barrera y re-comprobación). Hoy esa lectura ya existe y no está declarada.

### 10. Frontera de licencia

Pendiente de confirmación de asesoría jurídica:

- **Agregado.** Docuboto y el proveedor forman un **agregado** (AGPL-3.0 §5): programas separados en contenedores separados, que se hablan solo por protocolos de red documentados (PPI y, si se usa, MCP estándar). Sin imports, enlazado, plugins en proceso ni esquemas de base compartidos.
- **El protocolo es genérico** y su especificación vive en `docs/contratos/ppi/` como parte de Docuboto, bajo la misma **AGPL-3.0-or-later** que el resto del repositorio. Un proveedor implementa el protocolo en su propio código y habla con Docuboto por la red; no necesita incorporar esos archivos en su producto.
- **El código de Docuboto nunca nombra a un proveedor.** Un guardián lo vigila.
- **Docuboto ofrece el fuente de todo lo AGPL**, incluidos la pasarela y los paneles (AGPL §13).

## Alternativas consideradas

- **Proveedor externo por internet, con webhooks o un feed.** Descartado. Saca los datos de la instalación y depende de la red. ADR-018 no tiene barrera por registro, ni borrado, ni vuelta de decisiones, y un feed obligaría al proveedor a tirar de Docuboto.
- **Plugin en el proceso de Docuboto.** Descartado: formaría una obra combinada con código AGPL, y el proveedor no podría seguir siendo un producto aparte.
- **Base de datos compartida.** Descartada: el proveedor leería sin pasar por la clearance, sin barrera y sin dejar huella en el historial de acceso.
- **Solo MCP.** Descartado como vía única. MCP sirve a un agente que actúa como el usuario en una sesión, no a una ingesta de fondo con borrados y vuelta de decisiones. Queda como **complemento** para trabajo agéntico posterior.
- **El proveedor tira de Docuboto con una cuenta de servicio de la API.** Descartado: Docuboto dejaría de decidir registro a registro qué sale, y la cuenta tendría credencial sobre toda la API.
- **Reusar `kb_suggestions` como bandeja.** Descartado: es la traza de una generación pedida por una persona, aceptable solo por ella (§6).
- **Cliente de Keycloak para las llamadas del proveedor.** Descartado (§4): lo vuelve credencial de la API y no firma el cuerpo.
- **Un canal de auditoría nuevo (`intelligence`).** Descartado: el acto lo ejecuta la persona por el gate, y el canal dice por dónde entró. Lo que distingue el acto es `correlacion.suggestion`, y cambiar el `CHECK` global no aporta información nueva.

## Consecuencias

**Positivas:**

- Docuboto decide, registro a registro y en el momento de enviar, qué sale. La barrera vive en un solo sitio y la vigilan guardianes.
- El proveedor no tiene credencial de la API de Docuboto. Ninguna escritura la hace su cuenta, y cada aceptación es un acto de un funcionario con su asiento y su sugerencia.
- Docuboto funciona sin proveedor (`local`) y cae a `local` cuando el proveedor falla.
- El historial de acceso de un radicado muestra cuándo salió y hacia quién.
- El protocolo es neutral y genérico: otros proveedores pueden implementarlo hablando con Docuboto por la red.

**Negativas / límites:**

- Más piezas: una red, un perfil, un consumidor, un libro de envíos, una bandeja y un outbox de decisiones.
- **El respaldo `local` es pobre** (asunto y remitente) hasta que se mejore su ingesta. La UI debe decirlo.
- **El texto libre de una pregunta sale.** Se mitiga ofreciendo el panel solo sobre radicados de niveles habilitados y avisando, pero no se puede impedir que alguien escriba en la pregunta algo que no debería.
- **Con RESERVADA o CLASIFICADA habilitadas, ese contenido llega a los modelos que el proveedor declare**, alojados por terceros en planes sin retención, salvo que la ruta del nivel sea local. La falta de retención limita lo que el subencargado guarda, no lo que ve. Lo cubre la declaración por nivel del administrador y la lectura jurídica del responsable de producto (Ley 1712 de 2014, arts. 18 y 19), pendiente de confirmar con asesoría jurídica (pregunta 8).
- **Los datos personales de los radicados transmitidos salen** (remitente, contenido de los anexos). Lo cubre la declaración de transmisión de la entidad (Ley 1581; Decreto 1074 de 2015 [confirmar numerales]) y lo acota la lista cerrada de campos, pero no desaparece.
- **El secreto HMAC es simétrico** (§4).
- **Cambio compatible por defecto**: todas las entidades quedan en `local` hasta que su administrador elija `ppi`.

## Residuos declarados

- **El borrado en el proveedor es asíncrono.** Entre una reclasificación hacia arriba y el borrado confirmado hay una ventana. La re-comprobación viva (§5) impide que lo reclasificado se muestre en ella, y la reconciliación (§2) da la evidencia.
- **El consumo de `jti` vive en el proceso** (ya declarado en `proposals.py`). Una aceptación duplicada contra dos réplicas dentro de los 120 s depende de la idempotencia de la operación de destino. La bandeja la acota: una sugerencia solo pasa una vez de `pending` a `accepted`.
- **La lectura SQL de `radicados` desde knowledge** se declara en ADR-043 §5 al aceptar este ADR.

## Preguntas abiertas para el responsable de producto

1. ~~**Plazo de vida de una sugerencia `pending`.**~~ Resuelta el 2026-10-28: una sola regla para todas las clases. Pasa a `expired` a los 30 días, o antes si el trámite se cierra. Las reglas de `superseded` y de `expired` por anulación o subida de nivel siguen igual.
2. ~~**«Aceptar con cambios».**~~ Resuelta el 2026-10-28: sí, dentro de la misma aceptación (§7). La decisión vuelve con `applied_arguments`, que PPI ya admite.
3. ~~**Ampliar un plazo propio.**~~ Resuelta el 2026-10-28: «no el propio». Cualquiera con `PERM_DUE_DATE` amplía un vencimiento con motivo, salvo el funcionario que tiene asignado el radicado; acortar siempre se permite. La ampliación no pasa del doble del término inicial (Ley 1755 de 2015, art. 14 [confirmar]). Sin segunda persona (plan D2).
4. ~~**Qué significa aceptar un antecedente.**~~ Resuelta el 2026-10-28: crea un vínculo `related` entre los dos radicados (tabla nueva, plan D2). Una sugerencia nunca incluye un radicado en un expediente: eso sigue siendo un acto manual del funcionario, que puede hacer desde la sección «Radicados relacionados». Los vínculos confirmados viajan al proveedor en `record.links` de PPI.
5. ~~**Retirar los datos al pasar a `local`.**~~ Resuelta el 2026-10-28: es un acto aparte del administrador (§4). Dos salvaguardas: mientras la entidad está en `local`, la pantalla de administración muestra cuántos radicados conserva el proveedor (inventario PPI), y la confirmación del cambio a `local` ofrece «retirar también los datos» como casilla.
6. ~~**Metadatos JSONB.**~~ Resuelta el 2026-10-28: un interruptor por entidad, `intelligence_send_metadata`, apagado por defecto (§4). Encendido, salen todos los metadatos de plantilla de los radicados de niveles habilitados, en el campo `metadata` de PPI 1.2.
7. ~~**Imagen del perfil.**~~ Resuelta el 2026-10-28 (§8): el perfil no arranca ningún servicio del proveedor; cada programa es su propio proyecto Compose y se encuentran en la red externa `ppi`, como fija el ADR 0009a del proveedor (decisión 12).
8. **Base jurídica de los niveles altos.** ¿Permiten la Ley 1712 de 2014 (arts. 18 y 19) y el Decreto 103 de 2015 que un encargado contratado, y sus subencargados alojados, procesen información RESERVADA y CLASIFICADA? ¿Qué exige la Ley 1581 de 2012 (y el Decreto 1074 de 2015) para la transmisión internacional? La revisión de 2026-10-27 parte de que sí se permite en planes sin retención; si asesoría jurídica dice otra cosa, el nivel pasa a ruta local o se deshabilita, sin cambiar el diseño. **Plan de arranque (2026-10-28):** el piloto empieza solo con PÚBLICA; la consulta a asesoría jurídica corre en paralelo, y RESERVADA o CLASIFICADA se habilitan después, nivel por nivel, cuando firmen asesoría jurídica y la revisión de seguridad de CR-005 C10 del proveedor. Ninguna fase espera por esto.
9. ~~**Borrado por nivel.**~~ Resuelta el 2026-10-28: la pasarela exige la versión vigente de PPI (1.2) y deshabilita un nivel con `POST /v1/ingest/purge` y `levels`. No hay proveedores desplegados que solo hablen 1.0, así que no se construye el borrado radicado por radicado.
