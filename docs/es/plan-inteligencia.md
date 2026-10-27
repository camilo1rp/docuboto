# Plan de inteligencia (Docuboto 2.0)

> **Estado: planeado.** Nada de lo que describe esta página está construido. La decisión
> de arquitectura es [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md)
> (Propuesto) y el protocolo es PPI v1, en borrador, en `docs/contratos/ppi/`.
> Actualizado al 2026-10-27.

## Para qué

Docuboto 2.0 incorpora un **proveedor interno de inteligencia**: un programa aparte, con
su propia licencia, que corre en sus propios contenedores dentro de la instalación y que
habla con Docuboto solo por PPI. Docuboto sigue siendo el sistema de registro y funciona
entero sin proveedor.

El plan tiene tres fases, que coinciden con las del primer proveedor para poder medir
ambos lados contra los mismos hitos:

| Fase | Qué gana el funcionario | Hito del proveedor |
|---|---|---|
| **D1 — Camino de lectura** | Buscar y preguntar sobre el cuerpo y los anexos de los radicados, con citas | Hito D1 |
| **D2 — Triage de entrada** | Sugerencias de tipificación, plazo, reparto, antecedentes y remitente, que acepta o rechaza en la bandeja | Hito D2 |
| **D3 — Borradores de respuesta** | Un borrador de respuesta citado, que se convierte en borrador de Docuboto a su nombre | Sin hito propio del proveedor; criterios de Docuboto |

## Reglas que valen para las tres fases

1. **Solo sale lo PÚBLICO.** Un radicado RESERVADA o CLASIFICADA no llega nunca al
   proveedor, por ningún camino (CC-03, sin excepción). Lo decide una sola función y lo
   hace cumplir un solo punto de paso, que relee el nivel vivo al enviar.
2. **Con el interruptor apagado no hay llamadas.** Si la bandera de ADR-037 está apagada
   o la entidad está en `local`, Docuboto no hace ninguna llamada al proveedor.
3. **Lo aceptado lo ejecuta el funcionario.** Cada aceptación pasa por el gate de
   escritura con la identidad de la persona y deja su asiento con la sugerencia en
   `correlacion`. El proveedor no tiene credencial de la API de Docuboto.
4. **El código no nombra a ningún proveedor.** El nombre del proveedor es un dato que
   llega en su declaración `capabilities` firmada; la imagen se configura por variable.

