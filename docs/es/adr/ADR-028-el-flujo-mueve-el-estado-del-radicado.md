# ADR-028: El flujo mueve el estado del radicado

**Estado**: Aceptado
**Fecha**: 2026-10-02
**Autores**: Giampiero (mantenedor principal); diseño de `orfeo-architect`
**Relacionados**: [ADR-009](ADR-009-historial-flujos-eventos.md) (historial de flujos como eventos), [ADR-010](ADR-010-libreria-compartida.md) (auditoría inmutable), E05 (flujos), E01 (radicación), RF-SEG-08 (no-read-up); continuado por [ADR-029](ADR-029-ubicacion-actual-del-radicado.md) (la ubicación actual se escribe en el mismo `UPDATE` que el estado)

---

## Contexto

Había **dos máquinas de estado que no se hablaban**. Repartir y tramitar avanzan los
**pasos** (`flow_steps`); `radicados.status` solo cambiaba si alguien pulsaba a mano
«Distribuir» / «En trámite» (un `PATCH /documents/{id}` con `status`). El resultado, visto
al recorrer la aplicación con el navegador (2026-10-01): un radicado repartido seguía en
`registered`; el panel dejó de contar desde `distributed`/`in_progress` porque el flujo
nunca los escribía; y seguían colgando de esos estados el botón «Devolver» de la bandeja y
los filtros por estado de la búsqueda.

En Orfeo, repartir **es** un cambio de estado. Había dos salidas y no eran equivalentes:
que el flujo mueva el estado del radicado, o retirar `distributed`/`in_progress` del
radicado y derivar el «en qué va» de sus pasos. Se eligió la primera: el estado del
radicado es una propiedad que otras pantallas, la búsqueda y el MCP consultan sin
reconstruirla desde los pasos, y es lo que el funcionario de Orfeo ya espera.

## Decisión

1. **El estado lo escribe el workflow-service, en la MISMA transacción que el paso.**
   Excepción única: `anulado` lo sigue escribiendo la anulación (document-service).
2. **Una sola función pura** (`workflow-service/app/services/estado_radicado.py`):

   ```python
   def derivar_estado_radicado(actual, paso_activo, *, tiene_pasos, finalizar=False) -> str:
       if actual in ("closed", "anulado"): return actual
       if finalizar: return "closed"
       if paso_activo == "pending": return "distributed"
       if paso_activo == "in_progress": return "in_progress"
       return "distributed" if (actual == "registered" and tiene_pasos) else actual
   ```

   El backfill SQL es su gemelo literal y la prueba de integración compara cada fila con
   ella. Una regla en dos sitios que no se vigilan entre sí deja de ser una regla.
3. **Cierre solo por «Finalizar trámite» explícito.** `POST /workflows/{radicado_id}/finalizar`
   (`PERM_TRAMITAR` **nivel 3**, solo el poseedor del paso activo —o, sin paso activo, quien
   poseía el último paso completado—, `comentario` y `motivo_cierre` obligatorios,
   `flow_events.tipo_tx = 'finalizar'`) cierra el paso activo como `completed` y el radicado
   pasa a `closed`. **No** es un `transaction_type`: un tipo del catálogo del tenant lo
   puede sembrar o editar cualquiera con acceso a la base, y el acto irreversible no puede
   vivir en una tabla configurable.
4. **`in_progress` solo por `marcar_leido` del poseedor.** Para eso se cierra un hueco que
   ya existía: `execute_transaction` no comprobaba tenencia, así que cualquiera con
   `PERM_TRAMITAR` podía marcar como leído —y mover el estado de— un trámite ajeno. Nueva
   columna `transaction_types.requires_holder` (`true` para `marcar_leido`). **Poseedor**
   = `assigned_to = actor`, o (`assigned_to IS NULL` y el actor pertenece a la dependencia
   del paso). Es más estrecho que el alcance de bandeja (`_own_scope_condition`: un
   miembro de la dependencia **ve** lo asignado a un colega, pero no lo **posee**); ambos
   comparten el subquery de «mis dependencias» para no divergir. Un no-poseedor recibe el
   mismo `404 no_active_step` que «no hay paso» (sin oráculo).
5. **`closed` y `anulado` son terminales.** Todo acto de trámite que mueva el paso
   (`assign`, `transfer`, `complete`, `devolver`, `rollback`, `finalizar` y las
   transacciones con `target_step_status`) sobre un radicado terminal responde
   `409 radicado_terminal` y **revierte todo**. `informar`/`agendar` siguen permitidas.
