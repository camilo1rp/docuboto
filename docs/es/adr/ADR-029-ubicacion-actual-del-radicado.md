# ADR-029: Ubicación actual del radicado

**Estado**: Aceptado
**Fecha**: 2026-10-02
**Autores**: Giampiero (mantenedor principal); diseño de `orfeo-architect`
**Relacionados**: [ADR-028](ADR-028-el-flujo-mueve-el-estado-del-radicado.md) (el flujo mueve el estado del radicado), [ADR-010](ADR-010-libreria-compartida.md) (auditoría inmutable), E05 (flujos), E01 (radicación), RF-SEG-08 (no-read-up)

---

## Contexto

Al recorrer la aplicación con el navegador, el radicado `2026-DEMO-E-000117` aparecía en la
lista con la dependencia **Oficina de Tecnología** tras haber sido repartido a Talento Humano
y devuelto a Control Interno. La petición del usuario fue: «debería mostrar siempre en dónde
estaba ubicado el radicado».

La causa es que `radicados.dest_dept`/`dest_dept_code` guardan la dependencia **de
radicación**: a quién iba dirigido el documento, una instantánea legal que se fija al radicar. Solo
se corrige por PATCH mientras el radicado no esté cerrado (en `closed` → 409 `dest_dept_immutable`) y, al corregir
el nombre, el código se re-resuelve en el mismo UPDATE (`NULL` si el nombre no identifica UNA dependencia activa). Dónde está el radicado **ahora** solo se podía reconstruir recorriendo `flow_steps`,
que es de otro servicio. En Orfeo esto es `radi_depe_actu` / `radi_usua_actu`: dos columnas del
propio radicado que cada reparto reescribe.

## Decisión

1. **El workflow-service escribe la ubicación, en el MISMO `UPDATE` que `status`.** Tres
   columnas nuevas en `radicados`: `flujo_dept_code`, `flujo_dept` (el nombre del paso,
   instantánea) y `flujo_usuario` (`auth_users.id` del poseedor, nunca el `sub` de Keycloak).
   Las escribe `sincronizar_estado_radicado` (ADR-028) —y por tanto también la reasignación en
   cascada—, dentro de la transacción del acto y antes del asiento: si la auditoría falla, no
   queda ni paso ni estado ni ubicación. **document-service nunca las escribe**: un solo
   escritor.
2. **La ubicación que se lee son dos columnas GENERADAS** (`STORED`) `ubicacion_dept_code` /
   `ubicacion_dept`: la del flujo si el flujo ya movió el radicado; si no, la dependencia de
   radicación (destino y, a falta de él, origen). Al ser generadas no hay un segundo escritor que
   se desincronice, y un radicado sin pasos se lee igual que antes. Con índice
   (`ix_radicados_ubicacion_dept_code`).
   **Se calculan por PARES**, no con un `COALESCE` columna a columna: con
   `COALESCE(flujo_dept_code, dest_dept_code, …)` un nombre de flujo sin código único mostraría el
   código de *otra* dependencia (el de radicación). Es un refinamiento del diseño original, que
   proponía el `COALESCE` simple.
3. **Una sola función pura** (`workflow-service/app/services/estado_radicado.py`), junto a
   `derivar_estado_radicado`:

   ```python
   def derivar_ubicacion(actual, pasos, *, congelada=None) -> Ubicacion | None
   ```

   - Con paso activo (`pending`/`in_progress`): el de mayor `step_number`.
   - Sin paso activo: el paso que **se cerró más recientemente** (`completed_at` DESC, desempate
     `step_number` DESC), en cualquier estado. **No** por número de paso: tras un rollback el paso
     cancelado tiene el número más alto pero se cerró antes que el reactivado y completado
     después, y ordenar por `step_number` pondría el radicado en la dependencia que lo canceló.
   - Sin pasos: `None`; la lectura cae a la dependencia de radicación.
   - **Terminales congelan**: un `closed`/`anulado` con ubicación ya escrita la conserva. Un
     cerrado queda en la dependencia donde se finalizó; un anulado, donde estaba al anularse (la
     anulación no reescribe nada). Un terminal anterior a este ADR (sin ubicación escrita) se
     deriva del historial, que es lo que hace el backfill.
4. **El código de la dependencia solo se resuelve si el nombre es único.** `flow_steps.to_dept`
   guarda el nombre sin FK y `dependencias.nombre` **no es único**. El código se resuelve solo si
   el nombre casa con exactamente UNA dependencia **activa**; con cero o con varias,
   `flujo_dept_code` queda `NULL` y `flujo_dept` conserva el nombre como instantánea. Elegir una
   sería inventar dónde está el radicado. Guardar `to_dept_code` en el paso, que cierra el
   problema de raíz, queda como incremento aparte.
