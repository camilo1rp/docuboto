# ADR-040: El cuadro de clasificación se deriva del organigrama; la productora es contenido versionado de la TRD

**Estado**: Aceptado
**Fecha**: 2026-10-20
**Autores**: Giampiero (mantenedor principal); tanda C-4 del triage (C6, C8, C10)
**Relacionados**: [ADR-015](ADR-015-trd-ccd-modelo.md) (TRD/CCD), [ADR-025](ADR-025-trd-instrumento-convalidado-versionado.md) (instrumento convalidado y versionado), [ADR-026](ADR-026-tvd-fondo-acumulado.md) (TVD), [ADR-032](ADR-032-dependencia-por-codigo.md) (la dependencia por código), [ADR-012](ADR-012-migraciones-por-tenant.md) (migraciones)

---

## Contexto

Tres defectos distintos con una raíz común: **el mismo hecho escrito en dos sitios, o no escrito en ninguno**.

1. **La retención de una serie tenía dos juegos de números** (C6). `trd_series` guardaba `retention_years` /
   `total_retention` (el juego heredado de Orfeo) *y* `archivo_gestion_years` / `archivo_central_years` (lo
   que de verdad calcula el calendario). La respuesta derivaba el heredado de las fases… salvo cuando faltaba
   alguna, y entonces devolvía la columna heredada. Resultado medido en una TVD: `total_retention` valía 10 por
   omisión, `archivo_central_years` quedaba `NULL`, el calendario se calculaba con **0** (`or 0`) y la
   pantalla mostraba **10 años**. Es retención cero presentada como diez. Además «serie > subserie» (ADR-015)
   era una intención: nada impedía que una subserie tuviera hijos, y el EAD lo documentaba como válido.
2. **La TRD no decía quién produce la serie** (C8). El cuadro de clasificación documental (fondo, sección,
   subsección, serie, subserie) existe en el organigrama (E14) y en la TRD, pero nada los unía. Capturarlo a
   mano por segunda vez crea otra fuente que diverge; no capturarlo deja al Consejo aprobando un CCD que no
   dice quién produce cada serie. Y el organigrama admitía ciclos (A → B → A): `/dependencias/tree` hacía
   desaparecer sus nodos en silencio, de modo que derivar de él un cuadro habría convertido un dato corrupto
   en un cuadro **incompleto que parece completo**.
3. **«Publicable» no era una decisión de la entidad sobre el tipo** (C10). `set_publicable` excluía los
   tipos con `admite_ventanilla_publica` (Ley 1755: «se recibe del público») como sustituto de «no se
   difunde», acoplando dos marcas que dicen cosas distintas, y un radicado **sin tipo** sí podía publicarse.
   El `CLAUDE.md` de document-service afirmaba la decisión contraria a la que luego se tomó.

## Decisión

### C6 — Las fases son la única fuente; dos niveles, impuestos por el motor

**Alternativas para el juego heredado.** A) `DROP COLUMN` y derivar en lectura. B) columnas
`GENERATED ALWAYS … STORED`. B no compra nada que A no tenga —ningún otro servicio lee `trd_series.retention_years`
(barrido de `services/*`, exports e INT-01 incluidos)— y deja un segundo nombre para el mismo hecho: una columna
que *parece* dato y no lo es. **Se elige A** (migración `069`).

- `retention_years` y `total_retention` **dejan de ser columnas**. La API los sigue publicando en la
  **respuesta**, como derivados de solo lectura (`readOnly` en el OpenAPI): gestión y gestión + central.
- Los esquemas de **escritura** TRD/TVD pasan a `extra="forbid"`: un cliente viejo que mande
  `retention_years` recibe `422 extra_forbidden`, no un silencio que le haga creer que fijó una retención.
- Las dos fases son **obligatorias y sin valor por omisión** (0 es válido; ausente no): `CHECK
  trd_series_fases_check` — toda TRD declara gestión y central, toda TVD su fase central. La TVD **no** tiene
  gestión (ADR-026); su total de retención es su fase central. Se retiran los `or 0` del cálculo: una fase
  central ausente es un dato corrupto y falla (`500 trd_sin_fase_central`, y `total_trd_years → None` para el
  cableado WORM, que pospone), no se convierte en «sin retención».
