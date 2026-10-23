# ADR-043: Retención legal por motivo, préstamo con prestatario identificado y soporte citado por una constancia

**Estado**: Aceptado
**Fecha**: 2026-10-21
**Autores**: Giampiero (mantenedor principal); tanda C-5
**Relacionados**: [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM y legal hold), [ADR-026](ADR-026-tvd-fondo-acumulado.md) D4.4 (fondo acumulado; enmendado), [ADR-030](ADR-030-constancia-firma-inobtenible.md) (constancia de firma inobtenible; enmendado), [ADR-003](ADR-003-asyncpg-sin-orm.md) (SQL crudo), [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) (schema por tenant)

---

## Contexto

Tres hallazgos de la revisión de dominio, verificados en el código, comparten causa: una garantía que vivía **entre** piezas y no en ninguna.

| # | Hecho | Consecuencia |
|---|---|---|
| H1 | El legal hold era un booleano por objeto, sin motivo | Levantarlo «por valoración» liberaba también un hold judicial |
| H2 | Un hold sobre un expediente sin objetos WORM daba 404; un objeto creado después de fijar el hold nacía sin él | No se podía congelar lo que aún no tenía AIP |
| H3 | La admisión del lote de eliminación no miraba el hold; con `worm_retain_until` NULL el `COALESCE(..., '-infinity')` lo daba por vencido | Un lote se avalaba y fallaba al ejecutar |
| H4 | «Unidad ya prestada» se comprobaba fuera del INSERT | Dos préstamos simultáneos pasaban los dos |
| H5 | `prestado_a`/`prestado_por` eran texto del cliente; el clearance del prestatario no se miraba; `ON DELETE CASCADE` borraba la custodia | Un préstamo no probaba a quién se entregó |
| H6 | Nada impedía transferir o eliminar un expediente cuya unidad estaba prestada; la tool MCP afirmaba lo contrario | Descripción falsa como vector |
| H7 | La purga por disposición no bloqueaba la fila ni miraba referencias probatorias | Un anexo citado por una constancia vigente podía destruirse |
| H8 | El clamp de D4.4 dejaba COMPLIANCE irreversible ~400 días sobre contenido de un fondo cuyo calendario ya venció | Una retención que nadie pidió y que no se puede deshacer |

## Decisión

### 1. Registro de retenciones legales por motivo (storage)

`preservacion_legal_hold` (migración storage `024`): una fila por acto, con `motivo_tipo` en el catálogo cerrado `judicial | disciplinario | fiscal | administrativo | ddhh_memoria_historica | valoracion_pendiente`, fundamento, referencia al acta, quién y cuándo fijó y levantó. Solo admite el UPDATE que fija `levantado_*` desde NULL; DELETE y TRUNCATE se rechazan.

- El estado S3 de un objeto es **«existe al menos un hold activo del expediente»**. Levantar un hold solo libera S3 si tras el levantamiento no queda ninguno: un levantamiento por valoración **no** libera uno judicial concurrente.
- Levantar exige repetir el `motivo_tipo` (409 `motivo_no_coincide`): un acto no puede levantar el hold de otro.
- Fijar **no exige objetos WORM** (cierra H2) y todo objeto protegido nace con `legal_hold = EXISTS hold activo`, resuelto por el servidor bajo el mismo advisory lock que serializa fijar/levantar.
- `valoracion_pendiente` es un hold **de sistema**: la API pública no lo fija ni lo levanta; solo la valoración del Comité.

### 2. Fondo acumulado con calendario vencido: solo hold, sin retención (enmienda a ADR-026 D4.4)

Para los tipos de contenido (`indice`, `aip`): si el expediente es de un fondo acumulado y `fecha_extrema_final + total` ya venció, **no se fija retención**: queda el hold `valoracion_pendiente` (`retencion_origen = 'valoracion_pendiente'`, `retain_until` NULL). Si el calendario es futuro, COMPLIANCE natural con base en la fecha extrema final. Nunca se fija un `retain_until` mayor que el natural. El margen deja de ser «clamp» y queda solo como umbral del piso (`worm_piso_margen_dias`).

Las **actas de transferencia y sus derivadas** quedan fuera: su base es el acto (`firmado_at`); no posponen la disposición del contenido y deben sobrevivir a él como prueba.

El Comité registra la valoración de la TVD (`POST /tvd/{serie_id}/valoracion`, solo con TVD aprobada o convalidada). Por expediente amarrado: si la disposición es CT se **fija** la retención (por primera vez) y **después** se levanta el hold; si es E solo se levanta; **M (medio técnico) se trata como CT** (el contenido que protege storage ya está en soporte electrónico: se conserva); **S (selección) no levanta nada por sí sola** (ver «Selección» abajo). El orden es conservador: un fallo entre los dos pasos deja el objeto retenido, nunca desprotegido. Una fase de barrido (`run_once_valoracion_holds`) reintenta con backoff hasta `agotada`.