Cada regla tiene su guardián (ver [Guardianes transversales](#guardianes-transversales)).

---

## Fase D1 — Camino de lectura

**Objetivo.** En la bandeja y en `/busqueda`, el funcionario pregunta y busca sobre el
cuerpo y los anexos de los radicados PÚBLICOS. Cada afirmación lleva cita, o la
respuesta es «evidencia insuficiente». Si el proveedor no está, responde el índice local.

### Cambios por servicio

| Servicio | Cambio |
|---|---|
| knowledge-service (la pasarela) | **Puerto de proveedor** con dos implementaciones: `local` (envoltorio de lo actual) y `ppi` (cliente HTTP de PPI v1). |
| | **Punto de paso único** de todo envío al proveedor, con la función «¿puede salir?» (`nivel_vivo == PUBLICA`) en `app/core/clearance.py`. Ningún otro módulo usa el cliente PPI. |
| | **Consumidor `inteligencia-pasarela`** sobre `orpycamcp.document.events` y `orpycamcp.workflow.events`, con reclamación de la PEL y dead-letter (ADR-021). Empuja altas y cambios, y ordena borrados por reclasificación hacia arriba, anulación y purga. |
| | **Traducción a PPI**: radicado → `record` con una lista cerrada de campos (número, fecha, tipo E/S/I, tipo documental, dependencia, asunto, remitente, vencimiento oficial, estado), y anexo → `attachment` por localizador. Los metadatos JSONB no salen. |
| | **Libro de envíos** de solo inserción y **reconciliación periódica** contra el nivel vivo y contra el inventario PPI (`GET /v1/ingest/inventory`). |
| | **Carga inicial y reanudación**: al pasar a `ppi`, o al volver a encender la bandera, empuja lo PÚBLICO pendiente por lotes reanudables con cursor. |
| | **Conexión PPI**: declara niveles, vocabularios, URL de retorno y claves (`PUT /v1/connection`); lee y verifica `capabilities` con la clave fijada en configuración; guarda su huella y suspende el envío si cambia. |
| | **Ruta interna de retorno `content`**, firmada con HMAC por entidad (ventana de 300 s, nonce de un solo uso en Redis): vuelve a pasar la barrera y sirve los bytes desde document-service. No la enruta el gateway. |
| | **Delegación de `/search` y `/rag`**, con alcance opcional a un radicado y sus anexos. Re-comprobación viva de cada acierto y cada cita, y respaldo `local` con su etiqueta. Las tres tools de conocimiento del asistente (E21) pasan por aquí y heredan todo esto sin cambios en mcp-server. |
| | **`on_behalf_of` seudónimo** (HMAC del `auth_users.id` con clave de la entidad) y clearance recortada a PÚBLICA. |
| | **Métricas** sin etiqueta de tenant (ADR-041): envíos, rechazos de barrera, respaldos activados y suspensiones. |
| document-service | **Dos rutas internas** (`X-Internal-Token`): el registro de un radicado y los bytes de un anexo (mediados por storage-service, como D-02). Ambas comprueban `nivel_seguridad = 1` en el `WHERE` y dejan constancia en el historial de acceso del radicado («transmitido al proveedor de inteligencia»). |
| | **Eventos nuevos por el outbox** (`042_eventos_salientes`): anexo añadido y datos del radicado cambiados. |
| tenant-service | **`GET` y `PUT /api/v1/config/inteligencia`** (`USUA_PERM_ADMIN` nivel 3, `FOR UPDATE` y asiento `tenant.inteligencia_cambiada` en una transacción). Pasar a `ppi` exige bandera encendida, política publicada y `declaracion_transmision` con el proveedor y sus subencargados tal como los declara `capabilities`. |
| | **`POST /api/v1/config/inteligencia/retirar`**: pide al proveedor el borrado de todo lo de la entidad (`POST /v1/ingest/purge`) y deja asiento. |
| auth-service | `/auth/me` publica `inteligencia_proveedor` junto a `asistente_habilitado`, para que la UI decida qué mostrar. `ALLOWED_AZP` **no cambia**. |
| `scripts/init_tenant.py` | **Paso nuevo e idempotente** que crea el cliente de servicio de la entidad en el realm (confidencial, solo `client_credentials`), guarda su secreto cifrado con la KEK e imprime el par *(issuer, client_id)* que registra el proveedor. Se puede correr para una entidad existente. |
| api-gateway | Enruta `/api/v1/config/inteligencia*`. **No** enruta ninguna ruta interna de la pasarela. |
| frontend | **Panel «Preguntar»** en el drawer de detalle de la bandeja (`frontend/src/routes/(app)/bandeja/+page.svelte`): solo con bandera, `PERM_CONOCIMIENTO` y radicado PÚBLICO. Muestra las citas con enlace al anexo y la página, dice cuándo responde el índice local y avisa de que la pregunta se transmite. |
| | **Pantalla de administración** «Inteligencia»: proveedor, subencargados y enrutamiento según `capabilities`, declaración, estado (suspendido o activo) y retiro de datos. |
| infra | **Perfil de Compose `inteligencia`** en `docker-compose.yml` e `infra/docker-compose.prod.yml`: red `internal: true` compartida solo por knowledge-service y `proveedor-inteligencia`, imagen `INTELIGENCIA_IMAGEN` sin valor por defecto (por digest en producción, ADR-033) y sin puertos. `scripts/preflight-prod.sh` rechaza la imagen sin digest. |

### Permisos nuevos

Ninguno. Consultar sigue siendo `PERM_CONOCIMIENTO` nivel 1 y configurar es
`USUA_PERM_ADMIN` nivel 3, como en ADR-037.

### Migraciones

| Servicio | Migración | Contenido |
|---|---|---|
| tenant-service (global) | siguiente número libre | `public.tenants.inteligencia_proveedor TEXT NOT NULL DEFAULT 'local'` con `CHECK (… IN ('local','ppi'))`. Todas las entidades quedan en `local`. |
| knowledge-service (tenant) | siguiente número libre | `inteligencia_envios`: libro de envíos de solo inserción, con trigger que niega `UPDATE` y `DELETE`. |
| knowledge-service (tenant) | siguiente número libre | `inteligencia_credenciales`: secretos cifrados con la KEK y AAD de entidad y finalidad (cliente de servicio, claves HMAC de retorno, clave de seudónimo). |
| knowledge-service (tenant) | siguiente número libre | `inteligencia_estado`: huella de `capabilities` aceptada, suspensión y cursor de la carga inicial. |

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
  nonce repetido da 401, y un localizador de algo no PÚBLICO da 410.
- **El cliente de la entidad no es credencial de la API.** El cliente creado por
  `init_tenant.py` no aparece en `ALLOWED_AZP` y solo tiene cuenta de servicio.
- **El perfil aísla al proveedor.** Un guardián de compose comprueba que
  `proveedor-inteligencia` solo está en la red `inteligencia`, que esa red es
  `internal: true`, que no publica puertos y que su imagen viene de variable.
- **La especificación PPI cumple su contrato.** Los ejemplos de
  `docs/contratos/ppi/examples/manifest.json` dan el resultado declarado contra su
  esquema. La carpeta solo tiene `.md`, `.json` y `.yaml`, bajo la AGPL del
  repositorio. (Requiere añadir `jsonschema` a las dependencias de prueba de
  `shared`.)
- `test_conocimiento_mira_la_bandera.py` y `test_toda_ruta_lleva_gate` se extienden a las
  rutas nuevas.

### Criterios de éxito (equivalen al hito D1 del proveedor)

- [ ] En una instalación de staging de Docuboto 2.0, los funcionarios buscan y preguntan
      desde la bandeja y `/busqueda` sobre el cuerpo y los anexos. Cada respuesta va
      citada o es «evidencia insuficiente», y cada cita mostrada pasó la re-comprobación
      viva.
- [ ] Con el proveedor detenido, Docuboto sigue funcionando: buscar y preguntar caen a
      `local` con su etiqueta, y radicar, tramitar y firmar no se enteran. Al volver, el
      consumidor recupera lo pendiente sin pérdida.
- [ ] La reconciliación queda registrada: en el proveedor no existe nada por encima de
      PÚBLICA (inventario PPI contra nivel vivo), y el libro de envíos de Docuboto no tiene
      ningún envío que no fuera PÚBLICA.
- [ ] Una reclasificación hacia arriba, una anulación y una purga terminan en un borrado
      verificado en el inventario del proveedor.
- [ ] La instalación corre desde una versión publicada, con la imagen del proveedor fijada
      por digest.
- [ ] Con la bandera apagada o la entidad en `local`, el registro de acceso del proveedor
      no muestra ninguna llamada de esa entidad durante la ventana de prueba.
- [ ] Firma de una persona responsable, registrada en el changelog.

---

## Fase D2 — Triage de entrada

**Objetivo.** Lo que entra PÚBLICO por ventanilla y por correo recibe sugerencias de
tipificación, plazo, reparto, antecedentes y remitente. El funcionario las acepta o las
rechaza en la bandeja, y cada aceptación es un acto suyo, auditado con su sugerencia.

Esta fase incluye **arreglos de plazo que hacen falta con o sin proveedor**: hoy los
radicados de ventanilla y de correo nacen sin vencimiento, porque nadie pasa
`response_days` (`document_service.py:365-376`, `ventanilla_service.py:539`,
`email_bandeja_service.py:504`).

### Cambios por servicio

| Servicio | Cambio |
|---|---|
| knowledge-service | **Ruta interna de retorno `recommend`** (HMAC por entidad). Guarda en `sugerencias` de forma idempotente por `(proveedor, ref)`. Rechaza la clase desconocida, el código fuera de vocabulario, el radicado que ya no es PÚBLICO o la versión vieja. Registra los «ninguna» explícitos con su motivo para medir cobertura. |
| | **Rutas de la bandeja**: listar por radicado (`PERM_CONOCIMIENTO` nivel 1 y clearance en el `WHERE`), `aceptar` y `rechazar` (bandera, `PERM_CONOCIMIENTO` nivel 2 y permiso propio de la operación de destino; rechazar lleva motivo de catálogo cerrado). |
| | **Reglas de vida**: `superada` cuando llega un reemplazo o cambia el valor oficial; `expirada` por plazo, cierre, anulación o subida de nivel. |
| | **Outbox de decisiones** hacia `POST /v1/decisions`, con reintentos, bajo la barrera y el interruptor. |
| | **Vocabularios**: tipos documentales activos (archive) y dependencias activas por código (tenant-service), leídos por rutas internas y redeclarados en la conexión PPI cuando cambian. |
| mcp-server | **Ruta interna para acuñar una propuesta** a nombre del funcionario (`X-Internal-Token` e identidad firmada de la persona). La llama la pasarela al aceptar, y el token lleva dentro la referencia de la sugerencia. |
| | **Al ejecutar** esa propuesta: cabecera firmada con la sugerencia (mismo mecanismo que `X-Origen-Canal`) y aviso del resultado a la pasarela. |
| | **Tools nuevas** en el catálogo: fijar o corregir el vencimiento, y conciliar la ficha provisional o vincular actores (`/api/v1/terceros/…`). |
| shared (`orpycamcp_common`) | **Firma y verificación** de la cabecera de sugerencia (`identidad`), `ContextVar` de sugerencia (`request_log`) y bloque `correlacion.sugerencia` con identificador, proveedor y modelos en `audit.append`. El canal sigue siendo `mcp` y el `CHECK` de `canal` no cambia. |
| api-gateway | Reenvía la cabecera de sugerencia **solo** si su firma verifica, igual que `X-Origen-Canal`. Enruta `/api/v1/knowledge/sugerencias*`. |
| document-service | **Plazo por defecto**: si un radicado de Entrada llega sin `response_days`, toma el plazo por defecto de su tipo documental. Aplica a ventanilla, correo manual y automático, y lotes. El importador conserva el vencimiento que traiga el paquete. |
| | **El sondeo de correo construye `DocumentService` con cliente de tenant-service.** Hoy lo hace con `None` (`email_sondeo.py:216`), y calcular el plazo lanzaría `RuntimeError`. |
| | **Operación auditada para fijar o corregir el vencimiento** (`PUT /api/v1/documents/{id}/vencimiento`, con fecha o días hábiles, y motivo). Lleva `PERM_VENCIMIENTO` nivel 3, clearance en el `WHERE`, `FOR UPDATE` y asiento con `antes`, `despues`, `motivo` y `origen` (manual o sugerencia). Emite por el outbox el evento de vencimiento cambiado. |
| archive-service | `tipos_documentales.plazo_respuesta_dias` (días hábiles, opcional) en la administración de tipos documentales. Es configuración de cada entidad, no una ley escrita en el código: el término legal (Ley 1755 de 2015, art. 14 [confirmar]) lo pone la entidad. |
| workflow-service | Consume el evento de vencimiento cambiado y alinea `due_at` del paso activo. |
| | Corrige los dos hallazgos de [endurecimiento](#endurecimiento-encontrado-al-leer-el-codigo) que afectan a esta fase: `_publish` y la regla en el asiento del reparto. |
| auth-service | Permiso nuevo `PERM_VENCIMIENTO`, con su concesión inicial (pregunta abierta). |
| frontend | **Panel de sugerencias** en el drawer de la bandeja: por clase, con evidencia citada; «Aceptar» abre `ProposalCard` y «Rechazar» pide motivo. |
| | **Insignias en la lista de la bandeja**: sugerencias pendientes por radicado. |
| | **Campo de vencimiento** editable con motivo, y **plazo por defecto** en la administración de tipos documentales. |

### Permisos nuevos

- **`PERM_VENCIMIENTO`** (nivel 3: fijar o corregir el vencimiento). Va aparte de
  `PERM_RADI` porque ampliar el propio plazo es un conflicto de interés. Queda abierto si
  ampliarlo exige además una segunda persona (ADR-045, pregunta 3).
- Decidir una sugerencia **no** es un permiso nuevo: es `PERM_CONOCIMIENTO` nivel 2
  (ADR-037) más el permiso propio de la operación.

### Migraciones

| Servicio | Contenido |
|---|---|
| knowledge-service (tenant) | `sugerencias`: clases y estados con `CHECK`, `UNIQUE (proveedor, ref)`, trigger de transiciones (solo desde `pendiente`, terminales inmutables) e índices por radicado y estado. |
| knowledge-service (tenant) | `sugerencias_decisiones_salientes` (outbox) y `inteligencia_triage` (último «ninguna» por radicado y clase, con su motivo). |
| archive-service (tenant) | `tipos_documentales.plazo_respuesta_dias INT NULL CHECK (… BETWEEN 1 AND 3650)`. |
| auth-service (tenant) | Alta de `PERM_VENCIMIENTO`, con la concesión inicial que se decida. |

### Eventos

- **Nuevo**: vencimiento del radicado cambiado (document-service, por outbox). Lo
  consumen workflow-service y la pasarela.
- La pasarela usa «datos del radicado cambiados» (D1) para marcar `superada` una
  sugerencia cuyo valor oficial fijó una persona.

### Guardianes de la fase

- **Toda aceptación queda auditada con su sugerencia** (transversal; se completa aquí).
- **Aceptar exige las tres condiciones**, al acuñar y otra vez al ejecutar. La matriz
  cubre que falte cada una, incluido retirar el permiso entre acuñar y confirmar.
- **La cabecera de sugerencia no se puede forjar**: el gateway la descarta sin firma
  válida.
- **Las clases son cerradas y coinciden** en el `CHECK`, la constante, la UI y la
  traducción a `kind` de PPI.
- **Transiciones**: solo desde `pendiente`; recibir dos veces el mismo `(proveedor, ref)`
  no duplica.
- **Ningún radicado de Entrada con plazo por defecto nace sin vencimiento**, por ventanilla,
  por correo (manual y automático) ni por lote.
- **Corregir el vencimiento deja asiento** con `antes`, `despues` y `motivo`, y respeta el
  clearance.
- **El modo automático del correo radica**: una prueba que hoy fallaría (ver
  endurecimiento).

### Criterios de éxito (equivalen al hito D2 del proveedor)

- [ ] En una instalación de Docuboto 2.0, lo que entra PÚBLICO por ventanilla y por correo
      recibe sugerencias de tipificación, plazo, reparto y antecedentes dentro del p95
      acordado, visibles con su insignia en la bandeja.
- [ ] Los funcionarios aceptan o rechazan en Docuboto. Cada aceptación aparece en la cadena
      de `audit_log` con la persona como actor y `correlacion.sugerencia` con
      identificador, proveedor y modelo. Lo comprueba `verify_audit_chains` más una
      consulta.
- [ ] La auditoría de Docuboto no muestra ningún cambio de estado hecho por el proveedor:
      no hay actor fuera de `auth_users` y del sistema, ni aceptación sin sugerencia
      correlada.
- [ ] Sobre el conjunto de oro de la entidad, la exactitud de tipo, plazo exacto y
      dependencia (top-1 y top-3) alcanza los umbrales acordados antes de la corrida. El
      conjunto sale del historial PÚBLICO ya transmitido (radicados cerrados), sin una
      exportación aparte.
- [ ] Al menos el 95 % de lo que entra PÚBLICO recibe sugerencias o un «ninguna» explícito
      con motivo, medido sobre `inteligencia_triage`.
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
| knowledge-service | **Ruta «pedir borrador»**: bandera, `PERM_CONOCIMIENTO` nivel 1 y radicado PÚBLICO. Lanza `POST /v1/runs` (`draft_reply`) bajo la barrera, con el radicado y sus anexos como alcance. |
| | El resultado llega como recomendación `draft` y se guarda como sugerencia `borrador_respuesta`. |
| | Al aceptar, acuña la propuesta de `crear_borrador` con `radicado_padre_id` y el cuerpo sugerido. `crear_respuesta_vinculada` queda como opción, porque consume un número de radicado (pregunta abierta). |
| mcp-server | La tool `crear_borrador` expone `radicado_padre_id`. `BorradorCreate` ya lo admite (`schemas/borrador.py`), pero la tool no lo ofrece hoy. |
| frontend | **Botón «Pedir borrador»** y **vista del borrador sugerido** con sus citas. El cuerpo se pinta como texto, nunca como HTML: lo redactó un modelo a partir de texto de un tercero. Al aceptar se abre `ProposalCard`, y el borrador se abre en el editor de borradores. |

### Permisos nuevos, migraciones y eventos

- **Permisos**: ninguno. Crear el borrador exige el permiso propio de `crear_borrador`
  (`PERM_RADI`) o de `crear_respuesta_vinculada` (`PERM_RESPUESTA_VINCULADA`).
- **Migraciones**: ninguna. La clase `borrador_respuesta` ya está en el `CHECK` desde D2.
- **Eventos**: ninguno nuevo.

### Guardianes de la fase

- **Aceptar un borrador crea un borrador**, no un radicado, a nombre del funcionario y con
  la sugerencia en `correlacion`.
- **El cuerpo sugerido se muestra como texto**: ningún `{@html}` en los componentes de
  sugerencias.
- **`run` solo sobre lo PÚBLICO** y solo con el interruptor encendido.

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
| **Nada por encima de PÚBLICA llega a un proveedor** | Ningún camino envía un radicado RESERVADA o CLASIFICADA: registro, anexo, alcance de consulta, tarea `run`, descarga por localizador y outbox de decisiones. | Transporte falso que falla ante cualquier petición con nivel no PÚBLICO. Incluye la carrera de una reclasificación entre el evento y el envío. Un control AST asegura que solo el punto de paso usa el cliente PPI. |
| **Con el interruptor apagado no hay llamadas ni egreso** | Con la bandera apagada, o la entidad en `local`, ni el consumidor, ni buscar, ni preguntar, ni la carga inicial, ni la reconciliación, ni el outbox de decisiones, ni `run` llaman al proveedor. | Transporte falso que falla ante cualquier petición. En compose, el proveedor solo está en una red `internal: true`. |
| **Toda aceptación queda auditada con su sugerencia** | Cada sugerencia `aceptada` tiene un asiento de la operación de destino con la persona como actor y `correlacion.sugerencia` (identificador, proveedor, modelos), unido por el `jti` al asiento `inteligencia.sugerencia_aceptada` de la pasarela. | Prueba de integración del recorrido completo, más un control que recorre las sugerencias aceptadas de un tenant de prueba y busca su asiento. |

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

1. **D1**, en este orden:
   1. migraciones y aprovisionamiento;
   2. punto de paso y barrera, con sus guardianes **antes** de cualquier envío real;
   3. consumidor y carga inicial;
   4. delegación con respaldo;
   5. panel «Preguntar», configuración y perfil de Compose.
2. **Los arreglos de plazo de D2** (plazo por defecto, operación de vencimiento, sondeo
   de correo) no dependen del proveedor y pueden adelantarse.
3. **El resto de D2** necesita D1 y, del lado del proveedor, la entrega de
   recomendaciones.
4. **D3** necesita la bandeja de D2 y, del lado del proveedor, la tarea `draft_reply`
   por `run`.

## Preguntas abiertas

Las del responsable de producto están en
[ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md#preguntas-abiertas-para-el-responsable-de-producto).
Este plan añade cuatro:

1. **Concesión inicial de `PERM_VENCIMIENTO`**: ¿a los grupos que ya tienen `PERM_RADI`
   nivel 3, o a ninguno, para que el administrador la reparta a conciencia como en
   ADR-037?
2. **p95 de D2**: ¿cuánto puede tardar una sugerencia desde la radicación? Lo acuerdan la
   entidad y el proveedor antes del hito.
3. **Umbrales de exactitud de D2**, acordados con la entidad antes de la corrida.
4. **Plazo de retención del libro de envíos**: es de solo inserción, como `webhook_entregas`
   (ADR-018), y comparte su pendiente de retención y purga.