- Los asientos de auditoría registran **lo escrito** (las fases), no el derivado.
- **Tope de dos niveles** en el trigger `assert_trd_parent_mismo_tipo`: el padre debe ser una serie raíz y una
  fila con hijos no puede ganar padre (`trd_jerarquia_dos_niveles`). El servicio responde `422` antes que el
  motor; las subseries apuntan a la *fila-versión* del padre, así que para «¿tiene subseries?» se pregunta por
  el **código** del padre.

### C8 — Serie ↔ dependencia productora, y cuadro derivado

**Alternativas para la relación.** A) columna `dependencia_productora_codigo` en `trd_series` (una por serie).
B) tabla N:M por **versión** de la serie. C) N:M por **código** de serie. A impone una sola productora, falso
en la práctica archivística (la misma serie en varias oficinas). C reescribe el CCD de versiones ya
convalidadas cada vez que el organigrama cambia, violando «la reorganización no reescribe historia». **Se
elige B** (migración `070`, `trd_serie_dependencia_productora`).

- La dependencia se cita por **`dependencias.codigo`** (ADR-032) —inmutable y nunca reutilizada— y **sin FK**:
  la tabla es de tenant-service y el orden de migración entre servicios no está garantizado. La columna se
  llama `dependencia_codigo` a propósito: casa con el barrido `_REF_COLUMNAS` de tenant-service, así que
  eliminar una dependencia que una TRD cita es `409 dependencia_con_historia` **sin tocar tenant-service**.
- La productora es **contenido de la versión**: solo se escribe en `borrador` (trigger), se **reemplaza entera**
  por `PATCH` o al crear/versionar, y solo existe en la **serie** (las subseries la heredan; la TVD tiene
  `fondo_nombre`). Una versión nueva **clona** la de su base; si alguna ya no está activa, falla con la lista y
  el cliente reenvía la explícita. Así entra una productora en una TRD convalidada: versión `N+1` en borrador,
  aprobar, convalidar. No hay vía en sitio: es contenido convalidado por el Consejo, no política técnica como
  `pdfa_profile`.
- **Gates**: `aprobar` y `convalidar` (incluida la ratificación `migrada → convalidada`, que no pasa por
  `aprobar`) responden `409 trd_serie_sin_productora` si la serie TRD raíz no declara ninguna. El gate va
  **dentro de la transacción y tras el `UPDATE` de estado**: desde `aprobada` el trigger ya no deja tocar la
  lista, así que no hay carrera con un `PATCH` que la vacíe, y el 409 revierte el cambio de estado.
- **Ruta congelada**: al convalidar se escribe `ruta_convalidada` (raíz → productora, del organigrama vivo)
  una sola vez (fill-once, trigger). Si el árbol no se puede leer o tiene un ciclo, la convalidación entera
  falla (`503` / `409 organigrama_con_ciclo`).
- **Frontera de lectura (S1)**: la declarada en `trd.py` —el vocabulario es abierto, el circuito exige
  `USUA_PERM_TRD`— ahora se cumple: `GET /api/v1/trd` devuelve por omisión solo `convalidada`/`migrada` y sin
  `acta_comite`, `fecha_aprobacion_comite` ni `created_by`; las versiones en trámite (`?incluir_en_tramite=true`) y
  `GET /trd/cuadro?incluir_borradores=true` responden 403 sin el permiso, y un borrador por id es 404 para quien
  no lo tiene. El acto de convalidación (resolución, fecha, instancia) sigue siendo público.