**Selección (S) — acto por expediente (auditoría de cierre del lote C).** Una valoración con disposición `S` exige declarar el **método de muestreo** (`tvd_valoracion.metodo_muestreo`, ≥ 10 caracteres, `CHECK` y 422 `metodo_muestreo_requerido`) y deja cada expediente en `seleccion_pendiente` (valorado, sin aplicar, sin selección): el hold `valoracion_pendiente` **sigue**. El Comité decide expediente por expediente (`POST /tvd/{serie}/valoracion/expedientes/{expediente}/seleccion`, `USUA_PERM_TRD` crud ≥ 3, asiento `archive.expediente_seleccion_registrada`, `GET .../seleccion-pendiente` para la cola con solo id y código): `conservar` sigue el camino de CT (fija la retención y después levanta); `descartar` levanta el hold sin retención y el expediente queda elegible para el lote. La selección es fill-once (trigger) y el motor no deja marcar aplicada una valoración S sin ella. Un expediente `conservar` **nunca entra a un lote** (`seleccionado_para_conservacion`). **Residuo declarado:** la renovación rodante de la retención (fase 5) solo selecciona series con snapshot CT; un expediente `S` + `conservar` conserva la retención inicial pero no se renueva automáticamente (hay que extender `CT_DISPOSITION_CODES`/el filtro a la selección en un siguiente incremento).

**Costo declarado**: sin red WORM irreversible para contenido vencido, compensada por el hold. El lote de eliminación **no admite** un expediente con hold activo (condición 6, en el `WHERE` de la consulta de candidatos; tabla ausente ⇒ rechazo fail-closed).

### 3. Préstamo con prestatario identificado (archive)

- Interno: `prestatario_user_id = auth_users.id`, validado en el `INSERT ... SELECT` contra usuarios activos (un `sub` de Keycloak es un UUID válido, pero no existe en `auth_users`). Externo: **instantánea en la fila** (nombre y documento obligatorios; entidad, cargo, correo y teléfono opcionales). No hay FK a un directorio de terceros ni a `auth_users` (tabla de otro servicio).
- Reglas en el `WHERE`/motor, nunca en una comprobación previa: índice único parcial `ux_prestamo_unidad_activo` más advisory lock sobre la **raíz** del árbol de unidades (padre e hijo se serializan); clearance del prestatario (externo: solo contenido público) además del de quien presta; no se presta lo que está en transferencia o en lote de eliminación vivo.
- **Simetría**: transferir o eliminar lo que está prestado, o vincular un expediente a una unidad prestada, da 409 `unidad_prestada`; defensa en el motor con triggers (`transferencias`, `expedientes`).
- `prestado_por` lo resuelve el servidor; la devolución fija `devuelto_recibido_por`. La FK a la unidad pasa a `RESTRICT` y un trigger deja como único UPDATE posible `prestado → devuelto`: la custodia no se borra ni se reescribe.
- El asiento de auditoría enmascara el documento del tercero (`****1234`); la fila lo guarda completo (Ley 1581: finalidad de custodia). **Los listados** (`GET /prestamos`, `GET /unidades/{id}/prestamos`) y la respuesta de devolver **enmascaran** documento, correo y teléfono del prestatario externo salvo con `USUA_PERM_EXPEDIENTE` crud ≥ 3 (quien presta); quién tiene la unidad (nombre, entidad, cargo) se ve con cualquier nivel. Sin identidad, enmascarado.
- **`prestatarios-elegibles` infiere el clearance de los usuarios** (los que aparecen cubren el árbol de la unidad; los que no, no): el endpoint es el oráculo de eso por diseño, porque la UI no debe ofrecer a quien el backend rechazará. Se acepta y se declara; no devuelve el nivel, exige `USUA_PERM_EXPEDIENTE` y, desde el cierre, una unidad inexistente responde 404 (y el repositorio no devuelve el directorio entero por sí mismo).

### 4. Anexo citado por una constancia vigente (storage ↔ archive)

Archive publica la vista contrato `v_anexo_citado_vigente` (migración archive `075`): anexos referenciados por una constancia de firma inobtenible no revocada y sin baja avalada. Storage la consulta al borrar.

