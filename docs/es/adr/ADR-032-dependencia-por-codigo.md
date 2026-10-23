# ADR-032: La dependencia se correlaciona por código, no por nombre

**Estado**: Aceptado
**Fecha**: 2026-10-10
**Autores**: Giampiero (mantenedor principal); triage B5
**Relacionados**: [ADR-012](ADR-012-migraciones-por-tenant.md) (migraciones), [ADR-028](ADR-028-el-flujo-mueve-el-estado-del-radicado.md) (el flujo mueve el estado), [ADR-029](ADR-029-ubicacion-actual-del-radicado.md) (ubicación actual), [ADR-010](ADR-010-libreria-compartida.md) (sobre de eventos)

---

## Contexto

Orfeo identifica una dependencia por `DEPE_CODI` (numérico): `radi_depe_actu`, `USUARIO.DEPE_CODI` y el
historial usan el código; el nombre (`DEPE_NOMB`) solo se une para mostrar, y renombrar no comprueba
nada porque nada se une por nombre. OrpycaMCP heredó el código en casi todo (`urd_usuaroldep.depe_id`,
`radicados.*_dept_code`, `flujo_dept_code`, archive, buzones), **pero el flujo no**: `flow_steps.to_dept`,
`workflow_rules.assign_to_dept` y la bandeja (`to_dept IN (SELECT d.nombre …)`) se unían a `dependencias`
**por nombre**. Consecuencias medidas:

1. Renombrar una dependencia con trámite vivo dejaba sus pasos fuera de **toda** bandeja y de la
   ubicación; por eso tenant-service lo prohibía con un `409` (divergencia del legado, que renombra libre).
2. Dos dependencias homónimas se confundían: `borrador_service` elegía `ORDER BY codigo LIMIT 1` y
   radicaba hacia la de menor código **en silencio**; `notification` tomaba una fila arbitraria.
3. `dependencias.codigo` era editable por `PATCH` y 8+ tablas de 5 servicios lo citan **sin FK**: cambiar
   el código rompía todo en silencio.
4. `nombre` admitía 500 caracteres y las instantáneas son `VARCHAR(100)`.

## Decisión

### 1. La llave es `dependencias.codigo` (INT); el nombre es una instantánea

Alternativa B (UUID `dependencias.id`) descartada: introduce una **tercera** identidad cuando el sistema ya
habla código, obliga a un `JOIN` UUID↔código en cada consulta de bandeja (`urd_usuaroldep.depe_id` es el
código) y deja sin proteger las 8 tablas que ya lo citan.

- `*_dept_code` es el campo canónico; `*_dept` (nombre) queda como **instantánea del momento del acto**.
  Las respuestas llevan siempre ambos.
- **`codigo` es inmutable**: `PATCH` con otro valor ⇒ `422 dependencia_codigo_inmutable` (el mismo valor
  es un no-op: el formulario lo reenvía) y, como defensa en profundidad, un trigger `BEFORE UPDATE OF
  codigo` (`ERRCODE check_violation`) cubre scripts y futuros endpoints. Corregir un código mal tecleado =
  crear la dependencia nueva y desactivar la vieja. Se descartó «409 solo si hay referencias»: las
  referencias viven en 8+ tablas sin FK y una lista de tablas se queda corta en silencio.
- `nombre` ≤ 100 (`CHECK` + Pydantic): la migración aborta listando las que exceden.

### 2. Una sola definición del criterio nombre → código

`resolver_codigo_dependencia(nombre, historico)` (migración tenant `013`, en el schema de cada tenant)
devuelve `(codigo, candidatos)`; el código sale **solo con exactamente un candidato**. `historico=false`
(alias de API): dependencias **activas** con ese nombre **actual**. `historico=true` (backfill y replay):
activas, inactivas, nombres anteriores y mapeos manuales. `dependencia_nombre_historico` es de solo
inserción: el renombre guarda el nombre anterior en la misma transacción que el asiento; el mapeo manual
(`POST /dependencias/{id}/mapeos-nombre`, `USUA_PERM_ADMIN` nivel 3, asentado) es la decisión humana para
un nombre ambiguo o huérfano. Nunca se desempata.

### 3. Contrato: código canónico, nombre alias deprecado