- **Cuadro**: `GET /api/v1/trd/cuadro`. Fondo = la entidad (`sgd_param_admin.entidad_nombre`); sección =
  dependencia raíz del camino raíz → productora; subsección = cada nivel inferior (la profundidad es la del
  organigrama); serie = la **vigente** por código (con `incluir_borradores`, además la en trámite); subserie =
  vigente por su propio código, agrupada por el **código** del padre. Una serie con N productoras aparece bajo
  cada una (`compartida`). **Una serie convalidada se ubica por la ruta que fijó la convalidación** —lo que el
  Consejo aprobó, y lo que ya usa el EAD— y la ruta viva solo se dice como aviso
  (`ruta_difiere_de_la_convalidada`, `organigrama_reorganizado_tras_convalidacion`); `?segun=vivo` la ubica en
  el organigrama actual. Sin instantánea (`migrada`, `borrador`) solo existe la ruta viva. Los nodos se
  identifican por su CAMINO, no por su código: tras una reorganización la misma dependencia puede aparecer bajo
  dos padres. Se arma en **una** transacción `REPEATABLE READ` de solo lectura con una función
  pura (`armar_cuadro`) que se prueba sin base de datos. Nada se omite en silencio: lo que no se puede ubicar
  va a `sin_seccion` con su motivo, y los avisos dicen lo que el organigrama cambió.
- **Ciclos, en dos capas** que no se sustituyen: *prevención* en tenant-service (migración `015`: trigger
  `BEFORE INSERT OR UPDATE OF parent_id` con candado consultivo por schema —sin él, `A.parent=B` y `B.parent=A`
  concurrentes pasan las dos comprobaciones— que sube desde el nuevo padre; la migración **aborta** listando
  los códigos si ya hay un ciclo; el router lo mapea a `409 dependencia_ciclo`) y *detección* en archive al
  leer: la CTE **sube desde cada nodo** (`CYCLE id SET es_ciclo`), porque bajando desde las raíces los nodos
  de un ciclo, que no son raíz de nada, nunca se alcanzarían y el cuadro saldría incompleto sin avisar.
- **Reorganizaciones**: un renombre se muestra con el nombre vigente (la instantánea y la ruta convalidada
  conservan el de entonces); un cambio de padre produce `ruta_difiere_de_la_convalidada` y el aviso
  `organigrama_reorganizado_tras_convalidacion` —el sistema lo **dice**, no reescribe la versión—; una
  desactivación deja la serie bajo la dependencia con `activo:false` y aviso; la eliminación es imposible
  mientras una versión la cite.
- **Retención mínima (A7)**: `CHECK trd_series_retencion_minima_check` — una TRD suma al menos un año entre
  las dos fases (`0 + 0` no es «sin retención»: es un calendario que vence al cerrar y un candado WORM que
  el cableado pospone para siempre); 422 `trd_retencion_minima` antes que el 23514.
- **Concurrencia**: el tope de dos niveles toma un candado consultivo por schema en el trigger de la `069`
  (alta de una subserie y «la serie pasa a ser subserie» concurrentes formaban tres niveles); la comprobación
  de la dependencia productora se hace DENTRO de la transacción con `FOR SHARE` (tenant-service no puede
  desactivarla entre la comprobación y la escritura); `update_tipo` lee la fila `FOR UPDATE`.
- **OAI-PMH / EAD**: el fondo se titula con el nombre de la entidad (sin nombre, se conserva el título por
  omisión) y entre `fonds` y `series` se emite un `c[level="subfonds"]` por cada nodo de la ruta
  (sección y subsección = subfondo en ISAD(G); `unitid` = el CÓDIGO de la dependencia —inmutable, ADR-032—
  y la sigla, que es mutable, va en el título), **solo si la serie tiene exactamente una productora**, con la
  ruta convalidada y, sin ella, la viva. Con cero o varias no se emite el nivel: el expediente no lleva columna
  de dependencia y elegir una sería inventar. El endpoint es público: un ciclo o un catálogo ilegible degrada
  a «sin subfondos», no a un error.

### C10 — `tipos_documentales.publicable`, marca propia

- `publicable BOOLEAN NOT NULL DEFAULT false` (migración `071`): la entidad declara que el **tipo** entra en
  su esquema de publicación (Ley 1712/2014 art. 12). Ortogonal a `admite_ventanilla_publica`. Es **política de
  difusión**, no contenido convalidado de la TRD: editable en sitio, asentada
  (`archive.tipo_documental_publicacion_cambiada`, antes/después, misma transacción) y con **`USUA_PERM_TRD`
  nivel 3** (las demás ediciones del catálogo conservan su nivel).
- **Reactivar** un tipo que ya es `publicable` (`activo` false → true) también exige nivel 3: reabre la
  puerta de la publicación. Esa decisión se toma dentro del servicio, sobre la fila bloqueada, y la traza de
  la denegación no se revierte con el 403.