**Sin TOCTOU**: bajo READ COMMITTED, `SELECT ... WHERE NOT EXISTS (vista) FOR UPDATE` re-evalúa tras esperar el bloqueo solo la fila de `files`; la subconsulta usa la instantánea vieja. El patrón es **bloquear la fila y comprobar en otra sentencia**: (1) `FOR UPDATE` de `files`; (2) sentencia aparte contra la vista; (3) WORM; (4) borrado. El lado que cita toma `FOR SHARE` sobre `files` en la misma transacción del INSERT de la constancia: o la constancia espera y ve el archivo borrado, o la purga espera y ve la constancia. Vista ausente ⇒ el archivo se trata como citado.

**Salida cuando llega la disposición TRD** (no un bloqueo eterno): **baja de soporte avalada por el Comité**, congelada al proponer el lote (`eliminacion_lote_soporte_citado`). Es aviso, no rechazo: viaja en la respuesta, el inventario y el acta de eliminación. La vista excluye la cita solo si hay baja congelada en un lote `avalado|ejecutado`. Una constancia declarada después de proponer no tiene baja ⇒ el expediente cae en `fallidos` con `soporte_citado_no_avalado`. La constancia firmada no se toca (append-only): su `soporte_estado` se deriva (`vigente | baja_avalada | eliminado_por_trd`).

### 5. Contrato SQL entre servicios en el mismo schema

Todos los servicios usan el mismo schema `tenant_{slug}` y el mismo rol de aplicación. El acoplamiento SQL se **declara**, en lista cerrada, con el dueño de cada nombre:

| Objeto | Dueño | Lector/bloqueador |
|---|---|---|
| `REFERENCIAS_DE_ARCHIVO` (tablas de document que citan un archivo) | document | storage (gate de borrado y purga de huérfanos) |
| `v_anexo_citado_vigente` | archive | storage (gate de borrado) |
| `preservacion_legal_hold` | storage | archive (admisión del lote, lectura de holds) |
| `files` (`FOR SHARE`) | storage | archive (al citar un anexo) |
| `auth_users` | auth | archive (validación y clearance del prestatario) |

**Declarado (cierre del lote C): el asiento de archive de un legal hold queda fuera de la transacción de storage.** Fijar o levantar un hold escribe su asiento en storage **dentro** de la transacción que cambia la fila (atómico), y archive-service asienta su propio acto (`archive.expediente_legal_hold_*`) **después** de que storage responde, en otra transacción de otro servicio. Si archive cae entre ambos, el hold existe y tiene su asiento de storage pero falta el de archive. No hay transacción distribuida; la traza de storage es la que manda y la de archive es su reflejo con el actor del acto.

**Baja de soporte (cierre del lote C).** `eliminacion_lote_soporte_citado.anexo_sha256` pasa a `NOT NULL` y proponer un lote **rechaza** (409 `soporte_sin_huella`) si una constancia vigente cita un anexo sin huella SHA-256: sin ella no hay qué avalar destruir. Un trigger niega `DELETE` de `eliminacion_lotes` en `avalado|ejecutado` (el `CASCADE` arrastraría la baja de soporte y el inventario, que son la evidencia de lo destruido). Migración archive `076`.

**Motivo `ddhh_memoria_historica`** (información de derechos humanos y memoria histórica; Ley 1448 de 2011 art. 144 **[confirmar]**; storage `025`). Criterio de levantamiento: no lo levanta el vencimiento de la retención de la TRD ni una valoración; solo un acto de autoridad competente, y el servicio exige un fundamento de levantamiento de al menos 50 caracteres (archive y storage), además de repetir el motivo.

Reglas: fail-closed con `to_regclass` (objeto ausente ⇒ se niega), y «bloquear y luego comprobar en otra sentencia». Antes de aplicar la migración `075` de archive, storage bloquea **toda** purga (la vista no existe): declararlo en el runbook de migraciones.

## Consecuencias

- Un fondo vencido deja de recibir COMPLIANCE no pedido; queda retenido por el hold hasta que el Comité valora.
- Un expediente con hold activo no se elimina ni se avala en lote.
- Un préstamo prueba a quién se entregó y quién entregó; la custodia no se borra.
- Un anexo citado por una constancia no se destruye en silencio; su destrucción exige un acto avalado.
- Los objetos que ya estén bajo el COMPLIANCE del clamp anterior no se acortan (imposible por diseño WORM); el entorno de prueba se recrea.
- Sin compatibilidad: el `POST /expedientes/{id}/legal-hold` y `POST /preservacion/legal-hold-internal` anteriores se retiran; `prestado_a` desaparece.

## Preguntas abiertas para la auditoría archivística

Base de las actas de fondo (acto, no fecha extrema final) — **respondida: `firmado_at` del acta, conforme**; si la selección (`S`) debe exigir algo más que levantar el hold — **respondida: sí, un acto por expediente con método de muestreo declarado (arriba)**; el catálogo cerrado de `motivo_tipo` — **suficiente, con `ddhh_memoria_historica` añadido**.