6. **Atomicidad y orden.** Cada acto sincroniza *después* de mutar el paso y *antes* de
   `audit.append`, dentro de su `conn.transaction()`: si la auditoría falla, no queda ni
   paso ni estado. `lock_radicado` (advisory por radicado) se añadió a `execute_transaction`,
   `complete_step`, `devolver`, `rollback` y `finalizar`. El cambio viaja en el payload del
   asiento existente (`"estado_radicado": {"antes", "despues"}`); tras el commit se publica,
   best-effort, `workflow.radicado.estado_cambiado` (dedup
   `{radicado_id}:{despues}:{flow_event_id}`). Los `GET` nunca sincronizan.
7. **Reasignación en cascada** devuelve el paso a `pending` (Orfeo `LEIDO=0`: el nuevo
   responsable no lo ha leído) y el radicado a `distributed`; **excluye** los radicados
   terminales (`skipped_terminal` en la respuesta, en pasos), toma los cerrojos ordenados por
   `radicado_id` y deja un asiento `workflow.estado_radicado` por radicado cambiado.
8. **document-service ya no acepta `status` en el PATCH**: `422 status_managed_by_workflow`.
   No basta con quitar el campo —Pydantic ignoraría el extra y el cliente creería que el
   estado cambió—, así que el campo se conserva solo para rechazarlo con error explícito.
9. **Backfill** (`migrations/tenant/013_radicado_estado_desde_flujo.sql`): recalcula los
   radicados existentes con el `CASE` gemelo, deja evidencia en
   `radicado_estado_recalculo (radicado_id, antes, despues, at)`, **no escribe `audit_log`
   desde SQL** (la cadena de hash la firma la aplicación), excluye terminales, es
   idempotente (`IS DISTINCT FROM`; la 2.ª corrida cambia 0 filas) y avisa con
   `RAISE NOTICE` de los terminales que aún tienen paso activo (los sanea la migración 015, R7).
10. **MCP**: `actualizar_radicado` pierde `status`; nueva tool `finalizar_tramite`
    (`destructive=True`, irreversible, tarjeta de confirmación).

> **Ubicación (2026-10-02, [ADR-029](ADR-029-ubicacion-actual-del-radicado.md)).** El mismo `sincronizar_estado_radicado` escribe también dónde está el radicado (`flujo_dept`, `flujo_dept_code`, `flujo_usuario`) en el mismo `UPDATE` que `status`.

## Declarado fuera (a propósito, no un olvido)

- **Salidas (`S`) e Internos (`I`) sin reparto fuera.** El estado solo se mueve cuando un
  paso los toca; un radicado sin pasos sigue `registered`. Un radicado de salida no «se
  distribuye» a una dependencia: se envía a un tercero (E20), que es otro circuito.
- ~~La anulación no cancela el paso~~ — **corregido en la remediación** (ver abajo, decisión R7).
- **`rollback` solo desde `pending`.** Revertir un paso que ya se leyó no se admite; no
  cambia por esta decisión.
- **Completar un paso sin marcarlo leído deja `distributed`.** No hay estado «completado sin
  leer»; la lectura es un acto del poseedor y completar no la presume.
- **Completar un paso `in_progress` deja el radicado `in_progress` sin paso activo.** Se
  mantiene a propósito (decisión R14): el radicado ya fue leído y trabajado; degradarlo a
  `distributed` mentiría. El poseedor del último paso lo cierra con «Finalizar» (R12) o lo
  reparte; mientras tanto el estado dice la verdad: se leyó y nadie lo ha cerrado.
- **Acto «reabrir» fuera de esta tanda.** Un radicado `closed` no vuelve a abrirse. MoReq
  prevé la reapertura controlada (permiso de administración, motivo, asiento propio); queda
  para su propio incremento, no se improvisa aquí.
- **Salidas e Internos sin reparto** (ya declarado arriba) siguen fuera de la tanda.
- **`finalizar`, `devolver` y `rollback` sobre un radicado ya cerrado dan 404 / 409 propio,
  no `radicado_terminal`**: un cerrado no tiene paso activo, y revelar «está cerrado» a
  quien no posee nada sería un oráculo sobre radicados que quizá no puede leer.
- **La importación (INT-05) conserva el `status` del paquete** (salvo `anulado`, que se
  rechaza; decisión R8): restaurar un radicado `closed` desde un paquete es una restauración,
  no un acto del flujo.