5. **Migraciones.** `document-service` `037_radicado_ubicacion.sql` crea las columnas y el
   índice (dueño de `radicados`; idempotente). `workflow-service`
   `016_radicado_ubicacion_desde_flujo.sql` rellena lo existente con el gemelo literal de la
   función Python (la prueba de integración compara cada fila con ella), deja la evidencia en
   `radicado_ubicacion_recalculo` —tabla de **solo inserción**, con trigger que rechaza
   `UPDATE`/`DELETE`— y un asiento-ancla `workflow.ubicacion_radicado.backfill` que escribe el
   *runner* (patrón de 013; nunca SQL contra `audit_log`). No toca `updated_at`. **Guardas de
   orden**: sin `radicados` en el schema no hay nada que recalcular y se omite; con `radicados`
   **sin** las columnas (document-service aún no migró) lanza `RAISE`, porque la migración se
   marca aplicada al terminar y un `skip` silencioso dejaría la entidad sin backfill para siempre.
   Orden de despliegue: runner de document-service y después el de workflow-service.
6. **Lecturas** (document-service):
   - `GET /api/v1/documents` y `GET /api/v1/search` admiten `?ubicacion_dept_code=`. El filtro
     va en la **misma condición** que filas y `COUNT` (junto al *clearance*): el total nunca
     cuenta lo que la página no puede mostrar.
   - Las respuestas llevan `flujo_dept_code`, `flujo_dept`, `ubicacion_dept_code`,
     `ubicacion_dept`; `dest_dept` sigue siendo la de radicación. **`flujo_usuario` solo sale en el
     detalle autenticado** (`GET /documents/{id}` y `by-tracking`), nunca en listados ni
     búsquedas.
   - Reportes: `GET /reports/radicados` añade `by_ubicacion` —lo que sigue en trámite, **sin**
     cerrados ni anulados— y `by_dependencia` pasa a documentarse como la de radicación; en el CSV
     la sección `dependencia` se renombra `dependencia_radicacion` y aparece `ubicacion_actual`.
   - Export INT-01: `metadata_sistema.ubicacion_dept` / `ubicacion_dept_code` como dato
     **informativo**; el import los **ignora** (la ubicación del importado la deriva el flujo de la
     entidad que recibe) y el poseedor no sale nunca en un paquete.
   - MCP: `listar_radicados` y `buscar_radicados` ganan el parámetro `ubicacion_dept_code`, y
     `dest_dept`/`dest_dept_code` se describen como «de radicación» para que el modelo no filtre
     «lo que tiene Control Interno» por una columna que dice a quién iba dirigido.
7. **Fuera, a propósito.** Todo lo público (`schemas/public.py`, verificación por código,
   OAI-PMH, CMIS, EAD): dónde está un radicado y quién lo tiene es trámite interno. Los eventos
   (`workflow.radicado.estado_cambiado` no cambia) y knowledge. `workflow_rules` sigue con
   `dest_dept` (radicación): una regla de reparto automático decide a quién se **reparte** un
   radicado según a quién iba dirigido.

## Consecuencias

- La lista, la búsqueda y el detalle dicen dónde está el radicado sin recorrer `flow_steps`, y se
  puede filtrar «lo que está ahora en Control Interno» con índice.
- Un radicado sin movimientos se lee exactamente como antes.
- `UPDATE` de `status` y de la ubicación son uno: no hay ventana en la que el estado diga
  `distributed` y la ubicación no se haya movido.
- Coste: tres columnas más escritas por el workflow-service en cada acto y dos generadas
  `STORED` (reescritura de tabla al aplicar 037; lectura barata).
- Verificado por mutación contra Postgres real (`RUN_INTEGRATION=1`): quitar la escritura de
  `flujo_*` en `sincronizar_estado_radicado`, ordenar el «último» por `step_number`, sacar el filtro
  del `COUNT` y añadir la ubicación a `PublicVerifyResponse` — cada una tumba al menos una prueba.

## Declarado fuera

- ~~El frontend~~ **Hecho** (2026-10-02): `/bandeja`, `/busqueda` (columna y filtro), `/reportes` (panel
  «Por ubicación actual» y renombre del de radicación), panel principal y selector de radicados del
  expediente. `by_dependencia` conserva su nombre en el JSON; la pantalla lo rotula «de radicación».
- **`flow_steps.to_dept_code`** (código en el paso): cerraría el caso de nombre no único de raíz;
  requiere migrar el paso y sus escritores.
- **La ubicación no viaja en el asiento** de auditoría: es una proyección derivada del paso, que ya
  deja su propio asiento.