Un ciclo de convivencia, resuelto con `historico=false`: 1 candidato ⇒ su código; 0 ⇒ `422
dependencia_desconocida`; ≥2 ⇒ `422 dependencia_ambigua` (con `candidatos`); código y nombre que no
concuerdan ⇒ `422 dependencia_inconsistente`. El nombre que se **guarda** lo pone el servidor desde el
catálogo, nunca el texto del cliente. El uso del alias responde `Deprecation: true` y
`Link: </docs/es/api.md#dependencias-por-codigo>; rel="deprecation"` y deja un `warning` en el log (mide
cuándo retirarlo). El **origen** de un paso ya no lo aporta el cliente: lo deriva el servidor del paso
activo; si el cliente lo envía y no coincide ⇒ `409 origen_desactualizado`. Las bandejas aceptan
`?dept_code=` (`?dept=` es el alias; ambiguo es 422, ya no «lista vacía»). Las condiciones de reglas
`eq/ne/in` sobre `dest_dept`/`origin_dept` se rechazan (`422 condicion_por_nombre_obsoleta`: cambiaban de
sentido al renombrar) y se ofrece `dest_dept_code`/`origin_dept_code`; `contains`/`regex` sobre texto siguen.
Eventos: `workflow.*` y `document.radicado.created` llevan **ambos** campos (ADR-010: aditivo); los
consumidores prefieren el código y caen al nombre resuelto **con historial** (así un evento viejo de la
cola de fallidos con el nombre anterior a un renombre sigue llegando a su dependencia).

### 4. Migraciones con backfill que falla cerrado

`tenant 013` (trigger, historial, función), `document 046` (`borrador.dest_dept_code`, relleno de
`radicados.*_dept_code`), `workflow 019` (columnas `*_code` en pasos, eventos y reglas; relleno;
condiciones; ubicación; restricciones). Reglas: se escribe **solo con un candidato**; un paso **vivo**, una
regla o una condición eq/ne/in con nombre ambiguo/huérfano **aborta la transacción** (la migración no queda
en el libro; el remedio es el mapeo manual y reejecutar); en historia (pasos terminales, `from_dept`,
`flow_events`) el código queda `NULL` con evidencia, porque la historia no se adivina. `flow_events` es
append-only: la 019 aparta el trigger **dentro de la transacción**, escribe solo las dos columnas nuevas, lo
rehabilita y **verifica** que quedó habilitado. Cada migración deja evidencia de solo inserción y el runner
la **ancla** en `audit_log` (conteo + SHA-256 del contenido ordenado). Después de la 019 un paso vivo sin
código no puede existir (`CHECK`), ni una regla sin código destino.

### 5. Desactivar y eliminar

Renombrar ya **no** se bloquea. **Desactivar** conserva el `409 dependencia_con_tramite_en_curso`, ahora por
código (la bandeja filtra `d.activo`: el trámite quedaría invisible; divergencia deliberada del legado,
que desactiva sin comprobar). **Eliminar** da `409 dependencia_con_historia` si **cualquier** tabla la cita
(pasos, eventos, reglas, radicados, borradores, URD, expedientes): una dependencia usada no se borra
(trazabilidad, Ley 594). La lista de tablas **no** es a mano: `referencias()` barre
`information_schema` del schema del tenant buscando columnas enteras `*dept_code`, `*depe_id`,
`*depe_codigo`, `*dependencia*_codigo`, `oficina_productora_codigo` y `unidad_administrativa_codigo`
(más `dependencia_nombre_historico.codigo`), así que una tabla nueva de otro servicio queda cubierta y
una dependencia con renombres o mapeos tampoco se elimina. Corrección (ronda de auditoría): la versión
inicial de este párrafo afirmaba que, al añadir tablas, «el fallo es no deja borrar»; era **al revés**
con una lista a mano —lo que faltaba se contaba como cero y el borrado pasaba—. Hoy el fallo de una
omisión del barrido sería «borra con referencias», por eso el barrido es por catálogo y la prueba de
integración fija cada columna.

**Un código eliminado no se reutiliza.** `DELETE` escribe, en la misma transacción que el asiento
`dependencia.eliminada`, el código en `dependencia_codigo_retirado` (solo inserción); crear una dependencia
con un código retirado, o con historial de nombres y sin dependencia viva, da `409
dependencia_codigo_retirado` (y un trigger `BEFORE INSERT` lo impide aunque se salte la API). Sin esto,
una dependencia nueva heredaría los trámites viejos que citan ese código.

**Mapeo manual (`POST /dependencias/{id}/mapeos-nombre`).** Con `p_historico` el mapeo **es** la
respuesta de `resolver_codigo_dependencia` para ese nombre (gana a las coincidencias textuales; dos
mapeos con códigos distintos siguen siendo ambiguos); antes se sumaba al `UNION` y no podía desambiguar
homónimas. Es **irreversible** (el historial es de solo inserción y no hay endpoint para deshacerlo), por
eso `409 dependencia_inactiva` si el destino está inactiva y `409 nombre_de_otra_dependencia` si el
nombre es el vigente de OTRA dependencia activa (salvo que el destino también lo lleve: homónimas, el
caso que el mapeo existe para resolver). El `PATCH` que renombra admite un `motivo` opcional (acto de
reestructuración), guardado en el historial y en el asiento.