- **El frontend** (botón «Finalizar trámite», retirada de «Distribuir»/«En trámite» manuales,
  traducción del 409) queda para su propio incremento.

## Consecuencias

- El panel, la bandeja y la búsqueda pueden volver a fiarse de `radicados.status`.
- El `PATCH` de radicado deja de ser una puerta lateral al estado (incluida la de
  «desanular»: ahora se rechaza antes de llegar al guard `409 radicado_anulado`).
- Se añade un cerrojo advisory por radicado a cuatro actos que no lo tenían; el coste es
  serializar actos sobre el **mismo** radicado, que es lo correcto.
- Verificado por mutación contra Postgres real (`RUN_INTEGRATION=1`): quitar la
  sincronización de `_create_step`, sacarla de la transacción, quitar la guarda terminal,
  quitar la comprobación de poseedor, invertir el `CASE` del backfill, no excluir terminales
  en la cascada e ignorar la bandera de `finalizar` — cada una tumba al menos una prueba.


## Remediación de la auditoría (2026-10-02)

Los dos auditores (seguridad multi-tenant y cumplimiento archivístico) revisaron la
implementación antes de integrarla. Estas son las decisiones que se tomaron, numeradas R1…R14
(las que cambian o completan la decisión original, con su porqué).

### Seguridad

- **R1. `assign`/`transfer` exigen clearance y tenencia.** Eran la puerta lateral: B se
  autoasignaba un radicado ajeno (incluso reservado: *write-up*) y luego «Finalizar» lo cerraba.
  Dentro de la transacción y **tras `lock_radicado`**: (a) el nivel del actor va **en el `WHERE`**
  de la lectura del radicado (`nivel_seguridad <= $nivel`; no es una comprobación previa, regla
  RF-SEG-08) y un radicado inaccesible da el mismo `404 radicado_not_found` que uno inexistente;
  (b) si hay paso activo, solo su poseedor puede repartirlo o trasladarlo (mismo `_holder_condition`),
  y un no-poseedor recibe el `404 no_active_step` neutro. El clearance lo resuelve el servicio desde
  `auth_memberships`/`role_clearance` (mismo schema de tenant; **no** viaja por claims ni cabeceras:
  el gateway solo inyecta `X-User-Id`). El **reparto automático** al radicar es un actor de sistema
  (sin usuario, sin clearance ni tenencia): `WorkflowService.assign(..., sistema=True)`, que solo
  pasa `auto_distribute` — ninguna ruta HTTP puede pasarlo. **Redistribución forzada del paso de
  otro**: *no se expone* por `assign`/`transfer`; basta la **reasignación en cascada**
  (`USUA_PERM_ADMIN`), que ya existe, con asiento por radicado. El residual declarado entonces (radicado con historial pero sin paso activo, repartible por cualquiera con `PERM_TRAMITAR`) se cierra en **R16**.
- **R2. Oráculo `409 radicado_terminal`.** Se cierra con R1 (el gate va antes de sincronizar) y,
  para `anular`, con `transaction_types.requires_holder = true` (migración 014). Además el código
  exige tenencia a **toda** transacción que mueve el paso (`target_step_status`), la declare o no
  su fila: el catálogo es configurable por el tenant.
- **R3. `finalizar` a nivel 3.** `require_permission("PERM_TRAMITAR", min_crud=3)`: era `1` (leer);
  el acto irreversible no puede alcanzarlo quien solo lee. Sale de `SIN_NIVEL_DECLARADAS` y el
  catálogo MCP lo espeja (`min_crud=3`).
- **R4. Una sola definición de poseedor.** `complete_step` usaba `assigned_to = $4 OR assigned_to
  IS NULL` sin mirar el estado: cualquiera cerraba el paso sin dueño de **otra** dependencia y un
  paso ya cerrado se «re-cerraba». Ahora `WHERE id = $3 AND status IN ('pending','in_progress') AND
  {_holder_condition(4)}`: la misma condición que `es_poseedor`, `tenencia` y `finalizar`.
- **R5. MCP `finalizar_tramite` se confirma con el radicado concreto** (`confirmar_con =
  "radicado_id"`), no con la frase fija `CONFIRMO`: una frase fija confirma «un cierre», no «este
  cierre», y se teclea de memoria (escalera de confirmación, `write_gate.py`).