- **Gate** (document-service, `set_publicable`, en el `WHERE`): solo al publicar, `canal <> 'ventanilla_publica'`
  **y** `EXISTS (tipo activo y publicable)`. `EXISTS` y no `NOT EXISTS`: el opt-in invierte el sentido, y un
  radicado sin tipo o con un tipo desconocido **no** se publica (antes sí). El canal sigue excluido siempre
  (Ley 1581: lo recibido de terceros). Retirar no lleva estas condiciones (fail-safe).
- Si lo **único** que falla es el tipo, `409 tipo_documental_no_publicable` en vez de un 404 que miente sobre
  la causa; cualquier otro motivo conserva el 404 neutro. La comprobación diagnóstica repite el mismo gate de
  clearance/nivel/anulado/canal, de modo que no es un oráculo.
- **No se lee en vivo en el chokepoint `_PUBLIC`**: desmarcar un tipo no despublica en silencio radicados ya
  difundidos (habría `radicados.publicable=true` sin servirse: dos verdades). El `PATCH` que pasa `publicable`
  de true a false responde `publicados_vigentes` (cuántos siguen publicados, o `null` si no se pudo contar) y el
  retiro es un acto explícito por radicado.

## Consecuencias

- Es un cambio **incompatible** de contrato (los datos son de prueba, no hay compatibilidad ni backfill): quien
  enviaba `retention_years`/`total_retention` recibe 422; los scripts de siembra y las filas demo que no
  declaren fases o productora hay que repararlos o recrearlos. La migración `069` aborta nombrando las filas.
- Desplegar **archive antes que document** (la `071` crea la columna que document-service lee; si no, la
  consulta falla con `UndefinedColumn` ⇒ 500 = fail-closed) y **tenant-service con la `015`** antes de confiar
  en el cuadro (sin ella, la prevención de ciclos no existe, aunque la detección sí).
- Una `migrada` existente SÍ puede ratificarse: `POST …/convalidar` acepta `dependencias_productoras` solo
  cuando el origen es `migrada`, y las escribe una vez, en la misma transacción y antes del gate, con asiento
  propio `archive.trd_productora_declarada` («captura de dato de TRD preexistente»); el trigger de la `070`
  deja AÑADIR (nunca quitar ni cambiar) la productora en una versión `migrada`. Sin ese camino la ratificación
  D6 de ADR-025 habría quedado muerta para toda serie raíz y las marcas `cerrado_bajo_serie_migrada`
  irresolubles. Una `aprobada` no la recibe en el body (422 `productora_solo_en_ratificacion`): la declaró en
  `borrador`. Mientras no se ratifique, la `migrada` aparece en `sin_seccion`.
- `compute_retention` y el cableado WORM dejan de tolerar una fase central ausente.

## Fuera de alcance (declarado)

- **Una subserie hereda TODAS las productoras de su serie** y no puede expresar plazos distintos por
  oficina: no hay productora por subserie (decisión del orquestador, A3). Una serie compartida repite sus
  subseries bajo cada productora del cuadro.
- **Deuda de publicidad del CCD**: el cuadro convalidado debería poder leerse públicamente (Ley 1712/2014,
  art. 9; Res. MinTIC 1519/2020 [confirmar el anexo exacto]) y hoy solo lo ve el autenticado del tenant.
  Tampoco hay CCD de la **TVD** de fondo acumulado (Ac. AGN 002/2004: cuadro de clasificación del fondo
  acumulado), que no lleva productora, ni un **acto de adopción del esquema de publicación** (Ley 1712,
  art. 12) que asiente cuándo y con qué fundamento la entidad aprobó el conjunto de tipos publicables:
  hoy cada cambio de `publicable` deja su asiento, pero no existe el acto colegiado que los reúne.
- Cuadro «a fecha» histórica: tenant-service no guarda historial de `parent_id`; la `ruta_convalidada` es la
  evidencia de lo aprobado.
- La TVD no lleva dependencia productora (tiene `fondo_nombre`).
- Un catálogo cerrado de fundamentos de publicación y la publicación por serie/expediente siguen pendientes.