**Solo nombres huérfanos o ambiguos.** El mapeo es la salida del RAISE de los backfills, no una vía para
redirigir un nombre que ya resuelve: dentro de la transacción, antes de insertar, se consulta
`resolver_codigo_dependencia(nombre, true)` y se rechaza con `409 nombre_resuelto_a_otra_dependencia` si
da exactamente un candidato y no es el código de la dependencia destino (huérfano = 0 candidatos y
ambiguo = más de 1 siguen admitidos). El `PATCH` que renombra rechaza con `409
nombre_mapeado_a_otra_dependencia` un nombre nuevo que ya tiene un mapeo manual hacia otro código.

**Evidencia de los backfills (ronda de corrección).** La evidencia anclada en `audit_log` (046 document,
019 workflow) no se reescribe: las migraciones 047 (document) y 020 (workflow) solo completan columnas
NUEVAS, fuera del hash de las anclas originales, y cada una deja su propia ancla
(`document.dependencia_codigo.backfill_047`, `workflow.dependencia_codigo.backfill_020`). «Mapeado» no es un
valor de `resultado` en document: se deriva de `historico_id IS NOT NULL` (en workflow la 019 ya lo escribió
y la 020 solo enlaza `historico_id`). El `despues` que la 020 reconstruye es la regla vigente **al
migrar** (`reconstruido = TRUE`, no una instantánea del momento del backfill); lo que no se puede probar
queda NULL. Un mapeo registrado después de la 046/019 no se refleja en la evidencia (deuda: job de
reparación).

### 6. Correcciones colaterales

`borrador_service` ya no elige `ORDER BY codigo LIMIT 1` (un nombre ambiguo es 422); `notification`
resuelve el buzón por código y, sin él, por el criterio único con historial (ya no una fila arbitraria);
el webhook recorta también `to_dept_code`/`from_dept_code`/`origin_dept_code`/`assign_to_dept_code`
(admisión inversa); el reporte por dependencia agrupa por código.

## Consecuencias

**Positivas**: renombrar una dependencia con trámite vivo ya no rompe bandeja, tenencia ni ubicación; las
homónimas dejan de confundirse; el código deja de poder cambiar bajo 8 tablas sin FK.

**Negativas / límites**:
- Un tenant con pasos vivos de nombre ambiguo **no migra** hasta que un administrador registre el mapeo.
- Las condiciones textuales (`contains`/`regex`) sobre un nombre siguen cambiando de sentido al renombrar:
  se listan en la evidencia (`conditions:textual`) y se documentan; no se bloquea el renombre por ellas.
- Un paso histórico sin código no se puede reactivar por `rollback` (el `CHECK` lo impide: `409
  paso_anterior_sin_codigo`).
- El alias por nombre debe retirarse en un incremento posterior (cuando el `Deprecation` esté a cero
  durante un ciclo): quitar el alias de los schemas y los índices por nombre (`ix_flow_steps_to_dept`,
  `ix_flow_steps_inbox`); la función con `historico=true` se queda para el replay.
- La copia de `resolver_codigo_dependencia` que usan los tests de integración de document y workflow debe
  coincidir con la de tenant-service: `test_dependencia_codigo.py` fija los mismos casos contra la real.

## Addendum (2026-10-20) — C-4, [ADR-040](ADR-040-cuadro-de-clasificacion-derivado-y-fases-de-la-trd.md)

- **Una nueva columna citante cubierta por el barrido**: `trd_serie_dependencia_productora.dependencia_codigo`
  (archive, migración `070`) casa con `_REF_COLUMNAS` (`dependencia\w*_codigo$`). Eliminar una dependencia que una
  versión de la TRD cita es `409 dependencia_con_historia` sin tocar tenant-service; `tests/integration/
  test_dependencia_sin_ciclos.py` fija el nombre de la columna.
- **El organigrama es un árbol** (tenant-service, migración `015`): trigger `BEFORE INSERT OR UPDATE OF parent_id`
  con candado consultivo por schema que rechaza el ciclo (`dependencia_ciclo`, `409` en el router); la migración
  aborta nombrando los códigos si ya hay uno. Antes solo se rechazaba `parent_id == id` y `/dependencias/tree`
  hacía desaparecer los nodos de un ciclo en silencio.