- **R6. La cascada no agota `max_locks_per_transaction`.** Tomaba un `pg_advisory_xact_lock` por
  radicado (cada uno ocupa una ranura de la tabla de cerrojos, 64 por defecto y compartida): un
  barrido grande fallaba a medias. **Elección**: un único cerrojo advisory de **alcance** del tenant
  —exclusivo en la cascada, **compartido** en `lock_radicado`— más cerrojos de **fila**
  (`SELECT … FOR UPDATE` en orden de `id`) sobre los radicados, también los que aparezcan al
  releer. Los actos de trámite siguen serializando por radicado y no se estorban entre sí; la
  cascada espera a los que están en curso y los detiene mientras dura. La alternativa (lotes con
  una transacción por lote) dejaba la cascada parcialmente aplicada y sin asiento atómico. Costo
  declarado: un acto administrativo raro detiene brevemente el trámite **de ese tenant**. Orden de
  cerrojos en todos los escritores —alcance, radicado, **fila del radicado**, luego sus pasos—
  para que no haya ciclos de interbloqueo (la anulación de document-service también toma la fila
  antes de tocar los pasos).

### Cumplimiento archivístico

- **R7. La anulación cancela el paso activo, en la misma transacción.** Un radicado anulado dejaba
  su paso `pending`: un zombi en bandeja, vencidos y cascada que nadie podía cerrar (todo acto sobre
  un terminal da 409). **Decisión de frontera**: `AnulacionRepository.decide` (document-service)
  pone el paso en `cancelled` y deja el evento `flow_events.tipo_tx = 'anulacion'` **en la misma
  transacción** que `status = 'anulado'` y el asiento. `flow_steps`/`flow_events` son del
  workflow-service pero viven en el mismo schema de tenant (ADR-002/012) y el rol de aplicación ya
  escribe en todas. Se prefirió a un consumidor de eventos en workflow por **atomicidad**: con un
  evento hay una ventana en la que el radicado está anulado y su paso vivo, y un evento perdido la
  deja abierta para siempre. Sin las tablas del flujo (se migran aparte) el `SAVEPOINT` absorbe el
  error y la anulación sigue. Coste: document-service conoce el estado destino del paso
  (`cancelled`, el mismo de la transacción `anular` del catálogo). **Zombis existentes**: migración
  `015_sanear_pasos_zombi.sql` — paso activo de radicado `anulado` → `cancelled`, de `closed` →
  `completed`; cada uno deja un evento `saneamiento_terminal` y una fila en
  `flow_step_saneamiento` (solo inserción, con el estado antes/después). **Defensa en
  profundidad**: `inbox`, `pending` y `list_overdue_unnotified` excluyen radicados terminales.
- **R8. Importación INT-05.** `RADICADO_SCHEMA` fija `metadata_sistema.status` a `registered |
  distributed | in_progress | closed`: un paquete con `anulado` se rechaza entero (no trae el acto
  de anulación —causal, aprobador, asiento— que lo justifique y crearía un radicado anulado
  inexplicable; el export ya excluye anulados). El asiento `radicado.import` registra el
  `status_origen` de cada radicado. Migración `034_radicado_status_check.sql` (document-service):
  `CHECK (status IN ('registered','distributed','in_progress','closed','anulado'))` —verificado
  contra el código y los seeds— añadido `NOT VALID` y validado si los datos lo permiten; con datos
  heredados fuera del conjunto avisa y deja la restricción rigiendo lo nuevo, sin tumbar el
  arranque de la entidad.
- **R9. Evidencia del backfill 013.** `radicado_estado_recalculo` es de **solo inserción** (trigger
  que rechaza UPDATE/DELETE) con `tracking_number`, `paso_activo` y `origen = 'migracion_013'`; el
  backfill ya **no toca `updated_at`** (recalcular un estado derivado no es editar el documento).
  El asiento-ancla `workflow.estado_radicado.backfill` (conteo + SHA-256 del contenido ordenado)
  lo escribe **la aplicación**, nunca SQL: el *runner* de migraciones (`migrations/runner.py::
  anclar_evidencia`), en la misma transacción que la migración, una vez por tenant (si ya existe el
  asiento no escribe otro; sin filas de evidencia tampoco). Misma mecánica para el saneamiento 015
  (`workflow.flow_step.saneamiento`). 013 aún no se había liberado, así que se editó en lugar de
  añadir una migración correctora (un tenant de desarrollo que ya la aplicó debe recrearse).
