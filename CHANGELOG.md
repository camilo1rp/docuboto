# Changelog — OrpycaMCP

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.0.0/) + [Conventional Commits](https://www.conventionalcommits.org/)

---

## [Unreleased]

### Added

- `fix(document)`: **auditoría de la creación masiva de radicados (`POST /api/v1/batch/documents`) + reactivación de `tests/test_batch.py`**
  `BatchService.process_batch_documents` creaba radicados —documentos oficiales con número inmutable, Ley 594/2000/Ac. AGN 060/2001— sin dejar ningún asiento en `public.audit_log`: control positivo decisivo, su gemelo exacto `archive-service/app/services/batch_service.py` (creación masiva de expedientes) sí auditaba desde su implementación. Cerrado con el mismo patrón ya validado en el servicio (`DocumentService.create`/`AnulacionService`): cada ítem del lote queda envuelto en su propio `async with self.conn.transaction():` (secuencia + `INSERT radicados` + marca de éxito + `audit.append`, todo o nada por ítem, con el `except` de fallo FUERA de ese bloque) y deja un asiento `document.radicado_created` — **uno por radicado creado**, no uno por lote, para no perder la granularidad que el índice electrónico y la trazabilidad forense necesitan. El `actor` es el `X-User-Id` que validó el router al encolar el job (`RETURNING created_by` sobre `batch_jobs`), nunca un campo del body ni algo resuelto en la propia tarea asíncrona detached. Un lote de 480/500 exitosos deja exactamente 480 asientos y 20 ítems `failed` — el resultado refleja lo que realmente entró, no la intención del lote.
  Además, `tests/test_batch.py` estaba **entera bajo `pytest.mark.skip`** desde su commit original (fixture `test_client` inexistente, sin cabeceras de tenant/usuario) — deuda que dejaba sin ejercitar el gate `require_permission("PERM_RADI")` recién añadido a los dos endpoints y la ruta renombrada `GET /api/v1/batch/documents/{job_id}/status`. Reescrita sobre las fixtures reales del servicio (espejo de `archive-service/tests/test_batch.py`): 403 sin `PERM_RADI` en ambos endpoints, 400 por tenant ausente (comportamiento real de este router, distinto del 403 del gemelo de archive), 202/200/404 del happy path, y dos tests de servicio que ejercitan `process_batch_documents` directamente (N radicados → N asientos con actor/payload correctos; fallo a mitad de ítem revierte también su asiento, sin contaminar los demás).
  *Why*: creación masiva de documentos oficiales sin traza de quién/cuándo es exactamente el hueco que la auditoría inmutable existe para cerrar; una suite de tests enteramente saltada no prueba nada, incluidos gates de autorización recién añadidos.
  *Impact*: sin migración (`audit_log` ya existe); `batch_service.py` deja de escribir el `INSERT INTO radicados` fuera de transacción. Suite de `document-service`: **284 passed, 20 skipped** (antes 273 passed, 25 skipped — los 20 restantes son integración contra Postgres real gateada por `RUN_INTEGRATION=1`, sin relación con este cambio).

- `feat(frontend)`: **`/admin/interoperabilidad` — exportar/importar paquetes de radicados + carga masiva de documentos y expedientes + rastreo postal (Fase 8, cierre del desfase API↔UI, E11/E20)**
  Última fase del cierre del desfase API↔UI: cinco endpoints de `document-service`/`archive-service` ya existían sin ningún consumidor en el frontend. Nueva pantalla `/admin/interoperabilidad` con cuatro secciones: **Exportar** (`POST /api/v1/export` → ZIP descargado en cliente, con contadores de incluidos/excluidos por nivel de seguridad); **Importar** (`POST /api/v1/import`, `Stepper` subir→validación→confirmar→resultado — la validación de fixity/esquema ocurre ATÓMICA en el backend, no hay paso "solo validar" independiente); **Carga masiva de documentos** (`POST /api/v1/batch/documents`, 1-1000 ítems) y **Carga masiva de expedientes** (`POST /api/v1/batch/expedientes`, 1-100 ítems), ambas con confirmación explícita del número exacto de ítems antes de ejecutar y sondeo de progreso cada 2s. Se añadió también "Actualizar rastreo" en `/envios` (`GET /api/v1/envios/{id}/tracking`, consulta PULL contra el operador, F5).
  Tres decisiones de honestidad de interfaz: (1) el `422` de importación trae en `detail` el archivo/checksum EXACTO que falló — se muestra tal cual, nunca se resume en "no se pudo importar"; los `antecedentes_no_resueltos` se listan por número de radicado, no solo se cuentan; (2) el sondeo de jobs distingue explícitamente "no se pudo consultar el estado" (fallo del proxy) de "aún sin resultados" (job legítimamente en `pending`) — nunca sintetiza contadores en cero; (3) el rastreo postal declara `operador_conectado=false` cuando no hay integración en vivo, en vez de aparentar una consulta real al operador (E11 INT roadmap, conector concreto pendiente).
  **Hallazgo de alcance dejado por este incremento, ya cerrado en la entrada `fix(api-gateway,document,archive)` de más abajo**: el gateway no tenía registrado el prefijo `/api/v1/batch/`, así que las dos secciones de carga masiva devolvían `404 route_not_found`. Al cerrarlo, el contrato del sondeo **cambió de forma** (`GET /api/v1/batch/{job_id}/status` → `GET /api/v1/batch/{documents,expedientes}/{job_id}/status`).
  **Defecto conocido, ya cerrado** por la entrada `fix(frontend)` de más abajo (revisión UX de 2026-08-02, hallazgos C1 y C2): los dos proxies de sondeo se quedaron con la forma de URL antigua y el panel de progreso no era reactivo, así que la carga masiva no reportaba resultado en ninguna circunstancia. Exportar, importar y rastreo postal sí operaron de punta a punta desde el principio.
  *Why*: cierre de la Fase 8 (última) del plan de cierre del desfase API↔UI — interoperabilidad, carga masiva y rastreo postal quedaban sin UI pese a estar completos en el backend.
  *Impact*: 1 ruta nueva (`/admin/interoperabilidad`) + tarjeta en `/admin` (`USUA_PERM_EXPEDIENTE`); 6 proxies SSR nuevos (`exportar`, `importar`, `lotes/documentos[/[jobId]]`, `lotes/expedientes[/[jobId]]`) + 1 proxy nuevo en `/envios/api/[id]/tracking`; 2 tipos JSDoc nuevos (`ImportResult`, `BatchJobStatusResponse`) + `TrackingResponse`. 27 tests nuevos (proxies: guard 401, allowlist campo a campo, `encodeURIComponent`+validación UUID en `[jobId]`, streaming/saneo de `Content-Disposition` en la descarga ZIP, 503 explícito del sondeo ante fallo de conexión); suite completa **1322/1322** (antes 1295), `svelte-check` 0 errores.

- `feat(archive)`: **TVD — Tabla de Valoración Documental de fondo acumulado ([ADR-026](docs/es/adr/ADR-026-tvd-fondo-acumulado.md), Increment 1: registro y convalidación, migración 039)**
  Una entidad con **fondo acumulado** (documentación ya producida, sin criterio archivístico, típicamente de dependencias suprimidas o entidades liquidadas) no tenía dónde registrar su instrumento de valoración: el gate D5 de disposición exigía un instrumento convalidado que no tenía forma de existir. La TVD entra como **discriminador `tipo_instrumento ∈ {TRD, TVD}` sobre `trd_series`**, no como tabla propia — bifurcar `expedientes.trd_serie_id` (el seam por el que nueve puntos derivan consecuencias jurídicas, siete alimentando `retain_until` WORM COMPLIANCE, irreversible) es el diseño que el ADR descarta. **Cuatro capas de blindaje**: (1) espacio de `code` único y compartido con TRD (los índices de la 037 no se tocan; `409 instrumento_code_en_uso` con el tipo del ocupante); (2) dos vistas `v_trd_series`/`v_tvd_agrupaciones` con `WITH CHECK OPTION` — `list_trd_series` pasa a `FROM v_trd_series`, cerrando el `SELECT * FROM trd_series WHERE 1=1` que era la consulta de la clase "filtro olvidado"; (3) CHECK por tipo en ambas direcciones (`trd_series_tvd_forma_check`/`trd_series_trd_forma_check`); (4) trigger `trg_expedientes_pin_tipo` en forma **ESTRICTA**: rechaza CUALQUIER expediente amarrado a una TVD — riesgo WORM **cero**, garantizado por el motor. Router `/api/v1/tvd` espejo 1:1 de `/api/v1/trd` (circuito completo: crear/PATCH/versiones/aprobar/devolver/convalidar/registrar-rusd/derogar, reutilizando íntegro el repositorio type-agnostic del circuito de ADR-025), `tipo_instrumento` fijado por el router (nunca del cliente), permiso reutilizado `USUA_PERM_TRD`, asientos de auditoría distintos (`archive.tvd_*`). Añadido también el gemelo `GET /api/v1/{trd,tvd}/{code}/versiones` (declarado por ADR-025 y nunca implementado). Ruta registrada en `api-gateway`.
  *Why*: hallazgo de `archival-compliance-auditor` sobre el modelo TRD (ADR-025): "todo el modelo es exclusivamente TRD; una entidad con fondo acumulado no tiene dónde poner sus TVD" — sin instrumento, ningún fondo acumulado puede cerrarse ni disponerse dentro del sistema.
  *Impact*: migración `039_tvd_instrumento_valoracion.sql`; endpoints nuevos bajo `/api/v1/tvd` + `GET /api/v1/trd/{code}/versiones` nuevo; `TrdSerieResponse` gana campos aditivos (`tipo_instrumento`, `fondo_nombre`, `fecha_extrema_inicial`, `fecha_extrema_final`, `justificacion_valoracion`) — sin ruptura de contrato para TRD existente. Una TVD **no gobierna ningún expediente todavía** (Increment 2, pendiente): el trigger estricto lo impide en el motor. 721 tests unitarios (antes 701, +20) + 164 de integración contra Postgres real (antes 151, +13: dos schemas de tenant, CHECK/vistas/trigger verificados por mutación), sin roturas.

- `feat(frontend)`: **pantalla `/admin/tvd` + circuito de convalidación compartido con `/admin/trd` ([ADR-026](docs/es/adr/ADR-026-tvd-fondo-acumulado.md) Increment 1, frontend)**
  Nueva pantalla `/admin/tvd`, espejo estructural de `/admin/trd` pero con columnas propias (**fondo/productora**, **fechas extremas**, **estado del instrumento**) y **sin** columna de archivo de gestión — no existe para TVD, y mostrarla vacía habría sugerido un dato faltante en vez de una fase inexistente. El formulario de creación trata `justificacion_valoracion` como `textarea` principal y obligatoria (es el objeto del instrumento, ADR-026 D5, no una nota secundaria) y valida en el proxy SSR fechas extremas/fondo/justificación **antes** de llegar al `CHECK` del backend, con mensaje legible. El circuito completo (aprobar → convalidar → registrar RUSD → derogar, más devolver) llega como componente **compartido** `InstrumentLifecycleActions.svelte` (`lib/components/features/admin/`), parametrizado por instrumento (`basePath`/`instrumentLabel`) — ADR-026 pedía explícitamente no construirlo dos veces, y como `/admin/trd` todavía no tenía esta UI, se construyó una vez y se conectó **también** a `/admin/trd` para que ambas pantallas resuelvan el mismo circuito de la misma forma. Los actos irreversibles (**convalidar**, **derogar**) muestran la advertencia dentro del `Modal` de confirmación, antes del botón de confirmar, nunca como tooltip ni después de un 409; cada botón de transición se gatea por `item.estado` — la UI nunca ofrece un acto que el backend rechazaría. Edición append-only: el `PATCH` completo solo se ofrece con la versión en `borrador`; fuera de ese estado el formulario deshabilita todo salvo `pdfa_profile` y el proxy solo reenvía ese campo (enviar los demás sin cambios habría disparado igualmente un `409`, porque el backend marca "cambio sustantivo" por presencia del campo, no por diferencia de valor).
  Se verificó `TrdSuggestChips.svelte` (preocupación explícita del ADR: que la sugerencia de clasificación de radicación pudiera proponer una TVD): su proxy (`/radicar/api/rag/antecedentes/+server.js`) resuelve `doc_class` consultando `GET /documents/by-tracking/{tracking}` de precedentes — nunca toca `trd_serie_id` ni `v_trd_series`/`v_tvd_agrupaciones`. No había nada que corregir.
  *Why*: ADR-026 §"Qué necesita el frontend" — sin pantalla, ninguna entidad con fondo acumulado puede completar el circuito de valoración que el backend ya expone (Increment 1).
  *Impact*: 2 rutas nuevas (`/admin/tvd` + tarjeta en `/admin`, gateada por `USUA_PERM_TRD` como pide el ADR — no por `USUA_PERM_ADMIN`), 1 componente compartido, 1 fábrica de proxies SSR (`lib/server/trdCircuitoProxy.server.js`) reutilizada por `/admin/trd` y `/admin/tvd`, más el gemelo `GET/POST /admin/trd/api/{code}/versiones` que faltaba en el frontend. 68 tests nuevos (proxies: guard 401, allowlist anti mass-assignment, validación de fechas extremas/campos obligatorios de TVD, `encodeURIComponent` de código/id, propagación de errores; tarjeta de administración por permiso); suite completa **1295/1295** (antes 1227), `svelte-check` 0 errores.

- `fix(archive)`: **remediación del dictamen de conformidad + seguridad sobre el circuito de convalidación de la TRD ([ADR-025](docs/es/adr/ADR-025-trd-instrumento-convalidado-versionado.md) addendum, migración 038)**
  Cinco hallazgos de conformidad sobre el Increment 2: **[Alta] #1** `aprobada` era un callejón sin salida (sin `DELETE`/`derogar`/`PATCH`, la única transición era `convalidar`) — nuevo `POST /trd/{code}/versiones/{version}/devolver` (`aprobada→borrador`, motivo obligatorio, asiento `archive.trd_version_devuelta`), y se corrige el mensaje de `409 trd_version_en_tramite` que instruía una acción inexistente (`derogar` desde `aprobada`). **[Alta] #2** el gate D5 (`app/core/trd_disposition.py`) tenía el criterio CONTRARIO al ADR — leía el estado VIVO de `trd_series` en vez del **snapshot congelado**; corregido a `assert_disposicion_final_autorizada(serie_estado, acto_administrativo)`: autoriza `convalidada` o `derogada` **con acto** — el criterio original habría bloqueado eliminaciones legítimas de expedientes cuyo instrumento fue convalidado y luego derogado, conservando indefinidamente documentación cuya disposición era eliminar. **[Alta por impacto] #3** `derogar` sin sucesora mataba el código de serie (`POST /versiones` daba 404 permanente) — decisión: `derogar` se bloquea (`409 trd_derogacion_bloqueada_expedientes_abiertos`) mientras existan expedientes `open` bajo el código (no se exige sucesora ya convalidada, para no impedir retirar series sin uso); `create_trd_serie_version` cae a la última versión por número cuando no hay vigente. **[Media] #5** `aprobar` ahora exige y escribe `{acta_comite, fecha_aprobacion_comite}` (columna `acta_comite` nueva); `convalidar` desde `aprobada` exige que ya consten (`409 trd_aprobacion_comite_requerida`). **[Media] #4** nuevo `POST /trd/{code}/versiones/{version}/registrar-rusd` para el RUSD/publicación que la norma permite registrar hasta 30 días hábiles después de convalidar. **[Baja]** citas normativas normalizadas a "Ac. AGN 001/2024, que compila el 004/2019"; estado del ADR pasa a "Implementado".
  Hallazgos de seguridad sobre el mismo código, cerrados en el mismo incremento: `GET /trd/revision-pendiente` **sin filtro de clearance** (`USUA_PERM_TRD` es ortogonal al clearance — cualquier portador enumeraba expedientes RESERVADA/CLASIFICADA; corregido con el mismo criterio no-read-up que `list_expedientes` + traza agregada de consulta RF-BUS-10); re-apunte de FK (`repoint_tipos_documentales`/`repoint_expediente_metadata_templates`) plantable con `500` en un flujo legítimo (colisión de `UNIQUE`) — ahora colisión-segura (`NOT EXISTS`, cuenta `omitidos`) con `UniqueViolationError` residual traducida a `409`; huérfanos silenciosos si una convalidación perdía la carrera de derogar la vigente previa — ahora aborta fail-closed; auditoría sin `estado_anterior` en las transiciones de derogación — añadido; el trigger de la 038 blinda a nivel de motor el acto de una versión `convalidada` (inmutable, `estado` solo puede ir a `derogada`; `rusd_radicado`/`fecha_publicacion` con *fill-once*).
  *Why*: `archival-compliance-auditor` re-auditó el Increment 2 recién liberado y encontró que resolvía la fuga retroactiva del ADR pero introducía un modo de fallo simétrico (bloquear eliminaciones legítimas) y dejaba el circuito de convalidación incompleto; un auditor de seguridad, en paralelo, encontró una regresión de un control ya cerrado en este mismo servicio (no-read-up) más cuatro hallazgos Media.
  *Impact*: 3 endpoints nuevos (`devolver`, `registrar-rusd`) + body ahora obligatorio en `aprobar`; migración `038_trd_convalidacion_devolucion_rusd.sql` (columna `acta_comite`, trigger extendido); sin cambios de contrato en los endpoints existentes salvo los nuevos `409`. 22 tests nuevos (unitarios + integración contra Postgres real); suite completa sin roturas — **701 passed / 151 skipped** (unitaria, antes 679/145) + **151 passed** (integración, antes 145).

- `feat(frontend)`: **MVP de PINAR — Plan Institucional de Archivos en `/admin/pinar` (Fase 7, cierre del desfase API↔UI, RF-ADM-08, Ac. AGN 003/2015)**
  PINAR tenía 30 endpoints en `tenant-service` (`pinar.py`) y ni una sola pantalla: era inservible desde la aplicación. Este MVP cubre el núcleo operable: `/admin/pinar` (listado de planes, filtro por estado, creación de una versión nueva siempre en `borrador`) y `/admin/pinar/[id]` (workspace con `Stepper` del ciclo de vida — `borrador→aprobado→en_ejecucion→cerrado` — y pestaña Tablero con avance por objetivo/por eje). 6 proxies SSR nuevos bajo `admin/pinar/api/` (D-05): `ejes/` (catálogo, sin consumidor en esta fase, preparado para la futura pantalla de priorización), `planes/` (GET filtrado + POST alta), y `planes/[id]/{aprobar,ejecutar,cerrar,tablero}/` — los tres primeros con `USUA_PERM_ADMIN` en el backend y actor imputable (`X-User-Id`, `_require_actor`); `aprobar` exige `acto_administrativo` no vacío.
  Honestidad de la interfaz, no negociable: **aprobar congela el contenido estructural del plan de forma permanente** (`_EDITABLE = {"borrador"}` en `pinar_service.py`, asiento en `audit_log` inmutable) — el `Modal` de confirmación lo advierte ANTES de ejecutar la acción, no después de un 409; lo mismo para "pasar a ejecución" (sin transición de retorno) y "cerrar" (estado terminal). El botón "Confirmar aprobación" queda deshabilitado sin `acto_administrativo`. El `Stepper` es puramente informativo (no navegable por clic): las transiciones solo ocurren por los botones de acción + confirmación, igual que el patrón ya usado en `expedientes/[id]`. El tablero distingue explícitamente "no se pudo cargar" (`tablero.error`, banner de alerta) de "vacío legítimo" (plan recién creado sin objetivos/proyectos, `EmptyState`) — nunca pinta una tabla en cero por un fallo de red.
  Fuera de alcance de este MVP (endpoints sin UI, quedan para una fase posterior de PINAR): aspectos críticos, priorización (aspecto × eje), objetivos, proyectos, seguimiento (append-only), articulación con otros instrumentos (CCD/TRD/FUID) y mapa de ruta — todos dependen de objetivos/proyectos, que a su vez no tienen pantalla propia todavía; el tablero de este MVP ya refleja que esos totales estarán en cero hasta entonces, sin ocultarlo.
  *Why*: cierre de la Fase 7 del desfase API↔UI — `tenant-service` ya exponía el ciclo de vida completo del PINAR (E14, Ac. AGN 003/2015) sin ningún consumidor en el frontend.
  *Impact*: 2 rutas nuevas (`/admin/pinar`, `/admin/pinar/[id]`) + 6 proxies SSR + tarjeta nueva en el índice de administración (`implemented: true`); 41 tests nuevos (proxies: guard 401, allowlist anti mass-assignment, `encodeURIComponent`+formato UUID, propagación de 409; páginas: gate por `USUA_PERM_ADMIN`, `Stepper` según estado, irreversibilidad advertida antes de confirmar, distinción error/vacío del tablero); suite completa 1227/1227 (antes 1186), `svelte-check` 0 errores.

- `feat(archive)`: **circuito de convalidación de la TRD — `aprobar`/`convalidar`/`derogar` + ratificación de series migradas ([ADR-025](docs/es/adr/ADR-025-trd-instrumento-convalidado-versionado.md) Increment 2)**
  Cierra el Increment 2 del ADR-025 (Increment 1 ya en `main`: versionado append-only de `trd_series`, congelación del amarre al cerrar un expediente). Máquina de estados del instrumento (Ac. AGN 004/2019), aislada en el service layer con validación explícita antes de tocar BD (`409 trd_transicion_invalida` fail-fast, el CHECK de Postgres queda como defensa en profundidad): `POST /api/v1/trd/{code}/versiones/{version}/aprobar` (`borrador→aprobada`); `POST /api/v1/trd/{code}/versiones/{version}/convalidar` (`{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}`) — desde `aprobada`, deroga en la MISMA transacción la versión previamente vigente del código y **re-apunta** (`UPDATE`, no clona) las FK huérfanas de `tipos_documentales`/`expediente_metadata_templates` a la nueva versión (cierra el aviso #3 del ADR); desde `migrada` (ratificación D6), la MISMA fila gana los campos del acto **sin crear versión nueva ni derogar nada**, y limpia `trd_revision_requerida`/`trd_revision_motivo` de los expedientes cerrados bajo ella cuyo motivo sea exactamente `cerrado_bajo_serie_migrada` (otros motivos, p. ej. `serie_no_resoluble`, quedan intactos); `POST /api/v1/trd/{code}/versiones/{version}/derogar` (`convalidada`/`migrada`→`derogada`). `GET /api/v1/trd/revision-pendiente` (paginado, `X-Total-Count`) — informe de expedientes marcados para revisión. Gate D5 para la eliminación efectiva (disposición final): `app/core/trd_disposition.py::assert_serie_convalidada_para_disposicion_final` exige `convalidada` **estricto** (sin excepción para `migrada`) — codificado ahora, deliberadamente **sin caller** porque ese job de eliminación todavía no existe (deuda declarada por el propio ADR, no oculta).
  *Why*: el Increment 1 cerró la fuga retroactiva (una versión nueva de la TRD ya no altera el calendario de expedientes cerrados) pero dejó todas las series preexistentes en `estado='migrada'` — "el sistema no sabe si fueron convalidadas" — sin ninguna vía para que una entidad declarara el acto administrativo real (Ac. AGN 004/2019) ni para promover una versión `borrador` a instrumento oponible.
  *Impact*: 4 endpoints nuevos en `archive-service` bajo `/api/v1/trd`, mismo permiso que el resto de mutaciones TRD (`USUA_PERM_TRD`, `crud≥3` en aprobar/convalidar/derogar, `crud≥1` en el informe); sin migración SQL (Increment 1 ya dejó todas las columnas); 31 tests unitarios + 5 de integración contra Postgres real nuevos (derogación+re-apuntado de FK, ratificación selectiva por motivo, transiciones ilegales, gate D5, paginación con tenant scoping) — 679/145 pasan (antes 648/140), sin roturas.

- `feat(frontend)`: **búsqueda semántica y de antecedentes en `/busqueda` (Fase 6, cierre del desfase API↔UI, E21)**
  `/busqueda` gana dos pestañas nuevas junto a la búsqueda exacta existente — **Semántica** (`POST /api/v1/knowledge/search`, E21 §Patrón de recuperación) y **Antecedentes** (`POST /api/v1/knowledge/antecedentes`, E21 Inc.2 Patrón A) — resueltas por `?tab=exacta|semantica|antecedentes` (deep-link/bookmarkable, mismo patrón que el conmutador de vista `?vista=` de `/bandeja`). Tres proxies SSR nuevos bajo `busqueda/api/`: `semantica/` y `antecedentes/` (D-05, allowlist campo a campo — `acl` y `source_type` nunca llegan desde el cliente al gateway) y `by-tracking/[trackingNumber]/` (contra `document-service`, `GET /documents/by-tracking/{tracking}` — resuelve el radicado pivote de Antecedentes en modo "por número" como paso de verificación antes de lanzar la búsqueda, con allowlist de SALIDA que no reenvía el documento completo). `+page.server.js` resuelve por SSR solo el backend de la pestaña activa; las búsquedas interactivas de Semántica/Antecedentes usan los proxies + `history.replaceState` (sin re-disparar el `load` SSR) para un estado de carga real sin recargar la pestaña.
  Honestidad de la interfaz, no negociable: los resultados de ambas pestañas se presentan como documentos **parecidos** (similitud vectorial 0-1, con una banda cualitativa — alta/media/baja — y una barra explicada, nunca como coincidencia textual exacta); un fallo de red muestra "No se pudo buscar" (con reintento), nunca se disfraza de "sin resultados"; el recorte por clearance de `knowledge-service` (ACL fail-closed) es completamente invisible — no se anuncia ningún conteo de resultados ocultos, para no convertir la búsqueda en un oráculo de existencia de documentos reservados.
  *Why*: `knowledge-service` (E21) ya exponía `/search`, `/antecedentes` y su advisory sin ningún consumidor de búsqueda directa en el frontend (el RAG generativo ya integrado en `/radicar`/`/borradores` es un caso de uso distinto — sugerencia al redactar, no exploración).
  *Impact*: 3 proxies SSR nuevos con 18 tests (guard 401, allowlist, `encodeURIComponent`); `+page.server.js`/`+page.svelte` de `/busqueda` extendidos con tablist ARIA (roles tab/tabpanel, navegación por flechas) y dos paneles de resultados nuevos (tarjetas, no tabla — cada resultado es "parecido", no una fila de coincidencia); suite completa 1186/1186 (antes 1168), `svelte-check` 0 errores.

- `feat(frontend)`: **ayuda generativa (RAG) integrada en redacción y clasificación de radicación/borradores (F6, cierre del desfase API↔UI, E21)**
  El RAG no se ofrece como un módulo aparte: se integra donde ya se redacta y clasifica. Botón "Sugerir con antecedentes" (`RagSuggestButton.svelte`) en `/radicar` (paso 1, cuerpo de Salida/Interno) y en el drawer de `/borradores`, que llama a `POST /knowledge/rag` (E21 Inc.3) vía proxies SSR nuevos (`/radicar/api/rag/suggest`, `/borradores/api/rag/suggest`, D-05). Chips de tipo documental sugerido (`TrdSuggestChips.svelte`) junto al selector de clasificación del paso 1, alimentados por `/radicar/api/rag/antecedentes` (E21 Inc.2 Patrón A) — ese proxy resuelve `doc_class` de cada precedente vía `GET /documents/by-tracking/{tracking}` porque `/antecedentes` no lo trae en su contrato real. Sección "Respuestas" nueva en el drawer de detalle de `/bandeja`, alimentada por `GET /documents/{id}/respuestas` a través de un proxy nuevo (`/bandeja/api/[id]/respuestas`).
  Tres reglas duras, no adorno: (1) el texto generado se inserta SIEMPRE marcado como generado y con sus citas (`$lib/utils/ragInsert.js::buildGeneratedHtml`, usando únicamente etiquetas de la allowlist de `sanitizeHtml.js` — blockquote/p/strong/em/ul/li — para que la marca sobreviva el saneo server-side); si la respuesta viene sin citas (`citations.length === 0`) o sin `answer`, NUNCA se ofrece insertar, con un mensaje honesto explicando que no se pudo fundamentar; (2) el chip de tipo documental sugerido nunca se autoaplica — solo se asigna al pulsarlo, un acto explícito; (3) un fallo del servicio de conocimiento (503/red) queda contenido en el panel del componente y nunca bloquea radicar/guardar/aprobar/radicar un borrador. La barrera de residencia del backend (`is_generatable`) se refleja con un aviso honesto y genérico (sin inventar cifras que el backend no da) de que la sugerencia puede excluir precedentes visibles en la búsqueda.
  *Why*: cierre de la Fase 6 del desfase API↔UI — el backend ya exponía RAG/antecedentes (E21) sin ningún consumidor en el frontend.
  *Impact*: 4 proxies SSR nuevos (`radicar/api/rag/{suggest,antecedentes}`, `borradores/api/rag/suggest`, `bandeja/api/[id]/respuestas`); 2 componentes nuevos en `lib/components/features/rag/`; `lib/utils/ragInsert.js` nuevo; tipos `RagResponse`/`RagCitation`/`Antecedente`/`RespuestaItem` añadidos a `lib/types/api.js`; 51 tests nuevos (proxies: guard 401, allowlist, `encodeURIComponent`, degradación 503; componentes: las tres reglas duras); suite completa 1186/1186 (antes 1135), `svelte-check` 0 errores.

- `fix(notification)`: **endurece el destino de los webhooks salientes — bloquea loopback/link-local/privado/servicios internos (hallazgo Media, auditoría de seguridad — no bloqueante)**
  `WebhookCreate.url` era `HttpUrl` de Pydantic: validaba la forma de la URL, nunca el destino. `webhook_service._deliver_one` hacía `POST` desde dentro de la red Docker sin lista de destinos denegados. El auditor calificó el hallazgo como **Media, no Alta** (es POST con cuerpo fijo sin cabeceras controlables — IMDSv1 responde 405 a POST, así que `169.254.169.254` no es explotable por esta vía; `httpx.AsyncClient` no sigue redirects; es completamente ciego, la respuesta nunca vuelve al atacante y no existe un endpoint de "probar webhook"; el router ya exigía `USUA_PERM_ADMIN` y firma con HMAC propio). Lo que sí quedaba abierto: dirigir 3 reintentos × evento contra un servicio interno arbitrario — amplificación/ruido contra la propia red. Cerrado con `app/core/webhook_security.py::validate_webhook_target`, invocado en **dos momentos con distinta profundidad**: (1) al crear la suscripción (`WebhookCreate`, validación Pydantic sin resolución DNS — esquema/host literal/IP literal, produce `422`); (2) en **cada intento de entrega** (`_deliver_one`, resolución DNS real vía `asyncio.to_thread`, ya que la entrega hace resolución de todas formas) — esto cierra el caso de DNS rebinding (un dominio que resolvía a IP pública al suscribirse y luego se re-apunta a una IP interna) y también cubre suscripciones preexistentes al endurecimiento. Bloquea loopback (`127.0.0.0/8`, `::1`, `localhost`), link-local (`169.254.0.0/16`, `fe80::/10`), privado (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, ULA IPv6 `fc00::/7`), los nombres de servicio de la red Docker interna, y esquemas distintos de `http`/`https`. Kill-switch de desarrollo invertido respecto al resto del proyecto: `Settings.webhook_allow_private_targets` es `False` por defecto (estricto), y solo se pone `True` en local para apuntar a un receptor en la máquina del desarrollador o a MailHog. El mensaje de rechazo identifica la causa concreta (no un "URL inválida" genérico) porque quien configura la integración es un administrador legítimo. Suscripciones preexistentes que ahora violarían la política **no se borran ni se desactivan**: la entrega simplemente se omite y se registra en log en cada intento, dejando la decisión al administrador. Limitación aceptada y documentada (no cerrada): ventana TOCTOU entre la revalidación y la conexión real de `httpx`, milisegundos después — cerrarla del todo exigiría IP-pinning a nivel de socket, fuera del alcance de este hallazgo no bloqueante.
  *Why*: auditoría de seguridad de notification-service — un canal de entrega dirigido por el administrador no restringía el destino a lo verdaderamente externo.
  *Impact*: `POST /api/v1/webhooks` gana un `422` nuevo para destinos internos/no enrutables; nuevo `app/core/webhook_security.py` (compartido por schema y servicio de entrega); nuevo flag `webhook_allow_private_targets` en `app/core/config.py`; 18 tests pytest nuevos en `tests/test_webhooks.py` (155 passed en el servicio); sin migración.

- `fix(auth)`: **cierre de hallazgo Alto — auto-escalada de un `USUA_PERM_ADMIN` a `USUA_PERM_ROOT` (y a clearance superior al propio); ROOT ahora se aprovisiona fuera de banda ([ADR-024](docs/es/adr/ADR-024-root-fuera-de-banda.md))**
  `USUA_PERM_ROOT` (bypass total de RBAC) era una fila normal de `auth_permissions`, y `GET /auth/permissions` la devolvía sin filtrar. Nada impedía que un administrador de entidad se auto-concediera ROOT: `POST /auth/users` con `is_root: true`, o `PUT /auth/groups/{gid}/permissions/{pid}` concediendo `USUA_PERM_ROOT` al grupo del que él mismo es miembro. Un tercer eje: `PUT /auth/groups/{gid}/clearance` no comprobaba que el `max_level` pedido no excediera el clearance efectivo del actor (no-write-up, espejo del que ya rige la clasificación de radicados/expedientes). Las tres vías ahora responden `403` a un llamante no-ROOT (`RBACService.create_user`/`set_group_permission` en `app/services/rbac_service.py`; `set_group_clearance` en `app/routers/clearance.py`); `GET /auth/permissions` omite `USUA_PERM_ROOT` del catálogo para no-ROOT. Cerrar esas vías sin más dejaría un punto muerto de arranque — verificado que no existía ninguna forma de crear el primer ROOT de un tenant fuera de la API (`upsert_user_by_keycloak_sub` nunca voltea `is_root`) — así que se añadió `app/ops/grant_root.py`, script de operación (no HTTP, no expuesto por el gateway) que concede ROOT a un usuario existente de un tenant explícito, idempotente y auditado (`rbac.root_otorgado_fuera_de_banda`), siguiendo el patrón ya establecido por `purge_revoked_signing_keys.py`/`rewrap_signing_keys.py`.
  *Why*: auditoría de seguridad dedicada a auth-service — el rol administrativo más alto operable desde la API era, de facto, indistinguible de ROOT.
  *Impact*: `POST /auth/users` (con `is_root: true`) y `PUT /auth/groups/{gid}/permissions/{pid}` (concediendo `USUA_PERM_ROOT`) ganan un `403` nuevo para llamantes no-ROOT; `PUT /auth/groups/{gid}/clearance` gana un `403` no-write-up; `GET /auth/permissions` cambia de forma para un llamante no-ROOT (ya no lista `USUA_PERM_ROOT`); nuevo script `app/ops/grant_root.py` (uso: `python -m app.ops.grant_root --tenant <slug> --user <username>`); 10 tests pytest nuevos en auth-service (198 passed, 15 skipped); sin migración; frontend pendiente de ocultar la casilla "Otorgar ROOT" para un caller no-ROOT.

- `fix(tenant,gateway)`: **`app/core/authz.py` + Canal A en tenant-service; `/api/v1/tenants/` sacado del gateway (cierra 2 hallazgos Críticos de auditoría de seguridad)**
  **Hallazgo 1 — el servicio no tenía NINGÚN gate de autorización.** `grep -rn "require_permission" services/tenant-service/app/` devolvía 0 líneas — no existía `app/core/authz.py` y `app/main.py` montaba los 6 routers sin `dependencies=`. 13 escrituras quedaban abiertas a cualquier autenticado del tenant: `catalogos.py` (POST/PATCH/DELETE de items de catálogo), `config.py` (crear/borrar/sembrar festivos, `PUT /config/params/{key}`) y `dependencias.py` (POST/PATCH/DELETE). Un radicador sin ningún permiso administrativo podía borrar los festivos del año uno a uno y desde ahí `GET /business-days/calculate` devolvía vencimientos anticipados — **todos los plazos legales de respuesta de la entidad mal calculados**. Un comentario en `config.py` afirmaba "hoy el api-gateway media el acceso" — **falso**: el gateway (`api-gateway/app/middleware/auth.py`) solo valida el token e inyecta cabeceras de identidad, nunca consulta permisos; ese comentario era la semilla del hueco y se borró (junto con su gemelo en `dependencias.py` y `pinar.py`). Cerrado portando el patrón `require_permission` de document-service/archive-service a `app/core/authz.py` (resuelve `auth_users`/`auth_memberships`/`auth_group_permissions` en el schema del tenant vía `get_tenant_conn`, fail-closed). Gate `USUA_PERM_ADMIN` en las 13 escrituras + las 18 de PINAR (`pinar.py`, mismo criterio — la spec §10.3 restringe la gestión al administrador y no tenía ningún gate real pese a documentarlo como pendiente). Los `GET` quedan **solo autenticados** (catálogos de referencia y calendario que la UI de radicación y el cálculo de plazos necesitan para cualquier usuario, no solo administradores).
  **Hallazgo 2 — fuga ENTRE entidades en el registro de tenants.** `routers/tenants.py` usa `get_db_conn` (schema `public`, sin acotar a tenant) y no tenía ningún gate, y `/api/v1/tenants/` estaba enrutado por el gateway — cualquier usuario de la entidad A podía `GET /api/v1/tenants` (enumerar todas las instituciones del despliegue) y **`PATCH /api/v1/tenants/{id}` escribir sobre el registro de la entidad B**: la única fuga *entre* entidades del sistema. Corrección quirúrgica: se sacó `/api/v1/tenants/` de la tabla de ruteo del gateway (`api-gateway/app/routers/proxy.py`) — verificado que no hay consumidor legítimo externo (el frontend no lo llama; `scripts/init_tenant.py` escribe directo a `public.tenants`, es provisión de plataforma, no de aplicación). Una petición a `/api/v1/tenants/` con token válido ahora responde `404 route_not_found` sin llegar a proxyar nada.
  **Hallazgo 3 (Media) — escrituras sin traza.** `tenant-service` solo auditaba en `pinar_service.py`; catálogos, dependencias, festivos y parámetros mutaban sin dejar asiento en `public.audit_log` (`config.py::_actor` solo alimentaba `updated_by`, no auditoría inmutable). Cerrado con `app/core/audit.py` (mismo patrón fail-closed que `RBACService`: `require_audit_available()` + `audit_tenant()` + `audit.append` dentro de la MISMA transacción que la mutación — si la auditoría falla, la mutación se revierte).
  *Why*: una auditoría de seguridad dedicada a tenant-service encontró que el servicio, pese a gestionar el organigrama, los catálogos de referencia y los plazos legales de respuesta de la entidad, no tenía ningún control de acceso — y que el único endpoint sin scoping de tenant del sistema estaba expuesto al exterior.
  *Impact*: `app/core/authz.py` y `app/core/audit.py` nuevos; 13+18 escrituras gateadas y auditadas; `/api/v1/tenants/` fuera de `api-gateway/app/routers/proxy.py` (contrato del gateway cambia: ese prefijo ya no es alcanzable externamente); 40 tests pytest nuevos/actualizados en tenant-service (403 sin permiso, 403 sin identidad, éxito con permiso, asiento de auditoría esperado por escritura) + 2 tests nuevos en api-gateway (`/api/v1/tenants/` no se enruta ni con token válido); sin migración (las tablas `auth_*` ya las provisiona auth-service en el schema del tenant).

- `fix(document)`: **`USUA_PERM_TRD` + auditoría en el catálogo de metadatos (E03, cierra hallazgo Alto de auditoría)**
  `POST /metadata/templates` y las tres escrituras de `POST/PATCH/DELETE /metadata/elements` no tenían **ningún** `require_permission` — cualquier usuario autenticado del tenant podía crear una plantilla de metadatos con un JSON Schema laxo para un tipo documental existente y, como los radicados se validan contra la plantilla **activa**, anular la validación de metadatos obligatorios de ese tipo para toda la entidad. El gemelo exacto en archive-service (`POST /expediente-metadata/templates`) sí exigía `USUA_PERM_TRD`; la omisión ya estaba documentada como hallazgo de dominio en el changelog de `/admin/metadatos` (entrada `feat(frontend)` de Fase 5). Cerrado con el **mismo permiso** (`USUA_PERM_TRD`, no uno nuevo — un permiso distinto para la misma capacidad bifurca el modelo). `DELETE /metadata/elements/{id}` exige además `min_crud=3` (precedente `archive-service/app/routers/tipos_documentales.py::delete_tipo`): retirar un elemento del catálogo es más destructivo que crearlo/editarlo.
  **Media adicional**: ninguna de las cuatro escrituras dejaba asiento en `public.audit_log`. Ahora auditan `document.metadata_template_creada`/`metadata_element_creado`/`_actualizado`/`_eliminado`, **dentro de la misma transacción** que la mutación (patrón `AnulacionService`/`RBACService`: si `audit.append` falla, la mutación revierte; `tenant_slug` obligatorio y fail-closed).
  *Why*: es una omisión, no una decisión de diseño — dos rutas gemelas sobre objetos distintos, solo una con gate.
  *Impact*: `MetadataTemplateService.create` y el nuevo `MetadataElementService` (crea/actualiza/borra, auditado) en `app/services/metadata_service.py`; los routers pasan `tenant_slug`/`actor` desde los headers; **las lecturas (`GET`) quedan intactas, sin gate nuevo**; 10 tests pytest nuevos (403 sin permiso / éxito con él en las 4 escrituras, `min_crud=3` del DELETE, asiento de auditoría por escritura, no-audita si no muta); sin migración (`audit_log` ya existe); contrato HTTP cambia (endpoints que antes no exigían permiso ahora sí).

- `feat(auth)`: **Auditoría inmutable de las escrituras RBAC/URD/clearance (E08, cierra hueco de trazabilidad)**
  `rbac_service.py`, `urd_service.py` y el nuevo `clearance_service.py` dejaban 8 tipos de escritura administrativa sin ningún asiento en `public.audit_log` — crear usuario, crear grupo, alta/baja de miembro de grupo, asignar permiso a grupo, crear/eliminar URD y **subir/bajar el clearance de un grupo**: exactamente las vías por las que se escala un privilegio en este sistema, invisibles hasta ahora para un auditor. Cada escritura ahora llama a `orpycamcp_common.audit.append` **dentro de la misma transacción** que la mutación (`async with conn.transaction()`, mismo patrón que `TotpService`/`SigningKeyService`) y **no es best-effort**: `_require_audit_available()` rehúsa la operación con 500 si la librería de auditoría no está instalada (antes de tocar ninguna fila), y si `audit.append` falla dentro de la transacción, la excepción propaga y la mutación no queda confirmada. `tenant_slug` es obligatorio y fail-closed (`_audit_tenant`, mismo patrón que `AnulacionService` en document-service): un llamante que lo omitiera archivaría la escalada bajo el tenant del sistema, fuera de la vista de auditoría de la entidad — se prefiere que la escritura falle. El payload de `clearance.grupo_actualizado` incluye `previous_max_level` y `new_max_level` (sin el nivel previo no se distingue una subida de una bajada); ningún payload incluye secretos.
  Acciones nuevas: `rbac.usuario_creado`, `rbac.grupo_creado`, `rbac.miembro_agregado`, `rbac.miembro_removido`, `rbac.permiso_grupo_asignado`, `urd.creada`, `urd.eliminada`, `clearance.grupo_actualizado`.
  *Why*: el panel `/admin` convirtió estas vías (antes solo alcanzables por llamada directa a la API) en un clic — el hueco de trazabilidad pasó de teórico a explotable por error humano cotidiano.
  *Impact*: `RBACService`/`URDService` ahora se construyen con `conn` (no `repo`) — actualizados los 3 call sites (`routers/auth.py`, `routers/rbac.py`, `core/authz.py`); nuevo `app/services/clearance_service.py`; 20 tests pytest nuevos (action esperado por escritura, sin asiento si la mutación falla, mutación no confirmada si la auditoría falla, 500 fail-closed sin librería/sin `tenant_slug`); sin migración (`audit_log` ya existe); sin cambio de contrato HTTP.

- `feat(frontend)`: **Administración de TRD/CCD, metadatos, catálogos y parámetros (E03/E04/E14, Fase 5 cierre de desfase API↔UI, segunda tanda)**
  Cuatro pantallas nuevas del panel `/admin`, todas gateadas hoy por `USUA_PERM_ADMIN` del lado del cliente (visibilidad del panel), con el gate REAL espejado donde el backend lo declara. `/admin/trd`: dos pestañas sobre `CatalogCrudPanel` — series TRD/CCD en **árbol indentado** por `parent_id` (nunca lista plana; código inmutable, `PATCH` gateado por `USUA_PERM_TRD`) y tipos documentales con CRUD completo y filtro por serie. `/admin/metadatos`: reparte con `/admin/trd` (TRD = clasificación, metadatos = campos de esos esquemas) en tres pestañas — plantillas de expediente (archive-service, `USUA_PERM_TRD`) y de documento (document-service) de **solo alta** (el backend no tiene `PATCH`/`DELETE`: nueva versión = nuevo registro), con el `json_schema` editado como JSON crudo validado en el cliente antes de enviar (`SchemaField.svelte`/`schemaField.js` **rellenan** un formulario a partir de un schema existente, no lo autoran — no aplican aquí); y elementos de metadato reutilizables con CRUD completo. `/admin/catalogos`: maestro-detalle (catálogos a la izquierda, items a la derecha) que degrada a dos niveles navegables con botón «Volver» en móvil. `/admin/parametros`: parámetros del tenant (JSON libre por clave), calendario de festivos (precarga Ley 51/1983 vía el algoritmo de Pascua/Emiliani ya existente en el backend, sin llamada externa) y una calculadora de días hábiles que **reconstruye el desglose** de qué días se descontaron (fin de semana/festivo con descripción) porque `BusinessDaysResult` del backend solo devuelve la fecha resultante — el desglose se deriva en el cliente del mismo catálogo de festivos ya cargado, nunca inventado.
  **HALLAZGO DE DOMINIO reportado (no corregido en el front):** a diferencia de `archive-service/app/routers/trd.py`/`tipos_documentales.py`/`metadata.py` (gate real `USUA_PERM_TRD`), los routers `document-service/app/routers/metadata.py`/`metadata_elements.py` y `tenant-service/app/routers/catalogos.py`/`config.py` **no declaran ningún `require_permission`** — cualquier autenticado del tenant puede escribir hoy; `config.py` lo documenta explícitamente en su propio docstring como pendiente. Las cuatro pantallas gatean sus botones de escritura del lado del cliente como medida conservadora, sin fingir una autorización que el backend no aplica.
  *Why*: cerraba cuatro áreas de administración listadas como "Próximamente" en el dashboard `/admin` desde la Fase 0 — sin ellas, la clasificación documental (TRD/CCD), los esquemas de metadatos, los catálogos de referencia y los parámetros de plazo (RF-RAD-04) solo eran operables vía API directa.
  *Impact*: 13 proxies SSR nuevos (D-05) bajo `/admin/trd/api/*`, `/admin/metadatos/api/*`, `/admin/catalogos/api/*` y `/admin/parametros/api/*`; sin cambios de backend ni de esquema; 55 tests nuevos de proxy (401, allowlist campo a campo anti mass-assignment, validación de JSON Schema/fecha/rango de años, UUID + `encodeURIComponent`, whitelist de nombre de catálogo); `svelte-check` 0 errores/0 warnings; 1125 tests vitest verdes (91 archivos).

- `feat(auth)`: **Lectura de membresías, permisos y clearance de grupo (E08, cierra el gap RBAC read)**
  Tres `GET` nuevos, cada uno espejo exacto de la escritura ciega equivalente: `GET /api/v1/auth/groups/{group_id}/members` (paginado, `X-Total-Count`, junta `auth_memberships`+`auth_users`), `GET /api/v1/auth/groups/{group_id}/permissions` (`[{permission_id, nombre, descripcion, crud}]`, junta `auth_group_permissions`+`auth_permissions`) y `GET /api/v1/auth/groups/{group_id}/clearance` (`{group_id, max_level}`, `max_level: null` si el grupo no tiene fila en `role_clearance` — implícito PUBLICA=1; ruta hermana exacta del `PUT` existente en `clearance.py`, no una ruta nueva). Mismo gate `USUA_PERM_ADMIN` que sus escrituras hermanas (heredado a nivel de router en `rbac.py`; declarado explícito en `clearance.py`). `group_id` inexistente **o de otro tenant** → `404` indistinguible (el `search_path` ya acota el schema; nunca `403`, que confirmaría la existencia del grupo ajeno). Sin migración: solo lectura de tablas ya existentes (`auth_groups`, `auth_memberships`, `auth_group_permissions`, `role_clearance`).
  *Why*: el panel `/admin/grupos` del frontend podía dar de alta un miembro/permiso/clearance pero no mostrar el estado actual del grupo — un admin no puede auditar ni corregir lo que no puede ver, y una baja se hacía a ciegas (gap advertido explícitamente en la UI desde su implementación, ver entrada `feat(frontend)` anterior).
  *Impact*: 3 endpoints nuevos, 4 métodos de repositorio nuevos (`RBACRepository.get_group/list_group_members/list_group_permissions`, `ClearanceRepository.group_exists/get_group_clearance`), 2 schemas Pydantic nuevos (`GroupPermissionItem`, `GroupClearance`); 14 tests pytest nuevos (200, 404 grupo inexistente, 404 grupo de otro tenant, 403 sin `USUA_PERM_ADMIN`); sin cambios de esquema.

- `feat(frontend)`: **Administración de grupos y dependencias (E08/E14, Fase 5 cierre de desfase API↔UI)**
  Nueva pantalla `/admin/grupos`: listado con `CatalogCrudPanel` (crear grupo; **no** hay edición ni borrado — `auth-service/app/routers/rbac.py` solo tiene alta+lectura de grupos, la UI no ofrece nada que el backend rechazaría) y un drawer de detalle para miembros (buscador + alta/baja, `POST/DELETE /auth/groups/{id}/members/{user_id}`), permisos por grupo (`PUT .../permissions/{permission_id}`, crud 0-5) y clearance (`PUT .../clearance`, reutiliza `nivelSeguridad.js` para la etiqueta/color del chip). **GAP DE BACKEND verificado y advertido en la propia UI**: `rbac.py`/`clearance.py` no exponen NINGÚN endpoint de lectura de membresías, permisos ni clearance ya asignados a un grupo — solo altas/bajas ciegas; el drawer lo declara explícitamente en vez de simular un "estado actual" que no existe. Nueva pantalla `/admin/dependencias`: vista árbol del organigrama (`GET /dependencias/tree`) con un componente nuevo `DependenciaTreeNode.svelte` (`<details>/<summary>` nativos — no existía ningún componente de árbol reutilizable en el proyecto), crear/editar/activar/desactivar. Renombrar o desactivar dispara un **modal de confirmación de riesgo explícito** (nunca un tooltip): las dependencias se correlacionan por **nombre**, no por id/código, en `workflow_rules.assign_to_dept` (texto libre, sin FK) y en los `flow_steps` en vuelo, así que un radicado asignado hoy puede quedar sin bandeja visible. Borrado duro (existe en el backend, bloqueado si hay hijos) queda fuera del alcance pedido.
  *Why*: cerraba dos áreas de administración listadas como "Próximamente" en el dashboard `/admin` desde la Fase 0, con impacto directo en RBAC/clearance (escalada de privilegios) y en la integridad del enrutamiento (dependencias mal renombradas/desactivadas).
  *Impact*: 12 proxies SSR nuevos (D-05) bajo `/admin/grupos/api/*` y `/admin/dependencias/api/*`, 1 componente nuevo (`DependenciaTreeNode.svelte`), sin cambios de backend ni de esquema; 57 tests nuevos de proxy (401, allowlist campo a campo, UUID + `encodeURIComponent`, edición parcial real); `svelte-check` 0 errores.

- `feat(frontend)`: **Índice de información clasificada y reservada (E08, Ley 1712 art. 20) + Disposición final del radicado/expediente (RF-MET-08/E04, Fase 4 cierre de desfase API↔UI)**
  Nueva pestaña "Índice de clasificados" en `/reportes` (gate real `PERM_RECLASIFICAR`, `GET /reports/indice-reservado[.csv]`): registro content-free por diseño con banner legal, sin ninguna columna de asunto/contenido, y descarga CSV completa (backend ya neutraliza inyección de fórmulas) diferenciada de la exportación local de solo lo visible. Nuevo Drawer "Disposición final" accesible desde `/bandeja` (edición vía `GET/PATCH /documents/{id}/disposition`, con confirmación explícita para la acción `eliminar`/marcado de eliminación) y desde `/expedientes/[id]` (solo lectura: muestra la `RetentionSchedule` materializada al cierre, o una vista previa de la regla de la serie TRD asignada si el expediente sigue abierto — nunca una fecha inventada). Se verificó contra el backend que un radicado individual NO tiene vínculo directo a una serie TRD (`doc_class` es texto libre sin FK, y no existe un endpoint inverso "expedientes que contienen este radicado"); el Drawer del radicado lo declara explícitamente en vez de simular un vínculo inexistente.
  *Why*: cerraba dos obligaciones sin UI operable — el instrumento de transparencia del art. 20 y la decisión (irreversible en el caso de eliminación) sobre disposición final, que hasta ahora solo existían como endpoints de backend.
  *Impact*: 2 proxies SSR nuevos (`reportes/api/indice-reservado[.csv]`, `bandeja/api/[id]/disposition`), 1 faceta SSR no crítica nueva (`trdSerieDetail` en `expedientes/[id]/+page.server.js`), 2 componentes nuevos en `lib/components/features/disposicion/`; sin cambios de backend ni de esquema; 16 tests nuevos de proxy (401, allowlist, escapado de ids, opacidad del 404 no-read-up, pass-through del CSV).

- `feat(frontend)`: **Detalle de transferencia con acta-certificado y firma personal por rol + administración de preservación digital (E12/E17/E10, Fase 4 cierre de desfase API↔UI)**
  Nueva subruta `frontend/src/routes/(app)/transferencias/[id]/` con el acta de entrega presentada en apariencia de certificado (qué se transfiere, entre qué dependencias, cuándo, y el estado de firma de sus TRES roles — elaborador, remitente, receptor). El rol de CADA persona se deriva client-side (espejo del backend, `enviada_por`/`decidida_por`/`creada_por`) para mostrarle solo lo que a ella le corresponde firmar (nunca un botón "Firmar" genérico); el backend siempre re-valida la derivación real. Incluye verificación on-demand del sello institucional XAdES y de las firmas personales, y reintento manual del sellado. Nueva pantalla `frontend/src/routes/(app)/admin/preservacion/` (plan de preservación versionado + historial, índices pendientes de sellado XAdES con reintento manual, y visibilidad de artefactos WORM pendientes de renovación de retención) — el empaquetado manual del AIP y la protección WORM manual de un artefacto se omitieron deliberadamente (requieren datos, `documentos[]`/`file_id`, que esta pantalla no puede reconstruir sin inventar información; la renovación de retención es, por diseño de backend, disparo de sistema únicamente sin ruta pública equivalente).
  *Why*: cerraba un desfase entre lo que el backend ya exponía (E12/E17 firma personal por rol, E10 visibilidad operacional de reconciliación) y lo que la UI permitía operar.
  *Impact*: solo lectura/escritura vía proxies SSR nuevos (D-05); sin cambios de backend ni de esquema; 26 tests nuevos de proxy (guard 401, allowlist, escapado de ids).

- `fix(workflow)`: **`workflow_rules.assign_to_dept` validado al configurar la regla + traza en `audit_log` de la degradación silenciosa**
  `POST /workflow/rules` y `PATCH /workflow/rules/{id}` no comprobaban `assign_to_dept` (texto libre, sin FK) contra el catálogo `dependencias` — una regla mal tecleada se aceptaba en silencio y solo se descubría en ejecución, radicado a radicado, vía un `logger.warning` que nadie lee. `WorkflowRulesService._validate_assign_to_dept` reutiliza `WorkflowRepository.dependencia_activa` (mismo helper que ya cierra `assign`/`transfer`) para rechazar con 422 `dependencia_desconocida` ANTES de escribir la fila; el PATCH parcial solo valida si el cliente tocó el campo. Además, `auto_distribute` (para reglas ya existentes con una dependencia luego dada de baja) deja un asiento best-effort en `public.audit_log` (`workflow.regla_dependencia_invalida`) en vez de degradar solo por log.
  *Why*: cierra el residual declarado en el CLAUDE.md del servicio — la desviación de una regla mal configurada debe detectarse cuando un administrador puede corregirla, y ser auditable sin depender de que alguien mire los logs del worker.
  *Impact*: aditivo, sin migración; no cambia el comportamiento de degradación de `auto_distribute` (el radicado sigue sin autodistribuirse, sin reintentos de DLQ contra un dato permanentemente inválido).

- `fix(document)`: **Auditoría del ciclo de anulación de radicados (ADR-008/010)**
  `AnulacionService` no dejaba ningún asiento en `public.audit_log`: solicitar, aprobar y rechazar la anulación de un radicado (el acto más consecuente de un SGDEA — retira de la circulación un documento oficial con número inmutable) era invisible para la traza legal forense. Ahora los tres actos (`document.anulacion_solicitada`, `document.anulacion_aprobada`, `document.anulacion_rechazada`) auditan dentro de la misma transacción que la mutación — si `audit.append` falla, la solicitud/decisión entera revierte — con `object_ref` resuelto server-side (`tracking_number`), nunca del body.
  *Why*: anular un radicado incumplía Ley 594/2000 y el ADR-010 sin dejar rastro atribuible; una anulación aprobada sin traza es peor que una anulación fallida.
  *Impact*: aditivo, sin migración de esquema (reutiliza `public.audit_log`); no toca autorización ni clearance existentes.

- `fix(workflow)`: **Auditoría forense de devolución, transacción de trámite y reasignación en cascada (ADR-008/010)**
  `devolver`, `execute_transaction` y `reassign_cascade` mutaban el trámite sin dejar asiento en `public.audit_log` (Canal A) — deuda declarada en el propio servicio. Ahora los tres auditan (`workflow.devolver`, `workflow.transaccion`, `workflow.reasignacion_cascada`) dentro de la misma transacción que la mutación, con `object_ref` resuelto server-side. El asiento del barrido en cascada registra el conteo REAL de pasos movidos, y no fabrica un número de radicado que no existe.
  *Why*: eran las tres últimas mutaciones del servicio sin traza legal atribuible (Ley 594/2000); una reasignación masiva sin constancia de su alcance es forensemente inútil.
  *Impact*: aditivo, sin migración ni cambios de autorización, clearance o no-oráculo.

- `feat(workflow)`: **Motivo obligatorio al revertir una asignación (RF-FLU-08)**
  Nuevo campo `motivo` obligatorio (5-500 caracteres) en `POST /workflows/{radicado_id}/rollback`; el frontend ya lo exigía pero el backend lo descartaba sin dejar rastro.
  *Por qué*: revertir una asignación es un acto sobre el ciclo de vida del radicado y debe quedar trazado legalmente, no solo operativamente.
  *Impacto*: el motivo queda registrado en la hoja de ruta y en el `audit_log` legal; sin migración de esquema.

- `feat(frontend)`: **Sistema de Diseño OrpycaMCP v1.0 — repintado de la fuente única de tokens (E22, ADR-020)**
  `_tokens.scss` pasa de la paleta provisional (`#459940`, que no cumplía AA) a la definitiva v1.0, separando el uso del verde de marca para texto (`--op-primary-dark`, 6.56:1) del uso no textual (`--op-primary`), además de nueva escala tipográfica, radios, sombras y tipografías self-hosted.
  *Por qué*: el contraste WCAG de los pares texto/fondo nunca se había calculado; el verde de marca medía 4.28:1, por debajo del mínimo AA.
  *Impacto*: cero rotura visual (se conservan todos los nombres de tokens `--op-*`/`$op-*`).

- `fix(frontend)`: **Font Awesome nunca se cargaba y el favicon daba 404 (E22)**
  El CSS de Font Awesome estaba en `package.json` pero nunca se importaba (iconos vacíos en toda la app); `app.html` referenciaba un favicon inexistente. Ambos se corrigen y se añaden los activos de marca reales.

- `feat(frontend)`: **Landing pública en `/` (E22)**
  Nueva ruta pública con 7 secciones (hero, marco normativo, comparativa Orfeo→ORPYCA, licencia AGPL) para un visitante sin sesión; redirige 302 a `/dashboard` si ya hay sesión activa.

- `feat(frontend)`: **Login, callback y `/403` rediseñados con la marca real (E22)**
  `/login` estrena composición split con degradado de marca (antes mostraba un placeholder "O"); `/login/callback` y `/403` adoptan el mismo lenguaje visual, y `/403` pasa a un mensaje no culpabilizador.

- `feat(frontend)`: **Búsqueda global en el topbar y favoritos configurables en el sidebar (E22)**
  Nuevo formulario `role="search"` en el topbar (atajo `/`, accesible) y una sección Favoritos en `SidebarNav` persistida en `localStorage` por tenant+usuario, que nunca filtra por su cuenta (exige recibir la lista ya filtrada por permisos).

- `feat(frontend)`: **Dashboard rediseñado a los cuatro módulos personales (E22)**
  `/dashboard` muestra ahora el trabajo personal del usuario (pendientes, actividad, alertas, favoritos) sin scroll; las métricas del tenant pasan a una sección colapsable gateada por `SGD_PERM_ESTADISTICA` tanto en UI como en el `+page.server.js`.
  *Impacto*: `NAV_ITEMS` se extrae a un módulo compartido para no duplicar las reglas de RBAC.

- `feat(frontend)`: **`DataTable` gana las ocho capacidades obligatorias del sistema de diseño (E22)**
  Filtros por columna, ordenamiento, exportación CSV con neutralización de inyección de fórmulas, columnas configurables, vistas guardadas, selección múltiple, virtualización de render y edición rápida en celda — todas opt-in por prop.
  *Impacto*: `/busqueda` y `/reportes` migran a `DataTable` preservando su presentación.

- `docs(es)`: **Actualización integral de arquitectura, pantallas y funcionalidades**
  `docs/es/` no seguía el ritmo del código (4 microservicios ausentes del diagrama, puertos internos en vez de los publicados, ADRs 020-023 sin indexar). Se reescriben `architecture.md`, se añade `pantallas.md`, y se sincroniza `docs/en/` en un segundo pase.

- `feat(archive)`: **Descripción archivística EAD 2002 / ISAD(G) multinivel de expedientes en OAI-PMH (E11, RF-INT-06)**
  Nuevo endpoint público en archive-service que disemina cada expediente público como fragmento EAD 2002 multinivel (fondo→serie→expediente), cerrando la deuda del item-level de document-service.
  *Impacto*: la descripción para deliberadamente en `level="file"` — nunca enumera los `tracking_number` de radicados hijos, solo un conteo agregado, para no filtrar reservados dentro de un expediente público.

- `feat(document)`: **Descripción archivística EAD 2002 / ISAD(G) en OAI-PMH (E11, RF-INT-06)**
  Segundo `metadataPrefix=ead` en el endpoint OAI existente (junto a `oai_dc`), con los 6 elementos obligatorios ISAD(G)/NTC 4095 por radicado público.
  *Impacto*: reutiliza el mismo chokepoint `_PUBLIC` que Dublin Core — el formato es ortogonal al recorte de seguridad, sin migración.

- `feat(document)`: **Importación interoperable de radicados (E11, RF-INT-05)**
  `POST /api/v1/import` reingiere el paquete ZIP producido por la exportación (INT-01), verificando el SHA-256 de cada binario antes de tocar la BD (todo-o-nada) y preservando el número de radicado original.
  *Por qué*: cierra el hallazgo Crítico de E11 — el import previo no verificaba fixity ni preservaba el radicado.
  *Impacto*: una colisión de número omite en vez de sobrescribir (identidad inmutable).

- `feat(document)`: **Exportación interoperable de radicados (E11, RF-INT-01)**
  `POST /api/v1/export` produce un paquete ZIP con JSON Schema publicado, checksums SHA-256 por miembro y el número de radicado preservado como identidad portable.
  *Por qué*: base de intercambio con otros SGD (Marco de Interoperabilidad de Gobierno Digital); cimiento del import (INT-05).

- `feat(document)`: **Perfil CMIS 1.1 mínimo de lectura (E11, RF-INT-02)**
  `GET /api/v1/public/cmis/{tenant}` expone radicados públicos como `cmis:document`/`cmis:folder` (Browser Binding JSON), sirviendo los bytes del anexo solo si el radicado es público.
  *Impacto*: perfil de solo-lectura; mismo chokepoint `_PUBLIC` que OAI-PMH.

- `feat(document)`: **OAI-PMH — cosecha de metadatos (E11, RF-INT-02)**
  `GET /api/v1/public/oai/{tenant}` implementa OAI-PMH 2.0 completo (6 verbos, Dublin Core, cosecha selectiva) sobre los radicados **públicos** únicamente.
  *Por qué*: interoperabilidad exigida por el Modelo SGDEA y el Marco de Interoperabilidad de Gobierno Digital.
  *Impacto*: el filtro de publicidad vive en la fuente — la omisión de un reservado es indistinguible por construcción (Ley 1712 arts. 18-19).

- `feat(tenant)`: **PINAR — Plan Institucional de Archivos (RF-ADM-08, E14)**
  `tenant-service` gana `/api/v1/pinar/*`: 9 tablas nuevas (aspectos críticos, ejes articuladores, priorización determinista, proyectos, seguimiento append-only), máquina de estados `borrador→aprobado→en_ejecucion→cerrado` con un único plan en ejecución por tenant.
  *Por qué*: cierra el último requisito normativo pendiente de codificación (Acuerdo AGN 003/2015).
  *Impacto*: aprobar exige acto administrativo motivado; el contenido queda congelado tras aprobar.

- `fix(document)` + `feat(document,archive)`: **Cierre de deudas declaradas (E08/E10/E02)**
  No-write-up al radicar (403 si el nivel excede el clearance del radicador); atribución de los asientos de reconciliación WORM al actor de sistema; el manifiesto de exportación de expedientes gana asunto/tipo documental.

- `feat(archive)` + `feat(storage)`: **Exportación de expedientes a ZIP (E02/export; Ley 594/2000 art. 19)**
  `GET /api/v1/expedientes/{id}/export.zip` empaqueta el índice electrónico firmado, un manifiesto CSV y los anexos, con checksums y un `LEEME.txt` que declara el alcance (copia de consulta, no un AIP acreditado).
  *Por qué*: no había forma de descargar un expediente completo como paquete portable; el AIP (E10) es un paquete de preservación bajo WORM, no una copia de usuario.
  *Impacto*: no-read-up a nivel de expediente Y por radicado (un radicado clasificado vinculado a un expediente de menor nivel deja de filtrar sus bytes).

- `feat(document)`: **Índice de información clasificada y reservada (E08; Ley 1712/2014 art. 20 + Decreto 1081/2015)**
  `GET /api/v1/reports/indice-reservado(.csv)` publica, content-free, qué radicados están reservados/clasificados, bajo qué excepción y desde cuándo — nunca el asunto ni el contenido.
  *Por qué*: la Ley 1712 obliga a publicar el registro de información clasificada y reservada.
  *Impacto*: exige motivación jurídica obligatoria al clasificar (nivel≥2); export CSV con neutralización de inyección de fórmulas.

- `feat(document)`: **Integración con operador postal como consumidor de webhooks (E20 F5; RF-POR-08; ADR-018)**
  `POST /api/v1/public/postal/callback/{tenant_slug}` — callback entrante idempotente autenticado por HMAC per-tenant, que separa la notificación automática del operador del endpoint humano de despacho (que antes conflaba ambos y filtraba datos de radicados clasificados).
  *Impacto*: cierra el residual D-14 #3; el `X-OrpycaMCP-Signature` tuvo que añadirse además al whitelist del gateway (bug real que inutilizaba la feature end-to-end).

- `feat(knowledge)`: **RAG generativo con citas (E21 Inc.3; ADR-006)**
  `POST /knowledge/rag` genera respuestas ancladas con citas (nativas vía la Citations API de Anthropic, o best-effort con otros proveedores), sobre un `LLMProvider` pluggable con default soberano `ollama_local`.
  *Por qué*: decisión de soberanía tomada — el material reservado nunca sale a un proveedor externo, ni siquiera para generación.
  *Impacto*: barrera dura `is_generatable` sin override; degradación honesta a solo-recuperación (503) si no hay proveedor disponible.

- `feat(knowledge)`: **Recuperación anclada con citas — `POST /knowledge/antecedentes` (E21 Inc.2; ADR-006)**
  Expone la búsqueda semántica existente como resultados anclados a su fuente, marcados con un advisory fijo (índice derivado, a confirmar contra la fuente autoritativa) — sin LLM, sin egreso.
  *Impacto*: un `radicado_ref` inexistente o sobre-clearance colapsa al mismo 404, sin oráculo.

- `feat(knowledge)`: **Proveedor de embeddings real, local/soberano (E21 Inc.1; ADR-006)**
  `EmbeddingProvider` pluggable con `local_st` (sentence-transformers, corre en el propio contenedor) como default de despliegue, reemplazando el stub hash determinista usado para validar el groundwork.
  *Impacto*: migración que renueva la dimensión del vector (64→384) y el índice ANN a HNSW; material reservado ya puede embeberse localmente sin salir a terceros.

- `feat(storage)`: **Validación PDF/A en la ingesta/captura de anexos (E07/E10 C2b follow-up)**
  La validación veraPDF, que antes solo corría al empaquetar el AIP, corre ahora también al subir el anexo (modo advisory por defecto, opt-in strict).
  *Por qué*: detectar la no-conformidad al capturar, cuando aún se puede pedir un mejor archivo, no al cerrar el expediente.
  *Impacto*: el empaquetado del AIP reutiliza el veredicto de ingesta si es terminal y del mismo perfil.

- `feat(storage)` + `feat(archive)`: **AIP índice-only para expedientes 100% físicos (E10 AIP wiring follow-up)**
  Un expediente sin anexos digitales ya no queda `omitido` sin ningún paquete OAIS — el índice firmado se empaqueta ahora como payload de un Bag BagIt válido.
  *Impacto*: comportamiento inferido automáticamente (sin flag), con nota PREMIS afirmativa declarando soportes físicos referenciados.

- `feat(archive)` + `feat(storage)`: **Perfil PDF/A configurable por serie documental / TRD (E10 C2b follow-up)**
  Nueva columna `trd_series.pdfa_profile` permite fijar el flavour PDF/A adecuado por serie en vez de un global único.
  *Impacto*: regla `invalid ≠ default` — un perfil inválido nunca se sustituye por el default, se degrada honestamente a `not_evaluated`.

- `feat(archive)` + `feat(storage)`: **Renovación WORM del AIP para series de Conservación Total (E10)**
  Completa la renovación de retención de los 3 artefactos (índice, acta, AIP) — el AIP quedaba fuera de la fase de renovación pese a que su lock COMPLIANCE también vence.
  *Impacto*: cierra la deuda Alta que había dejado abierta el cableado del AIP.

- `feat(archive)` + `feat(document)` + `feat(storage)`: **Cableado del AIP OAIS al cierre del expediente (E10)**
  Archive dispara el empaquetado real (BagIt+PREMIS v3+índice firmado) al cerrar el expediente, vía reconciliación best-effort — el endpoint ya existía pero nadie lo llamaba.
  *Impacto*: primer endpoint interno de inventario cross-servicio (document-service) sin gate de clearance por diseño — completitud de preservación es ortogonal al control de acceso de lectura.

- `feat(storage)`: **Validación PDF/A veraPDF real vía subprocess endurecido (E10 Increment C2b)**
  Reemplaza el stub honesto por un validador real, ejecutando veraPDF (Java) sobre PDFs no confiables con un modelo de seguridad completo (sin shell, entorno saneado, límites de concurrencia/memoria/salida, JSON-only).
  *Impacto*: JRE + veraPDF solo en la imagen de deploy (multi-stage); sigue advisory, nunca bloquea el AIP.

- `feat(storage)` + `feat(archive)`: **Renovación de retención WORM para series de Conservación Total (E10 debt)**
  Los objetos bajo Object-Lock COMPLIANCE (índice, acta) vencían tras la retención TOTAL de la TRD aunque la serie fuera de conservación permanente; nueva fase de reconciliación los renueva con ventana rodante.
  *Impacto*: filtro fail-closed por disposición — solo CT se renueva, nunca E/S/M; se retira además una ruta pública irreversible que un `crud≥3` podía usar para fijar el lock de un artefacto no-CT.

### Fixed

- `style(frontend)` + `fix(frontend,repo)`: **`npm run lint` vuelve a ejecutar ESLint — y el formateo destapó tres defectos reales**
  El script es `prettier --check . && eslint .`, y **676 archivos** sin formatear cortaban la cadena en el `&&`: ESLint llevaba tiempo sin correr en CI. Formateados todos (commit de formato, sin cambios de lógica) y cerrados los 12 errores que ESLint reportó al volver a ejecutarse. Lo relevante no es el formateo sino lo que **destapó, no causó**:
  1. **Diez atributos Svelte con comillas dobles sin escapar** — `emptyDescription="Crea el primero con "Nuevo usuario"."` y nueve gemelos. El parser corta el valor en la segunda comilla, así que el texto visible quedaba truncado en *"Crea el primero con "*. Es un defecto de interfaz real en diez pantallas de `/admin`, invisible hasta que Prettier no pudo parsear el archivo — `svelte-check` y `build` pasaban. Corregidos con las comillas angulares «» que el proyecto ya usa en el resto de textos.
  2. **`.svelte-kit.root-stale/` estaba versionado**: 737 archivos generados, 16 MB, commiteados en `32fc631`. Ni `.svelte-kit/` del `.gitignore` ni el hook `guard-bash` casaban con el nombre renombrado. Sacado del índice con `git rm -r --cached` (**sin borrarlo del disco**) y los patrones de `.gitignore`/`.prettierignore` pasados a `.svelte-kit*/`. **Segunda vez** en esta línea base que un patrón demasiado literal deja entrar lo que debía excluir — la primera fue `documentos/` sin anclar.
  3. **Prop muerta `icon` en `CatalogCrudPanel`**: declarada y nunca renderizada (el `EmptyState` consume `emptyIcon`), con dos pantallas pasándola. Eliminada de las tres. Igual que `canManage` en `transferencias/[id]`, que quedó huérfana al sustituirla el lote 3 por `canManageWrite`.
  Dos supresiones **justificadas, no silenciadoras**: los `{@html}` llevan `eslint-disable` explicando que el saneo server-side (`sanitizeHtml.js`) es la barrera y el `{@html}` su consumidor; y el regex de caracteres de control lleva la suya porque esos caracteres son justamente lo que un `java\x00script:` usa para esquivar la detección. `svelte/no-unused-svelte-ignore` se apagó en `.eslintrc.cjs` con la razón escrita: es una **divergencia real** entre herramientas — `svelte-check` sí necesita esos `svelte-ignore` (quitarlos produce 13 avisos) y `eslint-plugin-svelte` no evalúa esa regla de a11y, así que borrarlos habría roto la verificación autoritativa para contentar a la que no lo es.
  *Why*: un `lint` que nunca alcanza a ESLint da una señal verde falsa; y el formateo, que parecía deuda cosmética, era lo único que impedía ver un texto truncado en diez pantallas.
  *Impact*: `svelte-check` **0 errores y 0 warnings** (antes 1), **1370/1370** tests, `build` limpio. `npm run lint` queda con **1 error**: `no-useless-escape` en `expedientes/[id]/api/export/+server.js`, uno de **14 archivos de `src/` que pertenecen a `root`** por un build de Docker que escribió en el volumen montado — no editables desde el host sin `sudo`, que no se ejecutó aquí. Se destraba con `sudo chown -R $USER:$USER frontend/src`.

- `fix(archive)`: **`expedientes_trd_serie_id_fkey` pasa a validada (ADR-025 Increment 2, migración 040)**
  La 037 añadió la FK `NOT VALID` para no hacer fallar la provisión por huérfanos históricos, dejando anotado en su propio comentario que el `VALIDATE CONSTRAINT` iba en el Increment 2. `NOT VALID` protege las escrituras nuevas pero **no** permite al motor afirmar la integridad referencial del historial, que es justo lo que la FK promete.
  **Decisión sobre huérfanos: la migración NO los depura.** Un `trd_serie_id` que no resuelve es evidencia de la posición que ese expediente ocupaba en la TRD que gobernó (o debió gobernar) su retención: ponerlo a `NULL` destruye esa evidencia sin dejar rastro de que existió, y borrar el expediente es borrar un documento público (Ley 594/2000). Ninguna de las dos es una operación de migración legítima — son decisiones archivísticas caso por caso. La migración **falla ruidosamente** listando cada huérfano con su estado y su `trd_revision_motivo`, no toca los datos, no valida el FK, y es **re-ejecutable** hasta que el historial quede limpio. Reutiliza la señal `serie_no_resoluble` que la propia 037 ya deja, auditando directamente contra `trd_series` para no depender de que `trd_serie_code` siga en sincronía.
  Guard por `current_schema()`, no por el nombre desnudo de la constraint — este proyecto ya se quemó con guards que repiten `conname` entre esquemas y dejan al tenant #2 sin el objeto.
  *Why*: cerrar el pendiente que la 037 declaró por escrito; una FK `NOT VALID` indefinida es una garantía que el esquema anuncia y no cumple.
  *Impact*: validada en **Postgres real** sobre schema desechable con las migraciones 001–037 aplicadas, en los tres caminos: con huérfano inyectado falla con el listado y deja `pg_constraint.convalidated = f` sin tocar datos; sin huérfanos valida; y la segunda corrida no reintenta el escaneo. Suite de `archive-service`: **721 passed, 164 skipped**.
  **No incluido, y no por descuido**: el gate D5 `assert_disposicion_final_autorizada` sigue **sin llamante**, porque **no existe ninguna ruta de eliminación efectiva en el sistema** — `StorageService.purge_for_disposition` tampoco tiene llamadores en producción. El gate se escribió deliberadamente por delante de la épica que lo usará (documentado en el docstring del módulo, en el addendum de ADR-025 y en `trazabilidad.md`), para que quien la implemente no tenga que inventar el criterio. Cablearlo hoy exigiría fabricar un endpoint de eliminación irreversible sin diseño previo, que es exactamente el atajo que ADR-025 §"Orden de incrementos" descarta al pedir que el gate sea "exigible cuando exista esa ruta".

- `fix(auth,frontend)`: **`/auth/me` descartaba el nivel CRUD de los permisos — la UI ofrecía escrituras que el backend niega con `403` (lote 3 de la revisión UX, causa raíz de contrato)**
  `auth-service/app/routers/auth.py` resolvía `{permiso: nivel_crud}` con `resolve_effective_permissions` y devolvía `list(perms.keys())`: **el valor se tiraba**. El frontend recibía una lista plana y su `can("USUA_PERM_EXPEDIENTE")` no distinguía lectura de escritura, mientras los routers gatean con `min_crud=3` (RF-SEG-03) — así que se ofrecían Cerrar / Transferir / Excluir / Firmar acta a quien tenía el permiso en modo lectura. **Las dos revisiones de UX, ciegas entre sí, llegaron a este punto por caminos independientes y ambas se negaron a parchearlo pantalla por pantalla**: es contrato, no pantalla.
  **Backend**: `permission_levels: dict[str, int]` en `UserInfoResponse`, **aditivo** — `permissions[]` se conserva con idéntica semántica, de modo que ningún consumidor vivo se rompe y no hace falta despliegue coordinado. Escala real leída de `auth_group_permissions.crud` (`migrations/tenant/001_rbac.sql`), no inventada: `0` ninguno · `1` leer · `2` editar · `3` crear/borrar · `4` nivel_1 · `5` nivel_2; ROOT resuelve `USUA_PERM_ROOT: 5`. Fail-closed **simétrico**: sin contexto de tenant o con usuario no aprovisionado, `[]` y `{}` — nunca ausentes ni `null`, para que un consumidor no pueda leer la ausencia como "sin restricción". Barrido de `resolve_effective_permissions` en los 11 servicios: los otros dos usos (`GET /users/{id}/permissions` y `get_caller` de `authz.py`) ya conservaban el diccionario completo; el aplanamiento era único.
  **Frontend**: `can(permission, minCrud)` con `minCrud` **opcional** (sin él, retrocompatible por presencia) y fail-closed explícito `get(permissionLevels)[permission] ?? 0`. Al aplicarlo se cruzaron **todos** los gates de escritura contra el `require_permission` real de su router, y el resultado corrige la premisa del hallazgo: la mayoría de las mutaciones usan `min_crud=1`, así que solo **cuatro** pantallas necesitaban nivel 3 — circuito de convalidación de TVD y de TRD, borrado de tipo documental y de elemento de metadato, y nueva versión del plan de preservación. Añadir nivel 3 donde el backend no lo exige habría sido el mismo defecto en dirección contraria, fallando en silencio.
  Dos defectos colaterales cerrados: `radicar/+page.svelte` evaluaba `can()` dentro de un `$:` sin ninguna dependencia reactiva —`can()` usa `get()`, que no es reactivo, luego esa línea nunca se reevaluaría tras un cambio de sesión en el mismo montaje del componente—, y un em dash pegado a un parámetro JSDoc opcional (`[minCrud] —`) rompía el parser de TypeScript. Verificado que `context/switch` **no** altera permisos ni niveles, solo `active_depe_id`.
  *Why*: la UI debe ser espejo del RBAC del backend, no una aproximación por presencia; ofrecer una acción que el servidor va a rechazar es un defecto funcional, no cosmético.
  *Impact*: sin migración. `auth-service` **199 passed, 15 skipped** (Docker), con un test que reproduce el escenario exacto: mismo permiso en `crud=1` contra un gate `min_crud=3`. Frontend **1370/1370**, `svelte-check` 0 errores, build limpio. Anotado y **no** tocado: en `/bandeja` el gate de la UI es más estricto que el backend (devolver y visto bueno autorizan por tenencia del paso, sin `require_permission`) — es un cambio de comportamiento distinto.

- `fix(frontend)`: **accesibilidad transversal — contraste AA en estados permanentes, tablas inalcanzables por teclado, errores de campo en `/perfil` y sugerencias de IA sin anunciar (lote 2 de la revisión UX)**
  **Contraste (WCAG 1.4.3)**: blanco sobre `--op-primary` da 4.28:1 y falla AA. El defecto estaba en estados **permanentes**, no en `:hover`: los tres `Stepper` (2FA, credencial PKI, firma), el turno activo de la cadena de firma y los chips de filtro de `/borradores` y `/envios` —estos literalmente **invertidos**, con `--op-primary-dark` en el hover y el claro en reposo—. Todos a `--op-primary-dark` (6.56:1), que es lo que `Button.svelte` ya hacía bien; las páginas se habían desviado del propio sistema de diseño. Más cuatro instancias del otro par que falla, `--op-text-secondary` sobre `--op-primary-light`/`--op-success-bg`/`--op-error-bg` (~4.0:1).
  **Tablas (WCAG 1.4.10 y 2.1.1)**: `tabindex="0"` + `role="region"` + `aria-label` + `:focus-visible` en `DataTable` y `CatalogCrudPanel` —que lo propagan a 8+ y 7 pantallas— y en ~20 envoltorios de tabla cruda; `bandeja-table`, la única con `overflow: hidden` y ningún mecanismo de scroll, gana envoltorio propio con `overflow-x: auto`. Los `overflow: hidden` restantes envuelven un `DataTable` cuyo scroll vive en el wrapper interno: **no** se les añadió tab-stop, sería mudo y redundante. Cada `tabindex` nuevo dispara `a11y-no-noninteractive-tabindex`, regla que no reconoce la técnica WCAG SCR29; silenciada con `svelte-ignore` justificado en vez de dejar el aviso suelto.
  **Estructura de tabla**: `CatalogCrudPanel` aplicaba `display: flex` sobre un `<td>`, sacando la celda del modelo de tabla en el árbol de accesibilidad; el flex pasa a un `<div>` interior. El hallazgo hablaba además de "cuatro tablas con una celda más que encabezados": un barrido de **las 28 tablas crudas**, comparando `<th>` contra `<td>` e incluyendo columnas condicionadas por permiso, **no pudo reproducirlo** — se deja constancia en vez de forzar el conteo.
  **`/perfil`**: los siete errores de validación salían solo como toast, nunca asociados al campo, pese a que `FormField` ya soportaba `error` con `aria-invalid`/`aria-describedby`. Los siete quedan asociados, y los cinco campos de OTP ganan `inputmode="numeric"` + `autocomplete="one-time-code"` (hubo que extender `FormField` con esas props, que no existían). **Sugerencias de IA**: `aria-live="polite"` en los chips de serie TRD y el panel de antecedentes; el contenido no se tocó, `ragInsert.js` ya cumple el requisito normativo.
  *Why*: la pantalla que gobierna la credencial de firma electrónica no puede ocultar a un lector de pantalla qué campo falló, ni las columnas de Acciones pueden quedar fuera de alcance en móvil o por teclado.
  *Impact*: `Stepper`, `DataTable`, `CatalogCrudPanel` y `FormField` son compartidos, así que el arreglo se propaga a la mayoría de las pantallas. **1348/1348** tests tras este lote (113 archivos), `svelte-check` 0 errores, build limpio. **No** incluido a propósito: el código de recuperación de 2FA, que es brecha de producto en `auth-service` y no debe maquillarse en el frontend.

- `fix(frontend)`: **la carga masiva de `/admin/interoperabilidad` no reportaba resultado nunca + tarjetas de `/admin` hacia un `403` + dos pantallas sin enlazar (lote 1 de la revisión UX de 2026-08-02)**
  Primer lote de corrección de la revisión de UX/accesibilidad que las ~20 pantallas del cierre del desfase API↔UI nunca recibieron. Los tres hallazgos **funcionales**, no cosméticos. (1) **Carga masiva sin resultado**, dos defectos independientes que se sumaban: `createJobPoller` mutaba un objeto plano nunca reasignado —Svelte 4 no invalida, el panel quedaba congelado en "Consultando estado…"— y los dos proxies de sondeo pedían `${API_V1}/batch/{jobId}/status`, la forma **anterior** al fix de ruteo del gateway, que no casa con ningún prefijo → `404` permanente. Corregido con variables `let` de componente reasignadas y las URL anidadas reales (`/batch/{documents,expedientes}/{jobId}/status`). (2) **Tarjetas de `/admin` hacia un `403`**: cruzadas las **16** tarjetas contra el `require_permission` real de su router —no solo las tres observadas, porque el patrón ya había reaparecido cuatro veces—; `TRD / CCD` y `Metadatos` pasan a `USUA_PERM_TRD`, e `Interoperabilidad`, cuyo gate es heterogéneo por pestaña, se abre con `USUA_PERM_EXPEDIENTE || PERM_RADI` y filtra cada pestaña con su permiso propio, dejando de ocultarse a un radicador que sí puede usarla. (3) **`/admin/preservacion` y `/admin/seguridad/claves`**, construidas y alcanzables solo escribiendo la URL, añadidas al índice de tarjetas.
  Dos correcciones de higiene que son la causa, no el síntoma: se **borraron los comentarios obsoletos** que afirmaban que el gateway no enrutaba `/api/v1/batch/` y que los routers de metadatos de `document-service` no gateaban por permiso —ambas frases falsas, y la primera es literalmente el vector que produjo el `404`—; y se añadieron **tests de contrato de la URL** que fijan la forma exacta que cada proxy pide al gateway, la defensa que faltaba y que ya se había pagado antes con `FORWARDED_REQUEST_HEADERS`.
  *Why*: las pantallas se implementaron sin pasar por el revisor de UX, último eslabón de la cadena de agentes del proyecto; el resultado no fue estético sino una pantalla dada por terminada y documentada como completa cuya función principal no operaba en ninguna circunstancia.
  *Impact*: `interoperabilidad/+page.svelte` (sondeo reactivo + pestañas gateadas), 2 proxies `[jobId]/+server.js` (URL corregida), `admin/+page.svelte` (`ADMIN_AREAS`: 3 permisos corregidos + 2 tarjetas nuevas), `metadatos/*` y `plantillas/+page.svelte` (comentarios). Suite del frontend **1339/1339**, `svelte-check` 0 errores, `build` limpio.

- `fix(repo)`: **`.gitignore` ocultaba los proxies de carga masiva de documentos, que nunca llegaron a versionarse**
  El patrón `documentos/` —escrito para el `documentos/` de la raíz (material de trabajo local)— no llevaba barra inicial, así que git lo aplicaba a **cualquier** directorio con ese nombre a cualquier profundidad, incluido `frontend/src/routes/(app)/admin/interoperabilidad/api/lotes/documentos/`. Esos cuatro archivos quedaron fuera del commit de la Fase 8 pese a estar documentados como entregados: en `main`, la pantalla llamaba a rutas que no existían en el repositorio. Corregido anclando el patrón a `/documentos/`.
  *Why*: hallazgo de higiene destapado al reejecutar el lote 1 sobre un working tree limpio; no lo detectó la revisión UX, que lee el árbol de trabajo y por tanto veía los archivos.
  *Impact*: sin cambio de comportamiento en la aplicación; el `documentos/` de la raíz sigue ignorado exactamente igual. Lección: un patrón de `.gitignore` sin anclar es un filtro sobre todo el árbol, y su fallo es **silencioso** — el archivo simplemente no aparece en `git status`.

### Security

- `fix(workflow)`: **`GET /workflows/inbox`/`/pending` acotados por dependencia (RF-SEG-08, E05)**
  Ambos listados no tenían gate de permiso — cualquier autenticado del tenant veía la carga de trabajo completa de la entidad, y era además la primitiva de enumeración que explotaba el `step_id` en `complete`.
  *Impacto*: alcance por defecto = lo propio; ampliar con `dept`/`assigned_to` ahora exige `PERM_TRAMITAR` o `USUA_PERM_ADMIN`.

- `fix(workflow)`: **Cierre de hallazgos bloqueantes en workflow-service (RF-SEG-03/08; E05)**
  `complete_step` cerraba el paso activo de otra dependencia con solo el `step_id` sin verificar tenencia; `assign`/`transfer` confiaban en `radicado_id`/`tracking_number` del cuerpo sin verificarlos; `action` era falsificable; `/evaluate` no tenía gate; la reasignación en cascada no validaba el destino.
  *Impacto*: autorización anclada en el mismo `UPDATE` que la mutación, sin ventana TOCTOU; sin migración de esquema.

- `fix(notification)`: **Sellado del endpoint de envío + autorización de webhooks y del historial (RF-SEG-03; ADR-018)**
  `POST /notifications/send` es un endpoint servicio-a-servicio (signature-service lo llama para OTP de 2FA) pero el gateway lo exponía a cualquier JWT válido — un tercero podía enviar correo arbitrario con la identidad SMTP institucional.
  *Impacto*: sellado con `X-Internal-Token`; webhooks e historial pasan a `USUA_PERM_ADMIN`. signature-service quedó limpio en el barrido.

- `fix(workflow)`: **Enforcement RBAC de las transacciones de trámite y las rutas de distribución (RF-SEG-03; E05)**
  `execute_transaction` ignoraba `transaction_types.requires_permission` (la migración lo daba por delegado al gateway, que en realidad solo autentica) — cualquier autenticado podía ejecutar transacciones reservadas como `anular` o `cerrar_exp`.
  *Impacto*: `assign`/`transfer`/`complete_step` exigen `PERM_TRAMITAR`; `reassign_cascade`/`scan_overdue` exigen `USUA_PERM_ADMIN`.

- `fix(document)`: **Cierre del no-read-up en firmas, anulación, respuesta vinculada y envíos (RF-SEG-08; RF-FIR-12)**
  9 endpoints más con el mismo patrón de olvido: `crear_respuesta` copiaba el asunto de un antecedente reservado a un documento legible; `signatures.py` no tenía ningún gate (oráculo de verificación de hash incluido); `anulacion.py` mutaba el ciclo de vida de un radicado invisible.
  *Impacto*: gate dentro del propio `INSERT` en las escrituras, para que comprobación y escritura sean atómicas.

- `fix(document)`: **No-read-up en disposición y PATCH de radicados (RF-SEG-08; RF-MET-08)**
  `GET`/`PATCH .../disposition` y `PATCH /documents/{id}` estaban gateados solo por permiso, sin filtro de clearance — permitía leer y fijar la disposición final de un radicado clasificado, y el PATCH devolvía el registro completo como efecto colateral.
  *Impacto*: `max_clearance` pasa a obligatorio en la firma del repositorio (sin default "sin restricción").

- `fix(archive)`: **Atribución del `audit_log` de la renovación WORM al principal de sistema (E10)**
  Los 6 asientos de la fase de renovación usaban `actor=None`; se atribuyen ahora a `SYSTEM_RECONCILER_ID`, igual que sus gemelos de protección/empaquetado.

- `fix(archive)` + `fix(storage)`: **No-read-up en la consulta de eventos de preservación por expediente (E10 Increment C)**
  `GET /preservacion/eventos?object_ref=` (storage) filtraba solo por permiso, no por clearance, revelando actividad de preservación de un expediente clasificado.
  *Impacto*: archive media el acceso resolviendo el clearance contra el nivel vigente; storage se sella con `X-Internal-Token`.

- `fix(archive)` + `fix(storage)`: **No-read-up en la descarga del AIP de preservación (E10 Increment C)**
  El endpoint de AIP devolvía el inventario documental completo (nombres, SHA-256, tamaños) gateado solo por permiso.
  *Impacto*: se media por archive-service (el dueño de la clasificación); storage se sella al exterior; una lectura exitosa deja asiento de acceso fuerte.

- `fix(workflow)`: **CRUD de reglas de enrutamiento automático sin autorización alguna (RF-SEG-03; E05)**
  `rules.py` no tenía `require_permission` en ninguna ruta — cualquier autenticado podía desviar la correspondencia entrante hacia su propia dependencia.
  *Impacto*: `USUA_PERM_ADMIN` en los 5 endpoints CRUD; `/evaluate`/`/evaluate/batch` quedan deliberadamente sin gate (solo-lectura, invocadas internamente en proceso, no por HTTP).

- `fix(document)` + `fix(storage)` + `fix(gateway)`: **No-read-up en la descarga de anexos (RF-SEG-08; ADR-006/D-02)**
  `GET /storage/files/{id}/download` emitía una presigned URL de MinIO sin ningún chequeo de clearance — cualquiera con el `file_id` descargaba el anexo de un radicado reservado, y la URL saltaba toda auth una vez emitida.
  *Impacto*: document-service media ahora toda descarga en streaming (nunca presigned); storage se sella; el gateway acota el routing a `/storage/upload` únicamente.

- `fix(workflow)`: **No-read-up en la bandeja de trámite + autorización por tenencia en `rollback` + cierre de oráculo en `devolver` (RF-SEG-08)**
  Los listados de bandeja/historial no filtraban por clearance; `rollback` revertía y devolvía metadatos de cualquier radicado con paso activo sin verificar tenencia; `devolver` distinguía por error entre "ajeno" y "no existe" (oráculo).
  *Impacto*: helper de clearance compartido en `COUNT`+`SELECT`; `rollback` ahora exige que el actor sea el tenedor actual del paso.

- `fix(document)`: **No-read-up en las Salidas vinculadas — `GET /documents/{id}/respuestas` (RF-SEG-08)**
  El listado de respuestas vinculadas a un antecedente no filtraba por clearance, pese al invariante documentado del servicio; hueco pre-existente hallado incidentalmente en auditoría.

- `fix(archive)`: **Ciclo de vida de transferencias — carrera TOCTOU letal cerrada + los cuatro actos de custodia con asiento e identidad unívoca (RF-SEG-08; E12)**
  `update_estado` no tenía guarda de estado esperado — dos llamadas concurrentes (recibir + rechazar) podían comprometer ambas, dejando un expediente `transferred` y una transferencia `rechazada` a la vez, estado jurídicamente imposible e irreversible; además `create`/`enviar`/`rechazar` no dejaban asiento y la resolución de identidad divergía entre rutas.
  *Impacto*: guarda de estado con `expected_estado` obligatorio; identidad unificada en `app/core/actor.py`; `recibir()` re-verifica fixity del acervo antes de congelar la custodia; `rechazar` exige motivo obligatorio. **Cambio de contrato**: `POST /transferencias/{id}/rechazar` pasa de `observacion?` a `motivo` obligatorio; `recibir` gana un `409 fixity_mismatch`.

- `test(archive)`: **Tier de integración contra Postgres real — la atomicidad no es verificable con mocks**
  El carril unit mockeaba la conexión, así que `conn.transaction()` era un context manager de mentira cuya ausencia no era detectable por los tests.
  *Impacto*: nuevo carril `tests/integration/` contra un schema de tenant desechable, con commits reales y verificación por mutación.

- `fix(archive)`: **Cerrada la ruta alterna `PATCH /expedientes/{id}` que transicionaba el ciclo de vida sin dejar rastro**
  El PATCH genérico admitía `status`, ejecutando las mismas transiciones que `/close`/`/transfer` pero sin asiento, sin evento, sin disposición TRD materializada y sin la guarda TOCTOU.
  *Impacto*: `status` se retira de `ExpedienteUpdate` con `extra="forbid"`; `transition_expediente` queda como único escritor de esa columna.

- `fix(archive)`: **Atomicidad transición↔asiento en cierre y transferencia + TOCTOU de disposición + ruta gemela de transferencia sin auditoría**
  `close_expediente`/`transfer_expediente` no abrían transacción (fallo entre transición y asiento dejaba estado sin traza); `transition_expediente` no tenía guarda de estado esperado; `TransferenciaService.recibir` congelaba custodia sin evento ni asiento.
  *Impacto*: las tres rutas se envuelven en una única transacción con el asiento como último write.

- `fix(archive)`: **Atomicidad vínculo/exclusión↔asiento de auditoría en link/unlink de radicados**
  Mismo residual que en la creación de expedientes, ahora en la composición: `link_radicado`/`link_radicados_batch`/`unlink_radicado` mutaban y auditaban en statements autocommit separados.

- `fix(archive)`: **Atomicidad expediente↔asiento de auditoría + carrera de código duplicado en la creación**
  El INSERT del expediente y su asiento no eran atómicos; además la generación del consecutivo (`EXP-AAAA-NNNN`) podía duplicarse bajo concurrencia por no usar `RETURNING`.
  *Impacto*: consecutivo colapsado a un único `INSERT…ON CONFLICT…RETURNING`, serializado por el row-lock dentro de la transacción.

- `fix(archive)`: **No-read-up al enumerar los expedientes de una unidad física + auditoría de la creación por batch**
  `GET /unidades/{id}/expedientes` enumeraba expedientes sin filtrar por clearance; la creación por batch no dejaba asiento de auditoría.

- `fix(archive)`: **No-read-up en el FUID — el inventario documental deja de filtrar expedientes clasificados (E12/V2)**
  El FUID (asunto, serie, signatura) se servía por 3 puertas sin filtro de clearance, pese a ser descripción de contenido.
  *Impacto*: 404 total para un solo expediente; filtro por fila para los inventarios agregados. Cierra el último diferido de RF-SEG-08 en expedientes.

- `fix(archive)`: **Respuesta acotada de link/unlink/rebuild — sin oráculo de pertenencia/volumen/ciclo de vida (M2, RF-SEG-08)**
  La mutación sobre un expediente clasificado es legítima sin clearance de lectura (gestión archivística), pero la respuesta filtraba pertenencia, volumen y estado a un usuario sin clearance.
  *Impacto*: la mutación siempre ocurre y siempre audita; solo se redacta la respuesta, con ack uniforme e indistinguible entre estados. Cierra la terna M1/M2/M3 de RF-SEG-08 en expedientes.

- `fix(archive)`: **No-write-up al crear expedientes — no se puede originar un expediente por encima del propio clearance (M3, RF-SEG-08)**
  `create_expediente` no contrastaba `nivel_seguridad` contra el clearance del creador — un usuario de clearance baja podía crear un expediente clasificado y perderlo de vista de inmediato.
  *Impacto*: 403 explícito, nunca un clamp silencioso a PUBLICA (que sería una desclasificación de facto).

- `fix(archive)`: **Disposición de expedientes clasificados sin 404 espurio + respuesta acotada que no filtra contenido (M1, RF-SEG-08)**
  `close`/`transfer` devolvían 404 pese a haber mutado ya (por no propagar el `user_id` al read final), afectando incluso a archivistas con clearance suficiente.
  *Impacto*: la disposición se declara ortogonal al clearance de lectura (gatearla invertiría el mínimo privilegio); la respuesta se estrecha para no exponer contenido.

### Fixed

- `fix(api-gateway,document,archive)`: **Batch API inalcanzable — `/api/v1/batch/` sin registrar en el gateway, y contrato ambiguo entre servicios**
  Cierra el hallazgo de alcance dejado por la Fase 8 del frontend (`/admin/interoperabilidad`): el gateway no tenía `/api/v1/batch/` en su tabla de ruteo, así que las dos secciones de carga masiva devolvían `404 route_not_found`. El fix no fue solo añadir la entrada: `document-service` y `archive-service` compartían el **mismo** prefijo `/api/v1/batch`, y `GET /api/v1/batch/{job_id}/status` estaba definido con **idéntica forma** en ambos — ningún ruteo por-prefijo puede decidir a qué upstream pertenece un `job_id` dado. El estado se **anida bajo el tipo de trabajo** (`GET /api/v1/batch/documents/{job_id}/status` → document-service, `GET /api/v1/batch/expedientes/{job_id}/status` → archive-service), simétrico con el `POST` que ya distinguía por subruta; el gateway registra ambos prefijos completos (`/api/v1/batch/documents`, `/api/v1/batch/expedientes`) antes del genérico `/api/v1/archive/`. De paso, `POST /api/v1/batch/documents` y el nuevo `GET .../status` de document-service pasan a exigir `PERM_RADI` (igual que la creación individual de radicados, `POST /api/v1/documents/`) — antes no exigían ningún permiso, a diferencia de `archive-service`, que sí gateaba con `USUA_PERM_EXPEDIENTE`.
  *Why*: sin ruteo, la carga masiva construida en la Fase 8 del frontend era inalcanzable; el contrato compartido tenía que desambiguarse en la fuente, no parcharse en el gateway.
  *Impact*: 2 rutas públicas cambian de forma (`GET /api/v1/batch/{job_id}/status` → `GET /api/v1/batch/{documents,expedientes}/{job_id}/status`); `document-service` gana el gate `PERM_RADI` en ambos endpoints de batch. La deuda que esta entrada declaró abierta —la creación masiva de radicados sin asiento en `audit_log`, a diferencia de `batch/expedientes`— quedó **cerrada** por la entrada `fix(document)` de más arriba.

- `fix(frontend)`: **Barrido de accesibilidad tras la auditoría del rediseño v1.0 — 27 hallazgos Alto/Crítico (E22, WCAG 2.1 AA)**
  Contraste AA en `Button`/`BreadcrumbBar`/`AssistantDock` y varias vistas; objetivos táctiles ≥44px en overlays y layout; foco visible en `UserPicker`; confirmación explícita antes de firmar en lote.

- `fix(frontend)`: **Bucle de redirección entre Keycloak y `/login` (E22)**
  Cuando Keycloak devolvía `/login?error=…`, el `load` reiniciaba PKCE sin mirar el parámetro, generando un bucle infinito sin mensaje. Ahora se lee el error y se muestra un mensaje mapeado desde una tabla cerrada en español.

- `fix(frontend)`: **Comentarios `//` en bloques `<style lang="scss">` rompían la compilación SSR cruda (E22)**
  `Modal.svelte` y `Drawer.svelte` usaban comentarios de línea que el parser CSS sin preprocesar no entiende; cambiados a `/* */`.

- `test(archive)`: **Test de integración del AIP `omitido` desalineado del default del kill-switch (E10)**
  El test esperaba `omitido` para un expediente sin anexos, pero el default de `aip_indice_only_enabled` es ON (empaqueta AIP índice-only). Solo-test, sin cambio de producción.

- `fix(migrations)`: **Guards de idempotencia sin filtrar por schema rompían la provisión del 2º tenant en adelante**
  Los guards de `IF NOT EXISTS` comprobaban por nombre de constraint/columna sin acotar a `current_schema()`; como los nombres se repiten entre tenants, el guard omitía el objeto en todo tenant provisionado después del primero.
  *Impacto*: crítico en workflow/notification/signature (la migración de DLQ abortaba, bloqueando la provisión completa de un 2º tenant); se añaden migraciones de backfill para tenants ya afectados.

- `fix(knowledge)`: **`POST /api/v1/knowledge/search` devolvía 500 contra Postgres real (E21)**
  El pool de asyncpg no registraba un codec `jsonb`, así que `metadata`/`acl` se leían como texto crudo y Pydantic fallaba con cualquier chunk real; los tests unit no lo veían por mockear la conexión.

- `fix(scripts)`: **`init_tenant.py` provisionaba tenants incompletos y abortaba a media provisión (E14/E21)**
  Faltaban migraciones de knowledge-service y tenant-service en el registro (PINAR quedaba inservible, sin error visible); además la identidad de migración por nombre desnudo colisionaba entre servicios y abortaba la provisión a mitad.
  *Impacto*: identidad pasa a `servicio/archivo`, con backfill para tenants ya provisionados.

- `fix(knowledge)`: **2 tests de contención de inyección de prompt estaban en rojo desde su propio commit (E21 Inc.3)**
  Las aserciones buscaban el texto malicioso verbatim, pero la remediación anti-inyección lo escapa con `html.escape`; nunca corrieron en verde porque la imagen no llegaba a construirse.

- `fix(knowledge)`: **El `requirements.lock` contradecía su propia declaración y hacía irreconstruible el servicio**
  El lock resolvía la variante CUDA de `torch` (~5 GB) pese a declarar "CPU-only suficiente" — knowledge-service era el único servicio que no compilaba.
  *Impacto*: dependencia pesada separada a `requirements-ml.txt` con índice CPU; `ARG WITH_ML` (default 1) permite imágenes de 212 MB sin la capa ML.

- `fix(storage)`: **El gate de disposición deja de postergar la eliminación del contenido por la retención del acta de transferencia (E10)**
  El gate bloqueaba la purga de todo el expediente mientras cualquier objeto WORM tuviera retención futura, incluyendo el acta (que ancla su retención en `firmado_at`, posterior al cierre) — la eliminación mandada por TRD quedaba postergada indebidamente.
  *Impacto*: se distingue CONTENIDO (bloquea) de INSTRUMENTO DE CONTROL (no bloquea); `legal_hold` sigue siendo bloqueo incondicional.

- `fix(frontend)`: **`radicar` usa el `SchemaField` compartido — cierra la degradación de campos `enum`/`boolean` del tipo documental a texto libre (E22/E03)**
  El mapeo inline de `radicar` degradaba campos `enum`/`boolean` a texto libre, permitiendo valores inválidos que el backend rechazaba con 422.

- `fix(frontend)`: **Sincroniza la descarga de anexos del workspace de expediente con el nuevo contrato mediado por `document-service` (D-05, D-02, RF-SEG-08)**
  El backend cerró la descarga directa a storage-service (presignada, sin clearance) y el front seguía apuntando al contrato viejo; se reestructura el proxy BFF al nuevo endpoint mediado.

- `fix(frontend)`: **El diálogo de confirmación de radicación nunca era visible en producción — faltaba la prop `open` del `Modal`**
  `radicar` montaba el `Modal` sin pasar `open`, que gatea todo su markup interno; no había forma de disparar `submitRadicacion()` desde la UI. Defecto crítico pre-existente, hallado incidentalmente.

### Changed

- `chore(compose)`: **knowledge-service configurable para máquinas sin capacidad de modelo local (E21)**
  El compose de desarrollo sobreescribe por entorno `EMBEDDING_PROVIDER=stub`+`AI_PROVIDER=ollama_cloud` para equipos sin GPU/modelo local; los defaults soberanos de `config.py` no se tocan.
  *Impacto*: `stub` sigue siendo recuperación léxica real (no un vector falso) y local, así que el material clasificado se sigue indexando; con proveedor de generación externo el material reservado nunca se envía al LLM.

### Added

- `feat(archive)` + `feat(storage)`: **Cableado WORM/Object-Lock del ACTA de transferencia firmada (E10 Inc.2)**
  Tras confirmarse el sello del acta de entrega, se protege inmutable vía Object-Lock COMPLIANCE — espejo del cableado del índice (Inc.1), con retención anclada en `firmado_at` para que nunca expire antes de la disposición del expediente.
  *Impacto*: generaliza `proteger-indice`→`proteger-artefacto` con `tipo`; `/proteger-indice` se conserva como alias duro.

- `feat(archive)` + `feat(storage)`: **Cableado WORM/Object-Lock del índice electrónico firmado al cierre (E10 Inc.1)**
  La capacidad WORM (Object-Lock COMPLIANCE) existía pero nadie la disparaba — el índice firmado quedaba mutable/borrable pese a estar sellado XAdES.
  *Impacto*: disparo síncrono best-effort + fase de reconciliación; retención derivada de la TRD (total), fail-closed sin TRD (nunca un default sobre un lock irreversible).

- `feat(auth)`: **Purga del material privado de las credenciales de firma revocadas (E17/F4)**
  Un job de ops anula el material cifrado de credenciales revocadas tras una ventana de retención (default 30 días), conservando todo lo público — la verificación de firmas pasadas no se ve afectada.
  *Por qué*: una clave privada revocada no tiene uso operativo y retenerla es un pasivo de seguridad.
  *Impacto*: minimización de datos — no cambia el tier legal de la firma (sigue art. 7, no acreditada).

- `feat(auth)`: **Rotación de la KEK de custodia de las claves de firma personales (E17/F4)**
  Llavero versionado que permite rotar la KEK maestra (compromiso sospechado, higiene periódica) sin re-enrolar a cada usuario, vía un job de ops con re-wrap y round-trip de verificación antes de persistir.
  *Impacto*: retrocompat byte-idéntica sin rotación configurada; una KEK vieja solo se retira cuando su conteo llega a cero en todos los tenants.

- `feat(archive)`: **Firma electrónica personal PKI del acta de entrega — elaborador (rol `elabora`), Incremento 3 (E17/F4)**
  Completa el trío de responsables del FUID: quien creó la transferencia firma personalmente, en un campo booleano ortogonal (`elaborador_firmado`) que no se pliega dentro de `firmada_completa`.
  *Impacto*: el rol se deriva de la identidad (`creada_por`), nunca lo declara el cliente.

- `feat(archive)`: **Firma electrónica personal PKI del acta de entrega — remitente (rol `entrega`) + `firmada_completa` bilateral, Incremento 2 (E17/F4)**
  Completa el par de firmas de responsables (receptor + remitente), independientes y en cualquier orden, cada una con su propio TOTP.
  *Impacto*: el auto-traslado (misma persona envía y recibe) se permite pero se señala con un flag para visibilizar la ausencia de separación de funciones.

- `feat(archive)` + `feat(signature)`: **Firma electrónica personal PKI del acta de entrega de transferencia — receptor (rol `recibe`), Incremento 1 (E17/F4)**
  Quien recibe la transferencia puede firmar personalmente el acta con su cert PKI, como paso explícito posterior a `recibir()`, reutilizando el primitivo de firma-remota ya validado en la cadena de radicados.
  *Impacto*: cierra un bloqueante Alta — sin cotejo de identidad, un impostor podía firmar como receptor y bloquear permanentemente al legítimo.

- `feat(signature)`: **Gate `firma_personal_require_xades` por nivel de seguridad — Incremento 3 del epic (E17/F4)**
  Cierra el fail-open silencioso: un cert ausente o auth-service caído degradaba la firma a HMAC nativa sin que nadie lo supiera, incluso para actos reservados/clasificados.
  *Impacto*: tres modos (`off`/`clasificados`/`todos`); el gate corre antes de consumir cualquier factor 2FA para no quemar el TOTP en una denegación.

- `feat(auth)` + `feat(signature)`: **Revocación en línea de la firma personal PKI + anclaje de la sub-CA de usuarios en `/verify`, Incremento 2 (E17/F4)**
  La firma personal XAdES por-usuario no tenía forma de revocarse; `/verify` confiaba solo en el pinning del fingerprint sin anclar la cadena.
  *Impacto*: revocación en línea sin caché (consultada en cada verificación); veredicto matizado que distingue validez criptográfica de estado de revocación.

- `feat(signature)` + `feat(auth)`: **Firma electrónica personal PKI por-usuario (XAdES-B/T), custodia servidor — Incremento 1 (E06/E17 F4)**
  Eleva la firma de la cadena de un HMAC opaco de servidor a XAdES verificable con material criptográfico separado por usuario; la clave privada nunca sale de auth-service (split de firma-remota, seam a HSM/firma cualificada).
  *Impacto*: sigue siendo firma electrónica del art. 7 — mismo tier legal, cambia la verificabilidad y el formato, no el valor legal.

- `feat(archive)` + `feat(signature)`: **Firma XAdES del acta de entrega de transferencias (H-H, E06/F4)**
  El acta de entrega ya tenía tres garantías de integridad pero ninguna firma XAdES; recibe ahora un sello institucional XAdES-B/T/LT/LTA + OCSP, sobre el mismo XML canónico cuyo SHA ya se ancla en la hash-chain.
  *Impacto*: sellado best-effort con reconciliación automática, igual que el índice.

- `feat(archive)` + `feat(signature)`: **Reconciliación del sellado del índice electrónico al cierre — cierra un modo de fallo permanente (E15/E06 Inc.8)**
  El sellado best-effort post-commit podía dejar un índice sin firmar indefinidamente si fallaba una vez; se añade intento síncrono acotado + job de reconciliación con backoff + visibilidad operacional directa.

- `feat(signature)`: **OCSP stapled en la firma XAdES-LT del índice (E06 Inc.7, ADR-016 addendum)**
  Añade evidencia de revocación OCSP (RFC 6960) como hermano del CRL embebido, cubierta por el mismo `ArchiveTimeStamp` del perfil LTA; opt-in con kill-switch.
  *Impacto*: enriquece la evidencia de revocación, no sube la acreditación (sigue sin ONAC).

- `feat(document)` + `fix(frontend)`: **El panel de Anexos del workspace de expediente ya muestra datos (RF-SEG-08)**
  Estaba silenciosamente vacío porque asumía un campo `anexos` en `expediente.radicados[]` que nunca existió; nuevo endpoint gateado por clearance en document-service (el dueño real de los anexos) + carga perezosa por radicado en el front.

- `feat(mcp)` + `feat(gateway)`: **El asistente E18 gana la tool `buscar_conocimiento` — búsqueda semántica as-the-user con ACL (ADR-019/ADR-006)**
  Conecta el knowledge-service auto-poblado al asistente; el clearance lo impone el knowledge-service server-side desde el `X-User-Id` del token validado, no el modelo.
  *Impacto*: los resultados se marcan como pistas de un índice derivado/advisorio, a confirmar contra la fuente autoritativa.

- `feat(knowledge)` + `feat(document)`: **Purga-en-anulación del índice semántico (E21) — un radicado anulado deja de ser consultable**
  Un radicado anulado seguía apareciendo en la búsqueda semántica; se marca ahora `voided` de forma permanente (purgando embedding/texto), y ninguna ruta de reindexado puede des-voidearlo.

- `feat(knowledge)` + `feat(document)`: **Ingesta event-driven del índice semántico (E21, MVP) — auto-población con ACL fail-closed (ADR-006/ADR-010/ADR-021)**
  El knowledge-service se poblaba solo por `POST /ingest` manual; ahora consume eventos de radicación/reclasificación con ACL fail-closed (nivel ausente → clasificada) y purga el contenido si un radicado se reclasifica al alza con proveedor externo.
  *Impacto*: invariante de monotonía bajo entrega at-least-once en cualquier orden, cerrado tras dos rondas adversariales que hallaron fugas reales.

- `feat(workflow)`: **Traza de auditoría de las lecturas de la bandeja de trámite y hoja de ruta (RF-BUS-10)**
  Última pieza del barrido RF-BUS-10 (tras archive y document): traza agregada de bandeja y traza por-radicado de hoja de ruta/trámite puntual.

- `feat(document)`: **Traza de auditoría de las lecturas de radicados (RF-BUS-10)**
  document-service aplicaba no-read-up pero no auditaba ninguna lectura; se añade traza individual y agregada (nunca por fila en listados/búsquedas), con minimización de PII del texto de búsqueda.

- `feat(mcp)` + `feat(gateway)`: **Asistente conversacional E18 — backend end-to-end (loop LLM Claude as-the-user, v1 solo-lectura) (ADR-019/ADR-020)**
  El slice de frontend existía pero no había backend: nuevo `POST /assistant/message` que orquesta un loop sobre Claude, re-propagando el token del usuario a cada tool (as-the-user, nunca con la API key del servicio).
  *Impacto*: v1 excluye toda escritura por doble defensa; los 403/404 de las tools colapsan a un genérico antes de llegar al modelo (no-oráculo).

- `feat(frontend)`: **Form Builder en la creación de expediente — campos dinámicos desde el `json_schema` de la plantilla de metadatos de la serie TRD (E22/E03/ADR-007)**
  La creación de expediente enviaba `metadata: {}` vacío; ahora el campo Serie TRD pasa a `<select>` y sus propiedades se renderizan dinámicamente vía el nuevo `SchemaField`/`schemaField.js` compartido.

- `feat(frontend)`: **Slice del asistente conversacional (E22/ADR-020) — UI de chat lista contra un contrato definido, backend E18 pendiente**
  UI completa y honesta sobre no ser funcional end-to-end sin backend; el markdown de salida se sanea server-side antes de cruzar al cliente (XSS del contenido no confiable del asistente).

- `feat(archive)`: **Origen/destino obligatorio + cotejado contra la ubicación real, condicionado a la tenencia física (E12/E17)**
  Cierra el residual de H-L/H-M: con tenencia física localizada, origen/destino pasan a obligatorios y el origen debe cotejar contra la ubicación actual del expediente; sin tenencia localizada (electrónico), se mantiene el comportamiento previo.

- `feat(archive)`: **Integridad de la creación de transferencias — unicidad de transferencia activa (H-L) + coherencia origen/destino por tipo (H-M) (E12)**
  Índice único parcial impide más de una transferencia activa por expediente; se valida además que origen/destino sean coherentes con el tipo de transferencia (no saltarse el archivo central).

- `feat(archive)`: **Inmutabilidad forzada por el motor del acta de entrega + tamper-evidence anclada en la hash-chain (E12)**
  La inmutabilidad del acta era solo procedimental; se instalan triggers append-only (rechazan UPDATE/DELETE/TRUNCATE) y se ancla su SHA-256 en el asiento hash-encadenado de `audit_log`.

- `feat(archive)`: **Congelar el FUID como acta de entrega inmutable al recibir la transferencia (E12 H-I)**
  Al recibir, se congela dentro de la misma transacción una acta probatoria (XML canónico + fixity + los tres responsables), en vez de regenerar el FUID al vuelo cada vez.

- `feat(frontend)`: **Vista de listado de expedientes (E22 T-12) — reemplaza el único listado stub del frontend**
  Última vista de dominio sin implementar; SSR puro sobre `GET /expedientes/search` (defensa en profundidad de no-read-up incluso si el fix de backend se regresionara).

- `fix(archive)`: **No-read-up en el listado y el PATCH de expedientes (RF-SEG-08)**
  `GET /expedientes` no filtraba por clearance ni recibía `X-User-Id`, a diferencia de sus gemelos; se elimina además el default peligroso `max_clearance=3` (sin restricción) por un parámetro keyword-only obligatorio.

- `feat(archive)`: **Traza agregada de consulta de expedientes (RF-BUS-10)**
  Tras cerrar el leak de clearance, el listado/búsqueda no dejaba ninguna traza; se añade una entrada agregada por consulta (nunca por fila), sin registrar nunca el texto crudo de búsqueda.

- `test(frontend)` + `fix(frontend)`: **Cobertura de tests de UI + correcciones a11y/SSR en componentes overlay (E22)**
  El frontend tenía 1 test unitario y 0 E2E; se monta infra completa (Vitest+Playwright) que destapa y corrige bugs reales de SSR (guard D-16) y de apilamiento de overlays en componentes compartidos por las 13 vistas.

- `feat(storage)`: **Validación PDF/A en la ingesta del AIP — contrato + stub honesto (E10 Preservación, Increment C2a)**
  Nuevo `PdfaValidator` pluggable con stub honesto (siempre `not_evaluated`, nunca finge un veredicto); advisory, no bloquea el AIP.

- `feat(storage)`: **Réplica best-effort del AIP a un segundo bucket WORM (E10 Preservación, Increment C1b)**
  OAIS exige múltiples copias para durabilidad; réplica local o cross-site, best-effort no-fatal (nunca revierte el AIP primario sellado).

- `fix(storage)`: **PREMIS v3 conformidad plena — el AIP valida contra el `premis.xsd` oficial de la Library of Congress (E10 Increment C1a)**
  Antes validaba contra un XSD escrito por el propio proyecto, lo que no probaba conformidad real; se vendoriza el XSD normativo.

- `feat(storage)`: **AIP real (BagIt RFC 8493) + PREMIS v3 subido al bucket WORM (E10 Preservación, Increment B)**
  El AIP era un stub textual en BD; se arma ahora un Bag RFC 8493 real con manifiestos, fixity recalculada sobre bytes reales, y se sube al bucket WORM bajo el gate de disposición.

- `feat(storage)`: **WORM / Object-Lock real (MinIO) sobre el índice electrónico firmado + gate de disposición (E10 Preservación, Increment A, ADR-023)**
  Primer incremento de preservación: bucket WORM por tenant, retención derivada de la TRD, y gate que bloquea el borrado mientras el índice esté bajo retención o legal hold.

- `feat(auth,signature)`: **2FA por TOTP (RFC 6238) en la firma personal — secreto en auth-service, elevando el OTP-correo (E06 Inc.6, ADR-016 addendum)**
  Añade posesión de dispositivo como factor, más fuerte que el OTP-correo; secreto cifrado AESGCM con AAD atado a tenant+usuario.

- `feat(signature)`: **XAdES-LT/LTA del índice con CA local de desarrollo — material de validación a largo plazo, no acreditado (E06 Inc.5, ADR-016 addendum)**
  La firma llegaba a XAdES-T sin material de validación (cadena + revocación); escala a LTA cuando el sello del tenant trae material de CA, con `ca.key` fuera de todo contenedor.

- `feat(archive)`: **Índice electrónico v4 "conforme completo" — dependencia productora + fecha de declaración + pista de auditoría + política de acceso (E15, ADR-022 addendum)**
  Cierra los 4 residuales que dejaba v3 "conforme extendido" sin sobre-declarar (RF-FIR-15).

- `feat(archive)`: **Índice electrónico v3 "conforme extendido" — orden documental estable + exclusión trazable + ACL + contexto (E15, ADR-022 addendum)**
  El `<orden>` recalculado en cada exclusión violaba el principio de orden original; pasa a ordinal estable, y la exclusión pasa de DELETE físico a soft-delete trazable.

- `feat(signature)`: **XAdES-T — sello de tiempo RFC 3161 del índice electrónico (E06 Inc. 4, ADR-016 addendum)**
  Añade trazabilidad temporal verificable (TSA local in-process) al XAdES-B previo, cuyo `SigningTime` del reloj del servidor no era oponible por sí solo.

- `feat(archive)`: **Metadatos RT-15 del índice electrónico — perfil v2 (E15/E06 Inc. 3, ADR-022)**
  El Acuerdo AGN 001/2024 art. 4.3.2.3 exige formato, tamaño y foliación por documento, que el índice v1 omitía; nuevo perfil v2 con metadatos opcionales que se omiten (no se falsean) cuando no se declaran.

### Security

- `fix(shared,auth,archive,document,workflow,signature,notification)`: **Auditoría de accesos denegados — el gate RBAC deja traza en `audit_log` al denegar**
  `require_permission` levantaba el 403 antes de que ningún handler pudiera auditar — un intento de escalada de privilegios era invisible. Nuevo helper compartido `append_denial` (best-effort, nunca convierte una denegación en 500), aplicado en los 6 servicios.

- `fix(archive)`: **Mínimo privilegio en el gate RBAC — umbral `min_crud` por operación; disposición exige `crud>=3` (RF-SEG-03)**
  `require_permission` concedía con cualquier `crud>0`, sin distinguir lectura de disposición; se añade el parámetro `min_crud` (retrocompatible por default) y se eleva a 3 en cierre/transferencia/eliminación de tipos/retención TRD.
  *Impacto*: cierra además una ruta alterna — el PATCH que transiciona a `closed`/`transferred` exige ahora el mismo umbral que sus rutas dedicadas.

- `fix(archive)`: **Cierre del residual RBAC en la config archivística + trazabilidad — `USUA_PERM_TRD` y `audit_log` en las mutaciones de TRD/tipos/plantillas**
  Las mutaciones de TRD/tipos documentales/plantillas no aplicaban ningún gate, pese a que el permiso ya estaba sembrado; se aplica y se añade auditoría de las 6 mutaciones.

- `fix(archive)`: **Cierre del gap RBAC en la gestión de expedientes — `require_permission` en `expedientes.py` + `batch.py`**
  Los 12 endpoints de expedientes no aplicaban ningún gate de permiso; la auditoría además descubrió un bug de aislamiento cross-tenant en la tarea detached de creación por batch (reutilizaba una conexión que el pool podía reasignar a otro tenant).
  *Impacto*: nueva `acquire_tenant(tenant_slug)` con conexión propia para tareas detached.

- `fix(notification,signature)`: **Cierre de la fuga del OTP — notification-service no persiste el cuerpo de correos sensibles (RF-FIR-13)**
  El OTP viajaba en el cuerpo del correo y notification-service persistía el body completo en BD, dejándolo en claro durante su ventana de validez.
  *Impacto*: flag `sensible=True` separa enviar de persistir — el cuerpo real se envía por SMTP pero se redacta en la fila guardada.

### Added

- `feat(signature)`: **2FA por OTP-correo en la firma personal (E06 Inc. 2, RF-FIR-13, ADR-016 addendum)**
  Segundo factor de un solo uso por correo, ligado a `(solicitud_id, firmante_id, hash_documento)`; solo se persiste el HMAC del OTP, nunca el valor en claro.
  *Impacto*: **breaking** — `POST /batch` pasa a requerir `{solicitud_id, challenge_id, otp}` por ítem. No desactivable para radicados reservados/clasificados.

- `feat(signature,archive)`: **Firma XAdES-B real del índice electrónico + sello institucional (E06 Inc. 1, ADR-016 addendum)**
  Proveedor de firma enchufable con implementación XAdES-B real (`signxml`), activada para el índice al cierre del expediente con el certificado institucional del tenant.
  *Impacto*: si el sellado falla, el cierre no se bloquea — el índice queda `pendiente_firma` y bloquea solo la transferencia, no el cierre.

- `feat(signature,workflow,notification)`: **Pulido de 3 seguimientos Baja de la gestión del dead-letter (ADR-021)**
  Cap señalizado de candidatas (header `X-Truncated`), resolución de nivel en lote para el listado admin, y contrato de identidad unificado entre los tres servicios.

- `feat(signature)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en signature-service**
  Filtro de clearance en el listado admin del dead-letter, auditoría de vista (`evento.dead_letter.viewed`), y `origin_group` pasa a `NOT NULL` con índice compuesto.

- `feat(workflow)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en workflow-service**
  Mismo diseño que signature: filtro de clearance en el listado, auditoría de vista, `origin_group NOT NULL` + índice compuesto sobre la tabla física compartida.

- `feat(notification)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en notification-service**
  Mismo diseño que signature/workflow, aplicado a la variante de notification.

- `feat(notification)`: **Replay/gestión del dead-letter + purga selectiva (Fase 5 Inc. 2 + Fase 4 MVP-3 de notification, ADR-021 — cierra el roadmap de eventos)**
  notification estrena la superficie admin (listar/detalle/replay/discard) que signature y workflow ya tenían; el replay es best-effort por tratarse de un efecto externo no transaccional (envío de correo).
  *Impacto*: cierra el roadmap ADR-021 en los tres servicios consumidores.

- `feat(signature,workflow)`: **Purga selectiva de `evento_dead_letter` (Fase 4 MVP-3, ADR-021)**
  Job que purga solo estados terminales (`resolved`/`discarded`) con gracia de 90 días; `pending` nunca se purga.

- `feat(signature,workflow,auth)`: **Gestión/replay del dead-letter — endpoints admin (Fase 5 Inc. 1, ADR-021)**
  Nueva superficie admin gateada por `PERM_DLQ_ADMIN`, con replay atómico (`SELECT FOR UPDATE` + reproceso idempotente + transición anti-TOCTOU) y no-read-up sobre el nivel vivo del radicado referenciado.

- `feat(events)`: **Retención del buffer DLQ Redis vía `XTRIM` (Fase 4 MVP-2, ADR-021)**
  Los streams dead-letter crecían sin tope; se recortan por edad (no por `MAXLEN` ciego) una vez confirmada la persistencia en Postgres.

- `feat(notification)`: **Retención del cache de deduplicación (Fase 4 MVP-1, ADR-021)**
  Job que purga los marcadores de idempotencia vencidos (TTL 7 días, margen amplio sobre la ventana real de redelivery).

- `feat(notification)`: **Dead-letter durable + idempotencia de eventos (Fase 3, ADR-021 — completa el roadmap de eventos)**
  notification-service no es idempotente por naturaleza (efectos externos); se resuelve primero la deduplicación (insert-then-send) antes de aplicar el mismo dead-letter durable de signature/workflow.

- `feat(workflow)`: **Recuperación de pendientes + dead-letter durable de eventos (Fase 2, ADR-021)**
  Extiende el patrón de signature a workflow-service, que consume `radicado.created` para auto-distribuir — el camino archivísticamente más sensible.

- `feat(events,signature)`: **Dead-letter durable y atómico — cierre de R-4 (ADR-021)**
  La traza del dead-letter era best-effort tras el `xack`; ahora se persiste atómicamente en Postgres (evento + asiento en una sola transacción), con el `xack` gateado por el éxito de esa escritura.

- `feat(events,signature)`: **Recuperación de pendientes + dead-letter de eventos (Fase 1, ADR-021)**
  Nuevo helper `reclaim_stale` sobre `XPENDING`+`XCLAIM` que recupera mensajes atascados en el PEL y, tras agotar los reintentos, los deriva a un dead-letter stream en vez de descartarlos en silencio.

- `feat(frontend)`: **UI de reclasificación del nivel de seguridad en la bandeja**
  El backend de reclasificación (E08) existía sin superficie de UI; nuevo chip de nivel + modal de reclasificación con motivo obligatorio, gateado por `PERM_RECLASIFICAR`.

- `feat(document)`: **`nivel_seguridad` expuesto en el read path de radicados**
  La columna existía en BD pero no se incluía en ningún SELECT/`DocumentResponse`, bloqueando la UI de reclasificación.

- `feat(document,signature,auth)`: **Reclasificación del nivel de seguridad de un radicado (E08) + re-sync del snapshot de firma**
  Nuevo `PATCH /documents/{id}/security-level` con motivo obligatorio y no-read-up/no-write-up; signature-service re-sincroniza el snapshot de nivel en sus cadenas de firma para no quedar desactualizado.

### Fixed

- `fix(signature,workflow)`: **Scoping por `origin_group` de la tabla compartida `evento_dead_letter`**
  Ninguna query filtraba por `origin_group` en la tabla física compartida entre servicios — la purga de uno podía borrar dead-letters del otro, y un replay podía invocar el handler equivocado.

- `fix(workflow,notification)`: **Los consumidores de eventos ya no tratan la expiración normal de `xreadgroup(block=...)` como error**
  El `TimeoutError` normal de redis-py al expirar el `block` sin entradas caía en el `except Exception` general, generando backoff y ruido de log innecesarios.

- `fix(signature)`: **Consumidor de re-sync ya no loguea la expiración normal de `xreadgroup(block=...)` como error**
  Mismo patrón que el fix de workflow/notification, descubierto primero en signature-service (cientos de líneas de log/minuto en reposo).

- `fix(frontend)`: **D-16 — SSR 500 en todas las rutas `(app)` por `onDestroy` que toca `document`**
  `onDestroy` también se ejecuta durante el render SSR, donde `document` no existe; varios componentes registraban ahí la limpieza de listeners, tumbando toda ruta del layout privado.
  *Impacto*: la limpieza pasa al retorno de `onMount`, patrón idiomático de SvelteKit SSR.

- `fix(auth)`: **D-07 — espejo RBAC de la UI roto**
  `GET /auth/me` no devolvía `permissions`, así que la UI mostraba todos los controles desactivados para todo el mundo.

### Security

- `feat(signature)`: **No-read-up por clearance en la cadena de firma (RF-SEG-08, cierra D-15 #6)**
  La cadena solo verificaba `PERM_FIRMA`, no el nivel de clasificación del objeto; se añade un snapshot de `nivel_seguridad` capturado al crear y un gate 404 neutro en toda la superficie de la cadena.

- `fix(auth,signature,gateway)`: **D-15 — el RBAC fino nunca funcionó E2E (identidad `keycloak_sub` vs `auth_users.id`)**
  El gateway inyectaba el `sub` de Keycloak como `X-User-Id`, pero los servicios de dominio matchean el UUID interno — 403 fail-closed para todo usuario real. Descubierto en el primer smoke test E2E con Keycloak vivo; los unit tests lo ocultaban por completo.
  *Impacto*: `auth-service /validate` resuelve `keycloak_sub → auth_users.id` con lazy provisioning; signature-service migra a RBAC DB-based.

- `fix(document)`: **D-14 — envíos postales sin compuerta de permiso y con read-up en el listado**
  Los 4 endpoints de envíos postales no exigían permiso, y el nuevo listado transversal exponía destinatario/dirección/guía de envíos clasificados sin filtro de clearance.

- `fix(document)`: **D-11 / D-12 — radicación y borradores sin compuerta de permiso**
  El router de borradores/plantillas y la radicación directa (`POST /documents`) no exigían ningún permiso — cualquier autenticado del tenant podía radicar oficialmente.

- `fix(document)`: **D-13 — mutación de radicado y disposición sin compuerta de permiso**
  Hallazgo derivado de cerrar D-12: `PATCH /documents/{id}` y el endpoint de disposición tampoco exigían permiso.

- `fix(workflow,archive,document)`: **Hardening del `tenant_slug` y traza forense de cadenas de VoBo**
  Reservas de defensa en profundidad de D-06/D-08/D-09: helper `tenant_schema()` fail-closed contra inyección de schema en los 10 sinks que interpolaban el slug crudo, y auditoría atómica de la creación de cadenas de visto bueno.

- `feat(auth)`: **D-10 — endpoint de selección de usuarios para la UI sin exigir admin**
  El `UserPicker` consumía el endpoint admin-only de usuarios; nuevo `GET /auth/users/pickable` con proyección mínima (`id`, `username`) gateado por `PERM_RADI`.

- `fix(workflow)`: **D-09 — hueco de autorización al crear cadenas de visto bueno**
  `crear_cadena` solo exigía tenant; cualquier autenticado podía conformar una cadena de revisores sobre cualquier radicado.

- `fix(archive)`: **D-08 — hueco de autorización en archivo físico y transferencias**
  Todos los endpoints de `fisico.py`/`transferencias.py` solo exigían tenant; se aplica `USUA_PERM_EXPEDIENTE` en lectura y escritura.

- `fix(document,workflow)`: **D-06 — hueco de autorización en acciones de operador**
  4 endpoints de escritura (anulación, respuesta vinculada, devolver, decisión de visto bueno) solo exigían presencia de `X-User-Id`, sin verificar rol ni tenencia.

- `fix(frontend)`: **D-05 — el `access_token` deja de viajar al cliente**
  El token se serializaba al navegador, exponiéndolo a XSS/exfiltración; 9 endpoints proxy SSR mueven la llamada al gateway al servidor.

- `feat(auth,signature)`: **Guard fail-fast del contrato de issuer**
  Una mala configuración del issuer (host interno de Docker) rechazaba todos los tokens con 401 genéricos, sin ninguna pista; se añade un WARNING explícito al arranque.

- `refactor(auth)`: **Migración `python-jose` → `PyJWT[crypto]`**
  `python-jose` arrastraba CVEs de confusión de algoritmo y DoS; PyJWT es el estándar activo mantenido.

- `fix(auth)`: **Validación del emisor (`iss`) en los JWT**
  Se verificaba firma y expiración pero no el issuer, permitiendo aceptar un token firmado por el mismo Keycloak para otro realm/cliente.

- `fix(deps)`: **Elevados suelos de versión con CVE conocido**
  `python-multipart` a `>=0.0.18` (CVE-2024-53981 DoS); `mcp` a `>=1.12.0` (alinear con la API actual del SDK).

- `chore(build)`: **Dependencias congeladas con lockfiles**
  Los `requirements.txt` usaban solo `>=` sin lock, así que builds en fechas distintas resolvían versiones distintas (cadena de suministro no reproducible); nuevo `requirements.lock` por servicio.

### Changed

- `refactor(signature)`: **Eliminado el dead code de validación JWT tras el fix D-15**
  signature-service ya no valida el JWT (confía en las cabeceras del gateway); se elimina `core/security.py` y la config de Keycloak/issuer asociada.

- `refactor(rebrand)`: **Renombrado completo `orfeomcp` → OrpycaMCP / orpycamcp antes de la v0.01**
  *Por qué*: el proyecto es la evolución de Orfeo bajo el concepto Orpyca ("Orfeo Pyme Calidad") con conexión a IA vía MCP.
  *Impacto*: marca `OrpycaMCP`, token de datos/infra `orpycamcp` (BD, realm, buckets, streams, paquete Python, red, contenedores). **Requiere recrear volúmenes** — los datos de desarrollo previos no son compatibles.

- `docs(claude)`: `CLAUDE.md` sincronizado con la arquitectura real — 11 microservicios con puertos correctos, referencias a SQLAlchemy/Alembic eliminadas (contradecían ADR-003).

- `chore(ci)`: CI unificada en GitLab — eliminado `.github/workflows/` duplicado; README/badges apuntan al repo real.

- `fix(workflow)`: hoja de ruta del radicado acotada por clearance (E05 §10 / RF-SEG-08) — `GET /workflows/{id}/events` deja de ser legible por cualquiera del tenant.

- `feat(archive)`: hoja de ruta del expediente (E02 §9 / RF-EXP-09) — tabla `expediente_eventos` append-only e inmutable, acotada por clearance.

- `feat(audit)`: cobertura de `public.audit_log` (E08 §9, Canal A) — workflow-service y archive-service emiten ahora también a la auditoría forense transversal (antes solo document + auth).

- `chore(compose)`: puertos publicados de desarrollo movidos a un rango alto (19xxx/15432/16379) para no chocar con otras pilas de dev; los puertos internos de los contenedores no cambian.

- `fix(compose)`: `signature-service` y `knowledge-service` esperan ahora a `postgres` *healthy* — antes arrancaban con `ConnectionRefused` contra una BD aún no lista.

### Added

- `feat(frontend)`: **T-12d-firma — bandeja del firmante (UI de la cadena E06)**
  Nueva vista `(app)/firmas/` con bandeja cross-documento, drawer de cadena, firma/rechazo con motivo obligatorio y firma en lote; cierra el ciclo E06 de extremo a extremo.

- `feat(signature)`: **E06 — cadena de firma (bandeja del firmante)**
  Nuevo modelo de cadena con turnos ordenados (`firma_solicitud`+`firma_cadena`), 7 endpoints gateados por `PERM_FIRMA`, con turno respetado bajo `SELECT FOR UPDATE` para cerrar el TOCTOU de doble firma.

- `feat(infra)`: **Frontend en su propio contenedor**
  Split de URLs público/interno y pinning de issuer de Keycloak para que el PKCE funcione correctamente detrás del proxy en dev y prod.

- `feat(frontend)`: **E22 Fase 1 (plantilla base)**
  Arranca la app SvelteKit: sistema de diseño por tokens, cliente del gateway, login OAuth2 PKCE server-side con tokens en cookies httpOnly, layout con navegación filtrada por RBAC, y biblioteca de 11 componentes accesibles.

- `feat(frontend)`: **E22 Fase 2 — vistas núcleo consolidadas**
  Dashboard, bandeja unificada de radicados, radicación guiada en stepper de 5 pasos, y workspace de expediente con 5 facetas — resuelve los flujos clave del operador en una sola vista.

- `feat(frontend)`: **T-12b — editor de texto enriquecido (TipTap) para el cuerpo de Salida/Interno**
  Reemplaza el textarea plano; el HTML se sanea tanto al escribir como al renderizar con un sanitizador propio de allowlist estricta.

- `feat(frontend)`: **E22 Fase 3 — acciones de operador y gestión del ciclo de vida**
  Acciones avanzadas de bandeja (anular/devolver/responder/VoBo), cadena de visto bueno con `UserPicker`, búsqueda avanzada, y vistas de archivo físico y transferencias con FUID.

- `feat(frontend)`: **T-12c — gestor de borradores y plantillas de contenido**
  UI para borradores de Salida (redacción previa a radicar) y plantillas de contenido reutilizables, todo vía proxies SSR con el HTML saneado al escribir y al renderizar.

- `feat(frontend)`: **T-12e — bandeja de envíos postales y planilla de despacho**
  Bandeja transversal de envíos con historial de eventos, devolución con causal, y planilla de despacho exportable a CSV con neutralización de inyección de fórmulas.

> **Plan specDrive — conformidad SGDEA (F1–F6, 21 épicas).** El detalle granular por épica vive en [`docs/es/changelog.md`](docs/es/changelog.md). Resumen consolidado (núcleo implementado, ~295 tests verdes en 10 microservicios, 20 ADRs, migraciones validadas en PostgreSQL/pgvector real):
> - **F1 Fundamentos**: librería común `orpycamcp_common` (ADR-010), migraciones por tenant (ADR-012), administración (E14), seguridad RBAC + auditoría inmutable + clasificación (E08), radicación (E01), metadatos JSONB + plantillas (E03), anexos (E07), notificaciones (E16), flujos + reglas + transacciones (E05).
> - **F2 Consulta**: búsqueda avanzada + reportes + búsqueda de expedientes (E09, ADR-014 PostgreSQL FTS), consulta pública por código de verificación (E13), ingesta de correo IMAP (E19).
> - **F3 Ciclo de vida**: TRD/CCD jerárquico + retención en dos fases + disposición AGN (E04, ADR-015), ciclo de vida del expediente cierre/transferencia/foliado (E02), firma electrónica (E06, ADR-016).
> - **F4 Conformidad**: índice electrónico XML versionado append-only + huella por documento + verificación (E15).
> - **F5 Preservación/físico**: archivo físico + FUID (E17, ADR-017), transferencias primarias/secundarias (E12), interoperabilidad por webhooks firmados (E11, ADR-018), plan de preservación + eventos PREMIS (E10).
> - **F6 Inteligencia**: mcp-server — capa MCP cliente del gateway (E18, ADR-019); knowledge-service — pgvector + recuperación semántica con pre-filtrado ACL (E21, ADR-006).

- `feat(auth)`: auth-service — autenticación Keycloak ROPC, validación JWT con JWKS cacheado, audit log en PostgreSQL, endpoints `/token`, `/refresh`, `/logout`, `/validate`, `/me`
  *Por qué*: dependencia fundacional de todos los servicios; sin auth no hay trazabilidad legal.
  *Impacto*: cualquier servicio puede validar un JWT llamando a `/validate` sin lógica propia.

- `feat(tenant)`: tenant-service — CRUD de instituciones con provisioning automático de schema PostgreSQL `tenant_{slug}`
  *Por qué*: el multi-tenancy es un requisito fundacional de Orfeo; cada institución debe tener aislamiento completo de datos.
  *Impacto*: agregar una nueva institución crea automáticamente su schema y la deja lista para operar.

- `feat(gateway)`: api-gateway — proxy HTTP centralizado con validación JWT, rate limiting por IP (Redis), inyección de headers de identidad
  *Por qué*: los servicios internos no deben conocer Keycloak; el gateway resuelve auth una sola vez.
  *Impacto*: los clientes tienen un único punto de entrada; los servicios reciben identidad ya validada.

- `feat(document)`: document-service — radicación de documentos E/S/I con numeración atómica, gestión de anexos como referencias UUID a storage-service
  *Por qué*: la radicación es el core legal del sistema; el número es inmutable y requerido por Ley 594/2000.
  *Impacto*: cualquier comunicación oficial puede registrarse con trazabilidad completa.

- `feat(storage)`: storage-service — upload a MinIO con deduplicación por SHA-256, buckets por tenant, URLs pre-firmadas para descarga directa
  *Por qué*: separar el almacenamiento de archivos del registro documental permite escalar independientemente.
  *Impacto*: el mismo archivo no se almacena dos veces en el mismo tenant.

- `feat(workflow)`: workflow-service — flujos de distribución de radicados entre dependencias, historial de pasos, eventos en Redis Stream
  *Por qué*: el flujo de distribución es la segunda función más crítica de Orfeo tras la radicación.
  *Impacto*: cada movimiento de un radicado queda registrado con responsable y timestamp.

- `feat(archive)`: archive-service — expedientes (`open→closed→transferred`) con vinculación de radicados, TRD con series documentales y tiempos de retención
  *Por qué*: la Ley 594/2000 exige organizar los documentos en expedientes y aplicar la TRD.
  *Impacto*: las instituciones pueden cumplir con los requisitos de archivo y disposición documental.

- `feat(notification)`: notification-service — consumer de Redis Streams, envío de emails SMTP, historial en Redis con TTL 30 días
  *Por qué*: las dependencias deben ser notificadas cuando reciben un radicado sin polling.
  *Impacto*: el workflow publica un evento y la notificación llega sin acoplamiento directo.

- `refactor(all)`: retirar SQLAlchemy y Alembic de todo el proyecto — usar asyncpg directo y SQL crudo con migraciones en archivos `.sql`
  *Por qué*: SQLAlchemy agregaba complejidad innecesaria; asyncpg directo es más simple y explícito.
  *Impacto*: menos dependencias, SQL visible y auditable, migraciones como archivos SQL simples.

### Fase 4 — Calidad e Integración (2026-06-04)

- `infra(lint)`: pyproject.toml con configuración centralizada de ruff y mypy — todos los 8 servicios pasan linting
  *Por qué*: la calidad de código es requisito para pasar a producción.
  *Impacto*: cada commit se verifica automáticamente contra imports no usados, type hints y violaciones de estilo.

- `infra(health)`: health check orquestado en api-gateway — `GET /health` verifica Redis + todos los servicios en paralelo
  *Por qué*: saber el estado global del sistema sin hacer llamadas separadas a cada servicio.
  *Impacto*: los clientes y orquestadores ven `degraded` (503) si un servicio crítico falla.

- `test(e2e)`: suite de integración end-to-end en docker-compose.e2e.yml — tests de flujo completo
  *Por qué*: los tests unitarios no capturan interacciones entre servicios.
  *Impacto*: detectamos antes de producción si un servicio rompió a sus clientes.

- `docs(adr)`: ADR-003 documenta la decisión de retirar SQLAlchemy y usar asyncpg directo
  *Por qué*: que futuros contribuidores entiendan por qué el proyecto no usa ORMs.
  *Impacto*: claridad arquitectónica y decisión documentada para auditoría.

---

## [0.1.0] — 2026-06-03 (scaffold inicial)

### Added

- `feat(infra)`: estructura base del proyecto con 8 microservicios FastAPI
- `feat(infra)`: docker-compose.yml con PostgreSQL 15, MinIO, Keycloak 24, Redis 7 y MailHog
- `feat(infra)`: realm Keycloak `orpyca` con roles RBAC y usuarios de desarrollo
- `feat(infra)`: init-db.sql con schema isolation por tenant
- `feat(ci)`: pipeline GitLab CI/CD con validación, revisión Claude, documentación automática y GitLab Pages
- `feat(docs)`: MkDocs Material con documentación bilingüe es/en
- `feat(adr)`: ADR-001 arquitectura microservicios con FastAPI