- **R10. El asiento `workflow.finalizar`** añade `step_id`, `flow_event_id`, `tiene_respuesta`
  (existe una Salida con `responde_a` = este radicado), `fuera_de_plazo` (`due_at` vs. ahora) y
  `en_expediente` (vinculado en `expediente_radicados`). Las dos lecturas cruzadas se hacen en un
  `SAVEPOINT`: si la tabla o la columna aún no existe en el tenant el valor es `null` («no
  consta»), nunca un `false` inventado, y no aborta el cierre.
- **R11. `motivo_cierre` tipificado y obligatorio**: `respondido | nrr | traslado_competencia |
  otro`, en el evento y en el asiento. Aclaración sobre «NRR»: es un **motivo de cierre**, no una
  transacción que cierre; la transacción `nrr` del catálogo (No requiere respuesta) existe, no
  tiene efecto de estado y **no cierra** nada — solo «Finalizar» cierra.
- **R12. `finalizar` sin paso activo**: lo permite quien poseía el último paso, ya `completed`, si
  el radicado no es terminal (completó el suyo y no repartió; exigir un paso activo obligaba a
  inventar un reparto ficticio). Cualquier otro caso, el mismo `404 no_active_step`; un radicado ya
  cerrado también da 404 (no 409).

### Pruebas y deuda de las pruebas

- **R13. `WorkflowService._cambios`** se vacía al inicio de cada acto: un acto que revertía dejaba
  cambios nunca confirmados que el siguiente acto de la misma instancia emitía como eventos
  fantasma (la prueba `xfail(strict)` pasó a verde).
- **R14.** Ver «Declarado fuera»: completar un paso `in_progress` deja `in_progress`.

### Defecto previo encontrado al probar contra Postgres real

`AnulacionRepository.decide` condicionaba la decisión a `estado = 'pendiente'`, un valor que el
CHECK de `radicado_anulacion` nunca admite (`solicitada | aprobada | rechazada`): **ninguna
aprobación de anulación podía aplicarse en una base real** (siempre `409 anulacion_ya_decidida`),
y los mocks no lo veían porque devolvían la fila igual. Corregido a `'solicitada'` y fijado con
pruebas de integración.

## Re-auditoría de seguridad: residuales (2026-10-02)

La re-auditoría de la remediación dejó siete residuales y una guarda mal ubicada. Se cierran
así (R15…R21); cada uno tiene su prueba contra Postgres real (`RUN_INTEGRATION=1`) verificada por
mutación.

- **R15. Los actos por tenencia también miran el clearance del POSEEDOR (media).** `finalizar`,
  `complete_step`, `marcar_leido` y toda transacción que exige tenencia comprobaban *quién* tiene el
  paso, no *qué nivel* alcanza: un miembro de la dependencia con clearance 1 cerraba un reservado
  repartido a su dependencia sin dueño (o uno reclasificado tras el reparto). Ahora el nivel va **en
  el `WHERE`** (`_nivel_poseedor_condition`: `EXISTS (SELECT 1 FROM radicados … nivel_seguridad <=
  $n)`, clearance resuelto como en R1 con `clearance_para_acto`) de `get_active_step_for_update`,
  `ultimo_paso_si_lo_poseia`, `es_poseedor`, `complete_step` y, para rollback/devolver, de sus
  `UPDATE`. Fail-closed: sin fila en `radicados` no casa. El resultado es el mismo 404 neutro
  (`no_active_step` / `step_not_found`). `rollback` y `devolver` dejan de usar
  `CLEARANCE_SIN_RESTRICCION`.
- **R16. Un radicado sin paso activo no es de cualquiera (media).** `assign`/`transfer` con
  `assigned_to = uno mismo` sobre un radicado sin paso activo lo tomaba cualquiera con
  `PERM_TRAMITAR`. Ahora, tras `lock_radicado` y sin paso activo: **con historial** exige
  `ultimo_paso_si_lo_poseia` (poseedor del último paso, completado, radicado no terminal);
  **sin ningún paso** exige `radicados.registered_by = actor`. `registered_by` guarda el
  `X-User-Id` que document-service recibe al radicar, es decir `auth_users.id` (el mismo que el
  actor del workflow, no el `sub` de Keycloak). El reparto automático (`sistema=True`) no pasa por
  esta regla y la redistribución forzada sigue siendo la cascada. **Se retira la objeción «rompería
  al radicador»** de R1: el radicador conserva el primer reparto (es `registered_by`) y, después,
  cualquier reparto lo hace el poseedor del trámite, que es lo que la tenencia siempre dijo.
  Consecuencias aceptadas: (a) quien radicó **deja de poder repartir** en cuanto hay historial salvo
  que poseyera el último paso; (b) un radicado con `registered_by` NULL (importado, ingestado por
  sistema) **no lo reparte nadie a mano** (solo el reparto automático o la cascada); (c) un
  radicado cerrado ya no da `409 radicado_terminal` al reparto de quien poseía su último paso sino
  el `404 no_active_step` neutro.
- **R17. Transacciones sin tenencia (media) y cierre del oráculo `no_flow` (H).** `vobo`, `informar`,
  `nrr`, `agendar`, `solicitar_firma`, `cerrar_exp`, `change_folder`, `validate_trd_send` no declaran
  tenencia ni `target_step_status`, no aplicaban clearance y devolvían `tracking_number`/`to_dept`
  de un reservado. `get_latest_step` lleva ahora el nivel en el `WHERE`: un radicado inalcanzable es
  **`404 no_flow`**, igual que uno inexistente o sin pasos. El no-autorizado recibe siempre esa misma
  respuesta (para cualquier `tipo_tx`, con o sin tenencia), de modo que `no_flow` frente a
  `no_active_step` ya no distingue «existe y es reservado» de «no existe»; `no_active_step` solo lo
  ve quien sí alcanza el radicado.
- **R18. TOCTOU en `rollback` y `devolver` (baja).** Leían el paso actual y la tenencia **fuera** de
  la transacción, antes del cerrojo: otro acto podía mover el paso entre la comprobación y la
  escritura. Ahora ambas lecturas van dentro de la transacción y tras `lock_radicado`, y
  `apply_rollback`/`apply_devolucion` **repiten** la condición en el propio `UPDATE` (estado activo +
  poseedor + nivel, `RETURNING`): si ya no se cumple devuelven `None` y el servicio responde el 404
  neutro sin escribir nada.
- **R19. Una sola definición de poseedor también en `rollback`/`devolver` (baja).** Usaban
  `assigned_to == actor`: el paso sin dueño (el que deja una devolución) no lo podía revertir ni
  devolver nadie de su dependencia. Ahora `_holder_condition`, como en R4. `rollback` conserva su
  exigencia propia de que el paso esté `pending`.
- **R20. MCP `finalizar_tramite` declara `motivo_cierre` (funcional, bloqueante).** El esquema del
  cuerpo es `additionalProperties: false` y el backend exige `motivo_cierre` (R11): al no declararlo
  el catálogo, **toda** llamada daba 422. Parámetro obligatorio con
  `enum respondido|nrr|traslado_competencia|otro`.
- **R21. La carrera de dos decisiones de anulación da 409, no 500 (baja).**
  `AnulacionRepository.decide` terminaba en `assert row is not None`: la decisión concurrente
  (que el `UPDATE … WHERE estado = 'solicitada'` no encuentra) era un 500. Devuelve `row | None` y
  el servicio —que ya cortaba antes del asiento— responde `409 anulacion_ya_decidida`.
- **RN-8. La guarda de `auto_distribute` va dentro del cerrojo.** La comprobación «sin pasos» de
  `auto_distribute` está fuera de cualquier cerrojo: dos entregas simultáneas del mismo evento la
  pasaban las dos. Con `sistema=True`, `_create_step` la repite **tras `lock_radicado`** y, si ya hay
  pasos, lanza `YaDistribuidoError`, que `auto_distribute` trata como «nada que hacer» (la guarda
  externa se conserva como atajo barato). Probado con dos conexiones reales.

### Declarado, no corregido

- **La anulación puede anular un radicado `closed`.** Decisión de dominio abierta (un cierre no
  impide anular un documento erróneo; la anulación exige causal, solicitante y aprobador distintos).
- **`USUA_PERM_ADMIN` en `min_crud=1` para la cascada.** Deuda previa a ADR-028: la reasignación en
  cascada, que mueve pasos ajenos, sigue pidiendo solo el nivel de lectura de ese permiso.
- **`TRUNCATE` no está cubierto por los triggers de evidencia** (`radicado_estado_recalculo`,
  `flow_step_saneamiento`): los triggers rechazan `UPDATE`/`DELETE`, no `TRUNCATE`. Lo cubre el
  privilegio del rol de aplicación, no el esquema.
- Un último paso `cancelled`/`returned` (no `completed`) sin paso activo no cuenta como «poseído»
  para R16: nadie reparte a mano ese radicado; la salida es la cascada o un administrador.
