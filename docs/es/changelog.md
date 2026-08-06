# Changelog

Todos los cambios notables se documentan aquí.

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.0.0/)

---

## [Unreleased]

### Añadido

- `feat(archive)`: **Los expedientes de fondo acumulado ya pueden gestionarse, con su calendario calculado desde la fecha correcta (ADR-026, segundo incremento)**
  El incremento anterior permitía **valorar** un fondo acumulado —documentación antigua, típica de entidades liquidadas— pero no **gestionarlo**: ningún expediente podía clasificarse bajo esa tabla de valoración. Esto lo habilita, y la parte delicada no es el permiso sino **desde qué fecha se cuenta la retención**.
  El sistema calculaba los plazos desde la fecha de cierre del expediente. Para documentación corriente es correcto: un expediente se cierra cuando deja de producirse. Para un fondo acumulado sería **gravísimo**: un expediente cuyos documentos son de 1985, registrado hoy, vería su plazo de archivo central terminar en 2046 — y esa fecha es la que se graba como protección de borrado irreversible en el almacenamiento. Documentación cuya conservación se agotó hace décadas quedaría **imposible de eliminar durante veinte años más**, sin marcha atrás posible, porque ese tipo de protección no admite acortarse.
  Ahora la fecha base es siempre explícita y debe declararse en cada cálculo: para un fondo acumulado es la fecha del documento más reciente que contiene, nunca la de registro. Quién fue esa fecha y por qué se eligió queda **congelado junto al expediente**, para que dentro de veinte años sea comprobable y no haya que reconstruirlo. Y el caso de un plazo **ya vencido** —lo normal en un fondo acumulado— se muestra tal cual, sin empujarlo hacia adelante: el expediente queda listo para su disposición final desde el día en que se registra.
  Se cerró además una vía que nadie había pedido revisar: al permitir cerrar estos expedientes, tres procesos de preservación habrían calculado esa misma protección de borrado desde la fecha equivocada. Quedan bloqueados para fondo acumulado hasta que el siguiente incremento los habilite correctamente.
  *Por qué*: sin esta pieza, una entidad con fondo acumulado podía registrar su instrumento de valoración y aun así no poder archivar nada bajo él.
  *Impacto*: 721 pruebas unitarias y 168 de integración contra base de datos real, incluida una obligatoria con fechas de los años 80 cuyo resultado **solo puede ser correcto si se usó la fecha debida** — con la equivocada el plazo caería siempre en el futuro y la prueba fallaría. Queda para más adelante el cableado real de la protección de borrado y la publicación previa del inventario de eliminación, que sigue pendiente de que se determine el artículo y el plazo legal exactos.

- `fix(document)`: **auditoría de la creación masiva de radicados + reactivación de la suite de pruebas de carga masiva**
  La creación de radicados en lote (`POST /api/v1/batch/documents`) no dejaba ningún rastro en la auditoría inmutable: se podían crear cientos de documentos oficiales, con número de radicado irrepetible, sin que quedara constancia de quién lo hizo ni cuándo. Se detectó porque la misma operación en lote sobre expedientes, en otro servicio, sí lo hacía desde el principio — dos funciones gemelas, solo una dejaba rastro. Se corrige dejando un asiento de auditoría por cada radicado creado (no uno por lote completo, para no perder la trazabilidad individual), atribuido siempre a la persona que encargó el lote, y de forma que un lote parcialmente exitoso (por ejemplo 480 de 500) deje constancia exacta de lo que sí entró y lo que no, nunca de la intención original.
  Además, la batería de pruebas automáticas de esta función llevaba deshabilitada por completo desde su creación (hacía referencia a una pieza de pruebas que nunca llegó a existir). Se reescribió por completo, cubriendo también el permiso de acceso que se había añadido recientemente sin ninguna prueba que lo verificara.
  *Por qué*: crear documentos oficiales en masa sin dejar rastro de autoría es justo lo que la auditoría existe para impedir; una batería de pruebas apagada no protege nada, y menos un permiso de acceso recién añadido.
  *Impacto*: sin cambios de esquema (la tabla de auditoría ya existía). Suite de pruebas del servicio de documentos: 284 aprobadas, 20 pendientes (antes 273 aprobadas, 25 pendientes — las 20 restantes son pruebas de integración contra base de datos real, no relacionadas con este cambio).

- `feat(frontend)`: **`/admin/interoperabilidad` — exportar/importar radicados, carga masiva y rastreo postal (Fase 8, última del cierre del desfase API↔UI)**
  Cinco funciones del backend llevaban tiempo listas sin ninguna pantalla que las usara: exportar un paquete ZIP de radicados, importarlo de vuelta preservando el número original, crear documentos o expedientes en lote, y consultar el rastreo de un envío postal contra el operador. La nueva pantalla cubre las cuatro primeras en pestañas, y la última se añadió como botón "Actualizar rastreo" en la bandeja de envíos existente. La parte más delicada es la importación: es todo-o-nada (si un solo archivo del paquete no coincide con su huella digital declarada, no se importa nada) y cuando eso pasa, la pantalla muestra exactamente qué archivo falló y por qué, nunca un mensaje genérico de "no se pudo importar". La carga masiva, al ser una escritura en bloque sobre documentos oficiales, exige confirmar con el número exacto de elementos antes de ejecutar, y mientras se procesa consulta el progreso cada dos segundos distinguiendo siempre "no se pudo consultar el estado" de "todavía no hay resultados" (nunca se muestra un progreso de cero inventado). El rastreo postal, al no tener todavía una integración real con el operador, lo dice explícitamente en vez de aparentar una consulta en vivo.
  Al construirla se descubrió que la carga masiva, aunque completa en la pantalla, no podía funcionar: la puerta de enlace central del sistema no tenía registrada la ruta que usan esos dos endpoints. Eso quedó corregido (ver más abajo, en Corregido), pero al corregirlo la dirección de consulta de progreso cambió de forma y la pantalla se quedó con la antigua. Sumado a un defecto de refresco del panel de progreso, la carga masiva no informaba de su resultado en ninguna circunstancia. Lo detectó la revisión de experiencia de uso del 2 de agosto de 2026 y **quedó corregido** en la entrada de esa revisión, más abajo. Exportar, importar y rastreo postal sí funcionaron de punta a punta desde el principio.
  *Por qué*: cierre de la última fase pendiente del plan de poner en la interfaz todo lo que el backend ya expone.
  *Impacto*: 1 pantalla nueva con 4 secciones, 1 botón nuevo en envíos postales, 27 pruebas automáticas nuevas; exportar/importar/rastreo quedan operables de punta a punta, la carga masiva queda a la espera de un ajuste de ruteo fuera de esta entrega.

- `feat(archive)`: **TVD — Tabla de Valoración Documental de fondo acumulado (ADR-026, Increment 1: registro y convalidación, migración 039)**
  Una entidad con fondo acumulado (documentación ya producida, sin criterio archivístico, típica de dependencias suprimidas o entidades liquidadas) no tenía dónde registrar su instrumento de valoración: sin él, no podía cerrar ni disponer nada de ese fondo. La TVD entra como el **mismo instrumento que la TRD**, con el mismo circuito de convalidación (Comité → Consejo → RUSD → publicación) — un discriminador sobre la misma tabla, no una tabla nueva, para no bifurcar el punto único del que dependen siete escrituras irreversibles de retención WORM. Cuatro capas lo blindan: un código de serie solo puede pertenecer a un instrumento; las listas se leen siempre a través de una vista que ya filtra por tipo; el contenido propio de cada instrumento (fondo, fechas extremas, justificación de la valoración para la TVD) es obligatorio o prohibido según el tipo, verificado por la base de datos; y, lo más importante en este incremento, **ningún expediente puede todavía clasificarse bajo una TVD** — la base de datos lo impide directamente, así que el riesgo de retención irreversible es cero mientras esta pieza no exista. Rutas `/api/v1/tvd` espejo de las de TRD (crear, editar, versionar, aprobar, devolver, convalidar, registrar RUSD, derogar), mismo permiso que TRD. También se añadió la consulta de historial completo de versiones (`GET .../{code}/versiones`) tanto para TRD como para TVD — estaba prometida desde el incremento anterior y nunca se había construido.
  *Por qué*: una entidad con fondo acumulado no tenía ninguna forma de valorarlo dentro del sistema — hallazgo de la auditoría de conformidad archivística sobre el modelo de retención.
  *Impacto*: 1 migración de esquema, rutas nuevas bajo `/api/v1/tvd` + historial de versiones nuevo para TRD, sin romper el contrato existente; 20 tests unitarios + 13 de integración contra Postgres real nuevos, suite completa sin roturas (721 unitarios + 164 de integración, antes 701+151).

- `feat(frontend)`: **pantalla `/admin/tvd` + circuito de convalidación compartido con `/admin/trd` (ADR-026, frontend)**
  Pantalla nueva `/admin/tvd`: mismo patrón visual que `/admin/trd` (árbol de agrupaciones, alta, edición) pero con las columnas que le corresponden a una TVD -- fondo/productora, fechas extremas, estado del instrumento -- y sin la columna de archivo de gestión, que en una TVD no existe. La justificación de la valoración es el campo principal del formulario de creación, no uno secundario: es el objeto del instrumento. El circuito completo (aprobar, convalidar, registrar RUSD, derogar, devolver) se construyó como un componente compartido y se conectó a las dos pantallas -- TRD y TVD -- para que ambas lo resuelvan de la misma manera; los actos irreversibles (convalidar, derogar) avisan dentro del cuadro de confirmación, antes de pulsar el botón, nunca después de que la acción falle. Ningún botón de transición se ofrece si el estado actual no lo permite. Se comprobó, porque el ADR lo pedía expresamente, que el chip de sugerencia de clasificación de radicación nunca pudiera proponer una TVD: no lo hace, porque nunca consulta las series/agrupaciones -- resuelve el tipo documental sugerido a partir de radicados precedentes, un dato distinto.
  *Por qué*: sin pantalla, ninguna entidad con fondo acumulado puede completar el circuito de valoración que el backend ya expone.
  *Impacto*: 2 rutas nuevas, 1 componente compartido, proxies nuevos (incluido el historial de versiones que le faltaba a TRD en el frontend); 68 tests nuevos, suite completa 1295/1295 (antes 1227), sin errores de tipos.

- `fix(archive)`: **remediación del dictamen de conformidad+seguridad sobre el circuito de convalidación de la TRD (ADR-025, migración 038)**
  Cinco hallazgos de conformidad sobre el Increment 2 (recién liberado): `aprobada` era un callejón sin salida que empujaba a declarar una convalidación falsa — se añade `devolver` (con observaciones, motivo obligatorio); el criterio de qué puede autorizar una eliminación efectiva estaba invertido (bloqueaba eliminaciones legítimas de expedientes cuyo instrumento fue convalidado y luego derogado) — corregido para leer el snapshot congelado del expediente, no la fila viva; `derogar` sin sucesora dejaba el código de serie sin ningún camino de vuelta — ahora se bloquea mientras haya expedientes abiertos bajo el código, y crear una versión nueva ya no da 404 permanente; la aprobación del Comité no dejaba ni acta ni fecha — ahora es obligatoria y se verifica antes de convalidar; el RUSD y la publicación solo eran capturables en el momento de convalidar, cuando la norma da hasta 30 días hábiles después — nuevo endpoint para registrarlos.
  De regalo, cinco hallazgos de seguridad sobre el mismo código: el informe de expedientes pendientes de revisión no filtraba por nivel de clasificación (cualquiera con permiso sobre la TRD veía expedientes reservados); el re-apunte de tipos documentales podía romperse con un 500 en un flujo perfectamente legítimo; una carrera entre dos convalidaciones concurrentes podía dejar referencias huérfanas sin avisar; los registros de auditoría no dejaban ver el estado anterior de una derogación; y el fundamento jurídico de una versión ya convalidada solo lo protegía la aplicación, nunca la base de datos.
  *Por qué*: una auditoría de conformidad archivística + una de seguridad, ambas sobre el mismo incremento recién entregado.
  *Impacto*: 3 endpoints nuevos (`devolver`, `registrar-rusd`, más el body ahora obligatorio de `aprobar`), 1 migración de esquema, 22 tests nuevos; suite completa sin roturas (701 unitarios + 151 de integración contra Postgres real, antes 679+145).

- `feat(frontend)`: **MVP de PINAR — Plan Institucional de Archivos en `/admin/pinar` (Fase 7, Acuerdo AGN 003/2015)**
  PINAR tenía 30 endpoints en el backend y ninguna pantalla. Este MVP cubre el listado de planes (filtro por estado, crear una versión nueva siempre en borrador) y el workspace de un plan con un indicador visual del ciclo de vida (borrador → aprobado → en ejecución → cerrado, informativo — las transiciones solo ocurren por botón + confirmación) y una pestaña de tablero de avance por objetivo/por eje.
  Aprobar un plan **congela su contenido de forma permanente** (queda registrado en la auditoría inmutable): el aviso aparece antes de confirmar, no después de un error. El resto de PINAR (aspectos críticos, priorización, objetivos, proyectos, seguimiento, instrumentos, mapa de ruta) queda para una fase posterior.
  *Por qué*: cerrar la Fase 7 del desfase entre lo que el backend ya exponía y lo que la aplicación permitía usar.
  *Impacto*: 2 rutas nuevas + 6 proxies SSR; 41 tests nuevos; suite completa 1227/1227, sin errores de tipos.

- `feat(archive)`: **circuito de convalidación de la TRD — `aprobar`/`convalidar`/`derogar` + ratificación de series migradas (ADR-025 Increment 2)**
  Sobre el versionado append-only de la TRD (Increment 1, ya en `main`), se añade el circuito completo del Acuerdo AGN 004/2019: aprobar una versión en `borrador` (aprobación del Comité), convalidarla (con acto administrativo, fecha e instancia convalidante — deroga la versión previa y re-apunta los tipos documentales/plantillas que quedarían huérfanos), derogar una versión vigente, y **ratificar** una serie preexistente marcada `migrada` sin necesidad de crear una versión nueva. Nuevo informe paginado de expedientes pendientes de revisión. La regla de que la eliminación efectiva de documentos exigirá una serie estrictamente convalidada queda codificada desde ya, aunque el sistema todavía no tiene ningún proceso que ejecute esa eliminación.
  *Por qué*: cerrar la fuga retroactiva (Increment 1) dejó todas las series existentes "sin poder afirmar si fueron convalidadas" — hacía falta la vía real para que una entidad declare el acto administrativo que ya tenía en papel.
  *Impacto*: 4 endpoints nuevos bajo `/api/v1/trd` en archive-service, mismo control de acceso que el resto de la TRD; sin migración de base de datos; 36 tests nuevos (unitarios + contra Postgres real), sin roturas en la suite existente.

- `feat(frontend)`: **búsqueda semántica y de antecedentes en `/busqueda` (Fase 6, E21)**
  Dos pestañas nuevas junto a la búsqueda exacta existente — **Semántica** (similitud vectorial sobre el contenido indexado) y **Antecedentes** (recuperación anclada y citada, por texto o por un radicado pivote) — resueltas por `?tab=` como deep-link, igual patrón que el conmutador de vista de `/bandeja`. Tres proxies SSR nuevos (D-05): búsqueda semántica, antecedentes, y resolución de un radicado por su número (verificación del pivote antes de buscar antecedentes "por radicado").
  Los resultados se presentan siempre como documentos **parecidos** (similitud, con una banda cualitativa explicada), nunca como coincidencias exactas; un fallo de red se distingue de "sin resultados"; y el recorte por permisos de acceso es invisible por diseño — la pantalla nunca revela cuántos resultados quedaron ocultos.
  *Por qué*: el servicio de conocimiento ya exponía búsqueda semántica y antecedentes sin ningún consumidor de exploración directa en el frontend.
  *Impacto*: 3 proxies SSR nuevos con 18 tests; pestañas con roles ARIA de tablist accesibles por teclado; suite completa 1186/1186 (antes 1168), `svelte-check` 0 errores.

- `feat(frontend)`: **ayuda generativa (RAG) integrada en redacción y clasificación de radicación/borradores (F6, E21)**
  El RAG se integra donde ya se redacta y clasifica, no como módulo aparte. Botón "Sugerir con antecedentes" en `/radicar` (paso 1, cuerpo de Salida/Interno) y en el drawer de `/borradores`, que llama al RAG generativo con citas (E21 Inc.3) vía proxies SSR (D-05). Chips de tipo documental sugerido junto al selector de clasificación, alimentados por `/antecedentes` (E21 Inc.2, Patrón A) más una resolución honesta de `doc_class` por precedente (ese endpoint no lo trae en su contrato). Sección "Respuestas" nueva en el drawer de detalle de `/bandeja` (Salidas que responden a un radicado).
  Tres reglas no negociables: el texto generado se inserta SIEMPRE marcado como generado y con sus citas (sobrevive el saneo del editor porque usa solo etiquetas permitidas); si no hay citas no se ofrece insertar, nunca una afirmación sin fuente; el chip de tipo documental sugerido nunca se autoaplica; y un fallo del servicio de conocimiento queda contenido en el componente sin bloquear radicar/guardar un borrador.
  *Por qué*: cierre de la Fase 6 del desfase API↔UI — el backend ya exponía RAG/antecedentes sin consumidor en el frontend.
  *Impacto*: 4 proxies SSR nuevos, 2 componentes nuevos (`lib/components/features/rag/`), `lib/utils/ragInsert.js` nuevo; 51 tests nuevos; suite completa 1186/1186 (antes 1135), `svelte-check` 0 errores.

- `fix(notification)`: **endurece el destino de los webhooks salientes (hallazgo Media, auditoría de seguridad — no bloqueante)**
  `WebhookCreate.url` validaba la forma, no el destino: un administrador podía suscribir un webhook a `postgres`, `keycloak` o cualquier IP privada/loopback/link-local, dirigiendo 3 reintentos × evento contra la red interna. El auditor confirmó severidad Media (POST ciego, sin cabeceras controlables, sin seguimiento de redirects). Nuevo `app/core/webhook_security.py::validate_webhook_target`, usado al crear la suscripción (rápido, sin DNS, produce `422` con el motivo) y en cada intento de entrega (con resolución DNS real, cierra el caso de DNS rebinding y cubre suscripciones preexistentes). Kill-switch de desarrollo `webhook_allow_private_targets` (`False` por defecto — estricto). Suscripciones preexistentes que ya no cumplen la política no se borran: simplemente dejan de entregarse.
  *Por qué*: auditoría de seguridad de notification-service.
  *Impacto*: `POST /api/v1/webhooks` gana un `422` nuevo; 18 tests pytest nuevos (155 passed en el servicio); sin migración.

- `fix(auth)`: **cierre de hallazgo Alto — auto-escalada a `USUA_PERM_ROOT`; ROOT ahora se aprovisiona fuera de banda ([ADR-024](adr/ADR-024-root-fuera-de-banda.md))**
  Un administrador de entidad (`USUA_PERM_ADMIN`) podía auto-concederse `USUA_PERM_ROOT` (bypass total de RBAC) por dos vías — crear un usuario con `is_root: true`, o conceder `USUA_PERM_ROOT` al grupo del que él mismo es miembro — y `GET /auth/permissions` ofrecía `USUA_PERM_ROOT` sin filtrar en el desplegable del panel admin. Un tercer eje: podía subir el clearance de un grupo por encima de su propio clearance efectivo. Las tres vías responden ahora `403` a un llamante no-ROOT; el catálogo de permisos omite `USUA_PERM_ROOT` para no-ROOT. Como el primer ROOT de un tenant no tenía ninguna vía de creación fuera de la API, se añadió `app/ops/grant_root.py` (script de operación, no HTTP): concede ROOT a un usuario existente de un tenant explícito, idempotente y auditado.
  *Por qué*: auditoría de seguridad dedicada a auth-service — el administrador de entidad era, de facto, indistinguible de ROOT.
  *Impacto*: `POST /auth/users`, `PUT /auth/groups/{gid}/permissions/{pid}` y `PUT /auth/groups/{gid}/clearance` ganan un `403` nuevo para no-ROOT; `GET /auth/permissions` cambia de forma para no-ROOT; nuevo script `app/ops/grant_root.py --tenant <slug> --user <username>`; 10 tests nuevos (198 passed, 15 skipped); sin migración.

- `fix(tenant,gateway)`: **`app/core/authz.py` + Canal A en tenant-service; `/api/v1/tenants/` sacado del gateway (cierra 2 hallazgos Críticos de auditoría de seguridad)**
  El servicio no tenía **ningún** gate de autorización: 13 escrituras (catálogos, dependencias, festivos, parámetros) quedaban abiertas a cualquier autenticado del tenant — un radicador podía borrar los festivos del año, adelantando el cálculo de todos los plazos legales de respuesta. Un comentario en `config.py` afirmaba (falsamente) que "el api-gateway media el acceso"; se borró junto con sus gemelos. Cerrado con `app/core/authz.py` (`require_permission`, mismo patrón de document-service/archive-service) — `USUA_PERM_ADMIN` en las 13 escrituras + las 18 del PINAR; los `GET` quedan solo autenticados. Además, `/api/v1/tenants/` (registro de tenants, sin scoping de tenant) estaba enrutado por el gateway sin ningún gate — la única fuga *entre* entidades del sistema: cualquier usuario de la entidad A podía leer y **escribir** el registro de la entidad B. Se sacó ese prefijo de la tabla de ruteo del gateway (no hay consumidor legítimo externo). Por último, las escrituras que antes mutaban sin traza ahora dejan asiento en `public.audit_log` dentro de la misma transacción (fail-closed, patrón `RBACService`).
  *Por qué*: una auditoría de seguridad dedicada encontró que tenant-service, pese a gestionar plazos legales y el organigrama, no tenía ningún control de acceso, y que el único endpoint sin scoping de tenant del sistema estaba expuesto al exterior.
  *Impacto*: `/api/v1/tenants/` ya no es alcanzable desde el gateway (cambia el contrato); 40 tests pytest nuevos/actualizados en tenant-service + 2 en api-gateway; sin migración.

- `fix(document)`: **`USUA_PERM_TRD` + auditoría en el catálogo de metadatos (E03, cierra hallazgo Alto de auditoría)**
  Crear una plantilla de metadatos de documento y las tres escrituras del catálogo de elementos reutilizables (crear/editar/borrar) no exigían ningún permiso — cualquier autenticado del tenant podía anular la validación de metadatos obligatorios de un tipo documental completo, ya que los radicados se validan contra la plantilla activa. Su gemelo en archive-service sí exigía `USUA_PERM_TRD`; se cierra con el mismo permiso. El borrado exige además `min_crud=3`. Las cuatro escrituras ahora también auditan en `public.audit_log`, dentro de la misma transacción que la mutación (fail-closed).
  *Por qué*: era una omisión, no una decisión de diseño — ya advertida como hallazgo pendiente en el changelog de `/admin/metadatos`.
  *Impacto*: las lecturas (`GET`) quedan intactas; 10 tests pytest nuevos; sin migración; cambia el contrato (endpoints que antes no exigían permiso ahora sí).

- `feat(auth)`: **Auditoría inmutable de las escrituras RBAC/URD/clearance (E08, cierra hueco de trazabilidad)**
  Crear usuario, crear grupo, alta/baja de miembro, asignar permiso a grupo, crear/eliminar URD y subir/bajar el clearance de un grupo ahora dejan asiento en `public.audit_log`, DENTRO de la misma transacción que la mutación — no best-effort: si la auditoría falla, la escritura se revierte; si la librería de auditoría no está instalada, la escritura se rehúsa (500) en vez de saltar el asiento en silencio. `tenant_slug` es obligatorio y fail-closed. El asiento de clearance incluye el nivel anterior y el nuevo.
  *Por qué*: son las vías por las que se escala un privilegio; el panel `/admin` las convirtió en un clic, así que el hueco pasó de teórico a explotable por error humano.
  *Impacto*: sin migración ni cambio de contrato HTTP; 20 tests pytest nuevos.

- `feat(frontend)`: **Administración de TRD/CCD, metadatos, catálogos y parámetros (E03/E04/E14, Fase 5 cierre de desfase API↔UI, segunda tanda)**
  Cuatro pantallas nuevas del panel `/admin`. `/admin/trd`: series TRD/CCD en árbol indentado por `parent_id` (código inmutable, gate real `USUA_PERM_TRD`) y tipos documentales con CRUD completo. `/admin/metadatos`: plantillas de expediente y de documento (solo alta — el backend no tiene `PATCH`/`DELETE`, nueva versión = nuevo registro), con el `json_schema` editado como JSON crudo validado antes de enviar; y elementos de metadato reutilizables con CRUD completo. `/admin/catalogos`: maestro-detalle (catálogos a la izquierda, items a la derecha), que degrada a dos niveles navegables en móvil. `/admin/parametros`: parámetros del tenant, calendario de festivos (precarga Ley 51/1983 sin llamada externa) y calculadora de días hábiles que muestra el desglose de qué días se descontaron y por qué (el backend solo devuelve la fecha resultante).
  **Hallazgo de dominio reportado (no corregido en el front)**: a diferencia de TRD/tipos documentales/plantillas de expediente (gate real `USUA_PERM_TRD`), las plantillas/elementos de metadato de documento y los catálogos/parámetros de tenant-service no exigen ningún permiso en el backend hoy; las cuatro pantallas gatean sus botones de escritura del lado del cliente como medida conservadora.
  *Por qué*: cerraba cuatro áreas de administración listadas como "Próximamente" desde la Fase 0 — sin ellas, la clasificación documental, los esquemas de metadatos, los catálogos de referencia y los parámetros de plazo solo eran operables vía API directa.
  *Impacto*: 13 proxies SSR nuevos; sin cambios de backend ni de esquema; 55 tests nuevos de proxy; `svelte-check` 0 errores/0 warnings; 1125 tests vitest verdes.

- `feat(auth)`: **Lectura de membresías, permisos y clearance de grupo (E08, cierra el gap RBAC read)**
  Tres `GET` nuevos, espejo exacto de la escritura ciega equivalente: `GET /api/v1/auth/groups/{group_id}/members` (paginado), `GET /api/v1/auth/groups/{group_id}/permissions` y `GET /api/v1/auth/groups/{group_id}/clearance` (`max_level: null` si no tiene fila asignada). Mismo gate `USUA_PERM_ADMIN` que sus escrituras hermanas. `group_id` inexistente o de otro tenant → `404` indistinguible, nunca `403`. Sin migración.
  *Por qué*: el panel `/admin/grupos` podía dar de alta un miembro/permiso/clearance pero no mostrar el estado actual del grupo — un admin no puede auditar ni corregir lo que no puede ver.
  *Impacto*: 3 endpoints nuevos; 14 tests pytest nuevos (200, 404 grupo inexistente/de otro tenant, 403 sin permiso); sin cambios de esquema.

- `feat(frontend)`: **Administración de grupos y dependencias (E08/E14, Fase 5 cierre de desfase API↔UI)**
  Nueva pantalla `/admin/grupos`: listado con `CatalogCrudPanel` (crear grupo; no hay edición ni borrado — el backend solo tiene alta+lectura de grupos) y un drawer de detalle para miembros (buscador + alta/baja), permisos por grupo (crud 0-5) y clearance (reutiliza `nivelSeguridad.js`). **Gap de backend verificado y advertido en la propia UI**: no existe ningún endpoint de lectura de membresías, permisos ni clearance ya asignados a un grupo — solo altas/bajas ciegas. Nueva pantalla `/admin/dependencias`: vista árbol del organigrama (`GET /dependencias/tree`) con un componente nuevo `DependenciaTreeNode.svelte` (no existía ningún componente de árbol reutilizable en el proyecto), crear/editar/activar/desactivar. Renombrar o desactivar dispara un modal de confirmación de riesgo explícito (nunca un tooltip): las dependencias se correlacionan por nombre, no por id/código, en las reglas de enrutamiento y en los pasos de flujo en vuelo. Borrado duro queda fuera del alcance pedido.
  *Por qué*: cerraba dos áreas de administración listadas como "Próximamente" desde la Fase 0, con impacto directo en RBAC/clearance y en la integridad del enrutamiento.
  *Impacto*: 12 proxies SSR nuevos (D-05), 1 componente nuevo, sin cambios de backend ni de esquema; 57 tests nuevos de proxy; `svelte-check` 0 errores.

- `feat(frontend)`: **Índice de información clasificada y reservada (E08, Ley 1712 art. 20) + Disposición final del radicado/expediente (RF-MET-08/E04, Fase 4 cierre de desfase API↔UI)**
  Nueva pestaña "Índice de clasificados" en `/reportes` (gate real `PERM_RECLASIFICAR`): registro content-free por diseño con banner legal, sin ninguna columna de asunto/contenido, y descarga CSV completa diferenciada de la exportación local de solo lo visible. Nuevo Drawer "Disposición final" accesible desde `/bandeja` (edición, con confirmación explícita para la acción de eliminación) y desde `/expedientes/[id]` (solo lectura: muestra la regla ya aplicada al cierre, o una vista previa de la regla de la serie TRD si el expediente sigue abierto — nunca una fecha inventada). Se verificó contra el backend que un radicado individual no tiene vínculo directo a una serie TRD; el Drawer lo declara explícitamente en vez de simular un vínculo inexistente.
  *Por qué*: cerraba dos obligaciones sin UI operable — el instrumento de transparencia del art. 20 y la decisión (irreversible en el caso de eliminación) sobre disposición final.
  *Impacto*: 2 proxies SSR nuevos, 1 faceta SSR no crítica nueva, 2 componentes nuevos; sin cambios de backend ni de esquema; 16 tests nuevos de proxy.

- `feat(frontend)`: **Detalle de transferencia con acta-certificado y firma personal por rol + administración de preservación digital (E12/E17/E10, Fase 4 cierre de desfase API↔UI)**
  Nueva subruta `/transferencias/[id]` con el acta de entrega presentada en apariencia de certificado (qué se transfiere, entre qué dependencias, cuándo, y el estado de firma de sus TRES roles — elaborador, remitente, receptor). El rol de CADA persona se deriva client-side (espejo del backend) para mostrarle solo lo que a ella le corresponde firmar, nunca un botón "Firmar" genérico — el backend siempre re-valida. Incluye verificación on-demand del sello institucional XAdES y de las firmas personales, y reintento manual del sellado. Nueva pantalla `/admin/preservacion` (plan de preservación versionado, índices pendientes de sellado XAdES con reintento manual, y visibilidad de artefactos WORM pendientes de renovación) — el empaquetado manual del AIP y la protección WORM manual de un artefacto se omitieron deliberadamente por falta de un endpoint público utilizable sin inventar datos.
  *Por qué*: cerraba un desfase entre lo que el backend ya exponía (E12/E17 firma personal por rol, E10 visibilidad operacional) y lo que la UI permitía operar.
  *Impacto*: solo proxies SSR nuevos (D-05); sin cambios de backend ni de esquema; 26 tests nuevos de proxy.

- `fix(workflow)`: **`workflow_rules.assign_to_dept` validado al configurar la regla + traza en `audit_log` de la degradación silenciosa**
  `POST /workflow/rules` y `PATCH /workflow/rules/{id}` no comprobaban `assign_to_dept` (texto libre, sin FK) contra el catálogo `dependencias` — una regla mal tecleada se aceptaba en silencio y solo se descubría en ejecución, radicado a radicado, vía un `logger.warning` que nadie lee. `WorkflowRulesService._validate_assign_to_dept` reutiliza `WorkflowRepository.dependencia_activa` (el mismo helper que ya cierra `assign`/`transfer`) para rechazar con 422 `dependencia_desconocida` ANTES de escribir la fila; el PATCH parcial solo valida si el cliente tocó el campo. Además, `auto_distribute` (para reglas ya existentes cuya dependencia se dio de baja después) deja un asiento best-effort en `public.audit_log` (`workflow.regla_dependencia_invalida`) en vez de degradar solo por log.
  *Por qué*: cierra el residual declarado en el CLAUDE.md del servicio — la desviación de una regla mal configurada debe detectarse cuando un administrador puede corregirla, y ser auditable sin depender de que alguien revise los logs del worker.
  *Impacto*: aditivo, sin migración; no cambia el comportamiento de degradación de `auto_distribute` (el radicado sigue sin autodistribuirse, sin reintentos de DLQ contra un dato permanentemente inválido).

- `fix(document)`: **Auditoría del ciclo de anulación de radicados (ADR-008/010)**
  `AnulacionService` no dejaba ningún asiento en `public.audit_log`: solicitar, aprobar y rechazar la anulación de un radicado (el acto más consecuente de un SGDEA — retira de la circulación un documento oficial con número inmutable) era invisible para la traza legal forense. Ahora los tres actos (`document.anulacion_solicitada`, `document.anulacion_aprobada`, `document.anulacion_rechazada`) auditan dentro de la misma transacción que la mutación — si `audit.append` falla, la solicitud/decisión entera revierte — con `object_ref` resuelto server-side (`tracking_number`), nunca del body.
  *Por qué*: anular un radicado incumplía Ley 594/2000 y el ADR-010 sin dejar rastro atribuible; una anulación aprobada sin traza es peor que una anulación fallida.
  *Impacto*: aditivo, sin migración de esquema (reutiliza `public.audit_log`); no toca autorización ni clearance existentes.

- `fix(workflow)`: **Auditoría forense de devolución, transacción de trámite y reasignación en cascada (ADR-008/010)**
  `devolver`, `execute_transaction` y `reassign_cascade` mutaban el trámite sin dejar asiento en `public.audit_log` (Canal A) — deuda declarada en el propio servicio. Ahora los tres auditan (`workflow.devolver`, `workflow.transaccion`, `workflow.reasignacion_cascada`) dentro de la misma transacción que la mutación, con `object_ref` resuelto server-side. El asiento del barrido en cascada registra el conteo REAL de pasos movidos, y no fabrica un número de radicado que no existe.
  *Por qué*: eran las tres últimas mutaciones del servicio sin traza legal atribuible (Ley 594/2000); una reasignación masiva sin constancia de su alcance es forensemente inútil.
  *Impacto*: aditivo, sin migración ni cambios de autorización, clearance o no-oráculo.

- `feat(workflow)`: **Motivo obligatorio al revertir una asignación (RF-FLU-08)**
  Nuevo campo `motivo` obligatorio (5-500 caracteres) en `POST /workflows/{radicado_id}/rollback`; antes el frontend lo pedía pero el backend lo descartaba sin dejar rastro.
  *Por qué*: revertir una asignación es un acto sobre el ciclo de vida del radicado y debe quedar trazado legalmente, no solo operativamente.
  *Impacto*: el motivo queda registrado en el historial operativo (`flow_events`) y en la traza legal (`audit_log`); sin migración de esquema.

- `feat(frontend)`: **Sistema de Diseño OrpycaMCP v1.0 (E22, ADR-020)**
  `_tokens.scss`/`custom-bulma.scss` pasan de la paleta provisional a la definitiva v1.0, conservando todos los nombres `--op-*`/`$op-*`.
  *Por qué*: el contraste WCAG de cada par texto/fondo se calculó por primera vez en vez de estimarse, y obligó a separar el primario de uso no-textual (`--op-primary`, 4.28:1) del de texto (`--op-primary-dark`, 6.56:1).
  *Impacto*: nueva escala tipográfica, radios, sombras, tokens de movimiento y tipografías self-hosted; cero rotura en las 19 pantallas existentes.

- `fix(frontend)`: **Font Awesome nunca se cargaba y el favicon daba 404 (E22)**
  El paquete estaba instalado y las clases `fas fa-*` se usaban en toda la app, pero su CSS nunca se importaba: los iconos eran cajas vacías. `+layout.svelte` ahora lo importa y `static/` recibe los activos reales de marca.

- `feat(frontend)`: **Landing pública en `/` (E22)**
  Nueva ruta `(public)/+page.svelte` con 7 secciones (hero, franja normativa Ley 594/Acuerdo AGN 060, comparativa Orfeo→ORPYCA, footer AGPL v3); redirige a `/dashboard` si hay sesión.

- `feat(frontend)`: **Login, callback y `/403` con la marca real (E22)**
  `/login` sustituye el placeholder textual por el logotipo y estrena composición split mobile-first; `/403` pasa a un mensaje no culpabilizador que orienta a contactar al administrador.

- `feat(frontend)`: **Búsqueda global en el topbar y favoritos configurables en el sidebar (E22)**
  El topbar gana un formulario de búsqueda hacia `/busqueda?q=…`; el sidebar gana favoritos fijables persistidos en `localStorage`, namespaced por tenant+usuario.
  *Impacto*: el store de favoritos nunca filtra por su cuenta — exige la lista ya filtrada por permisos, así un favorito cuyo permiso se pierde deja de mostrarse solo.

- `feat(frontend)`: **Dashboard rediseñado a los cuatro módulos personales (E22)**
  `/dashboard` mostraba las métricas agregadas del tenant (información de jefatura); ahora prioriza los cuatro módulos personales y relega las métricas a una sección colapsable.
  *Impacto*: las métricas quedan gateadas por `SGD_PERM_ESTADISTICA` tanto en la UI como en el servidor — la UI vuelve a ser espejo del RBAC.

- `feat(frontend)`: **`DataTable` gana las ocho capacidades obligatorias del sistema de diseño (E22)**
  Filtros por columna, ordenamiento, exportación CSV (con neutralización de inyección de fórmulas), columnas configurables, vistas guardadas, selección múltiple, virtualización y edición rápida en celda — todas opt-in por prop, con el comportamiento actual como default.

- `docs(es)`: **Actualización integral de arquitectura, pantallas y funcionalidades**
  `architecture.md` reescrito con diagramas mermaid; nueva `pantallas.md` con el inventario de vistas; ADR-021/022/023 incorporados al sitio; puertos de ejemplo corregidos en toda la documentación. `docs/en/` sincronizado en un segundo pase.

- `feat(archive)`: **Descripción archivística EAD 2002 / ISAD(G) multinivel de expedientes en OAI-PMH (E11, RF-INT-06)**
  Nuevo endpoint público `GET /api/v1/public/archive/oai/{tenant}`: cada expediente público se disemina como fragmento EAD 2002 multinivel, caminando el CCD (`fonds`→`series`→`file`).
  *Impacto*: chokepoint `_PUBLIC` en `expedientes` y frontera anti-fuga en `level="file"` — nunca enumera radicados hijos reservados de un expediente público.

- `feat(document)`: **Descripción archivística EAD 2002 / ISAD(G) en OAI-PMH (E11, RF-INT-06)**
  Segundo `metadataPrefix=ead` en `GET /api/v1/public/oai/{tenant}` con los 6 elementos obligatorios de ISAD(G)/NTC 4095. Reutiliza el chokepoint `_PUBLIC` ya existente — no crea oráculo de reservados. Pendiente: descripción multinivel (resuelta luego por archive-service).

- `feat(document)`: **Importación interoperable de radicados (E11, RF-INT-05)**
  `POST /api/v1/import` recibe el paquete ZIP de exportación (INT-01) con validación todo-o-nada: verifica el SHA-256 de cada binario antes de ingerir.
  *Impacto*: preserva el número de radicado original (colisión → omite, no sobreescribe, Ac. 060/2001); checksum no coincidente → 422 sin estado parcial. Cierra la interoperabilidad de radicados junto a INT-01.

- `feat(document)`: **Exportación interoperable de radicados (E11, RF-INT-01)**
  `POST /api/v1/export` produce un paquete ZIP con manifiesto, JSON Schema publicado, un JSON validado por radicado, binarios y `checksums.txt` (fixity, Ley 594 art. 19).
  *Impacto*: gate no-read-up por-radicado; el número de radicado se preserva como identidad portable, cimiento de la importación (INT-05).

- `feat(document)`: **Perfil CMIS 1.1 mínimo de lectura (E11, RF-INT-02)**
  `GET /api/v1/public/cmis/{tenant}` (repositoryInfo + Browser Binding JSON); `getContentStream` sirve los bytes del anexo solo si el radicado es público, mismo chokepoint que OAI. Solo-lectura, sin carpetas anidadas ni CMIS-SQL.

- `feat(document)`: **OAI-PMH — cosecha de metadatos (E11, RF-INT-02; SGDEA R.12.1)**
  `GET /api/v1/public/oai/{tenant}`: los 6 verbos OAI-PMH 2.0 + Dublin Core, cosecha selectiva y `resumptionToken`.
  *Por qué*: crítico de seguridad (Ley 1712 arts. 18-19) — solo expone lo público (`nivel_seguridad=1`), filtrado en la fuente para que la omisión sea indistinguible.

- `feat(tenant)`: **PINAR — Plan Institucional de Archivos (RF-ADM-08, E14; Acuerdo AGN 003/2015)**
  `tenant-service` gana `/api/v1/pinar/*`: gestiona el PINAR como instrumento de planeación articulado por referencia con CCD/TRD, FUID/IUD y el plan de preservación.
  *Impacto*: 9 tablas nuevas, priorización determinista por impacto, un único plan en ejecución por tenant, aprobación con acto administrativo auditado en `audit_log` inmutable.

- `fix(document)` + `feat(document,archive)`: **Cierre de deudas declaradas (E08/E10/E02)**
  No-write-up al radicar (un usuario PUBLICA no puede originar una CLASIFICADA); asientos de reconciliación WORM atribuidos al principal de sistema; el manifiesto de export de expediente gana columnas `tipo`/`asunto`.
  *Impacto*: quedan deudas declaradas en roadmap (conector postal E11, catálogo cerrado de fundamentos E08, PDF combinado E02, LLM/embeddings reales E21).

- `feat(archive)` + `feat(storage)`: **Exportación de expedientes a ZIP — `GET /api/v1/expedientes/{id}/export.zip` (E02/export; Ley 594/2000 art. 19)**
  Descarga el expediente completo como paquete portable de consulta (distinto del AIP de preservación): índice XML, `manifiesto.csv`, bytes de anexos, `LEEME.txt` y `checksums.txt`.
  *Impacto*: remediada una fuga read-up por-radicado — el gate era solo a nivel de expediente y un radicado clasificado dentro de un expediente de menor nivel entregaba sus bytes; ahora se filtra por fila.

- `feat(document)`: **Índice de información clasificada y reservada — `GET /api/v1/reports/indice-reservado(.csv)` (E08; Ley 1712/2014 art. 20)**
  Registro que la Ley 1712 obliga a publicar; materializa la clasificación vigente en `radicados` (content-free: nunca expone el asunto).
  *Por qué*: es ortogonal al clearance — registro completo por mandato legal, igual que la disposición o la custodia.
  *Impacto*: clasificar a nivel≥2 exige `fundamento` obligatorio (422 si falta), así el índice nunca queda con fundamentos nulos.

- `feat(document)`: **Operador postal como consumidor de webhooks — callback entrante `POST /api/v1/public/postal/callback/{tenant_slug}` (E20 F5; RF-POR-08; ADR-018)**
  El operador notifica el estado de entrega vía callback idempotente con identidad propia (HMAC-SHA256 per-tenant/operador), bajo `/public/` sin tocar el gateway.
  *Impacto*: aislamiento multi-tenant estricto (el secreto vive en el schema del tenant), fail-closed ante firma inválida, idempotencia por `UNIQUE(operador, event_id)`.

- `feat(knowledge)`: **RAG generativo con citas — `POST /knowledge/rag` (E21 Inc.3; ADR-006)**
  Genera una respuesta anclada a los radicados recuperados, con proveedor de LLM pluggable (`ollama_local`/`ollama_cloud`/`anthropic`/`disabled`).
  *Por qué*: resuelve la decisión de soberanía pendiente del Inc.1 — la barrera dura de residencia (`is_generatable`) aplica también a la generación, sin override, así material reservado/clasificado nunca llega a un proveedor externo.
  *Impacto*: sin contexto tras excluir por residencia, degrada honestamente al patrón de solo-recuperación (503 nunca respuesta simulada).

- `feat(knowledge)`: **Recuperación anclada con citas — `POST /knowledge/antecedentes` (E21 Inc.2)**
  Expone la búsqueda semántica como respuesta anclada y verificable — radicados relacionados con snippet y score, siempre con advisory de que es un índice derivado a confirmar contra la fuente. Reutiliza el pre-filtro ACL de `search`, sin duplicarlo.

- `feat(knowledge)`: **Proveedor de embeddings real, local/soberano (E21 Inc.1; ADR-006)**
  Sustituye el stub determinista por `EmbeddingProvider` pluggable, con `local_st` (sentence-transformers, corre en el contenedor) como default de despliegue.
  *Impacto*: migración que **NULLea los embeddings existentes** (dimensión incompatible, 64→384) y requiere backfill (`python -m app.ops.reembed`); con proveedor local, el material Reservado/Clasificado sí se embebe (nunca sale del contenedor).

- `feat(storage)`: **Validación PDF/A en la ingesta/captura de anexos (E07, E10 C2b follow-up; RF-PRE-02)**
  La validación PDF/A solo corría al empaquetar el AIP; ahora corre también al subir el anexo, en modo `advisory` (default, no bloquea la radicación) o `strict` (opt-in, 422 ante no-conformante).
  *Por qué*: Ac. 060/2001 obliga a radicar toda comunicación — no se puede bloquear la captura por formato.

- `feat(storage)` + `feat(archive)`: **AIP índice-only para expedientes 100% físicos (E10 AIP wiring follow-up; OAIS ISO 14721; RFC 8493)**
  Un expediente sin anexos digitales quedaba sin paquete OAIS portable pese a tener su índice firmado bajo WORM; ahora se empaqueta un AIP índice-only con el índice firmado como payload del Bag.
  *Impacto*: el PREMIS nunca fabrica objetos-documento inexistentes; declara explícitamente que es un expediente con soportes físicos referenciados.

- `feat(archive)` + `feat(storage)`: **Perfil PDF/A configurable por serie documental / TRD (E10 C2b follow-up; RF-PRE-02)**
  El perfil de validación del AIP era global (2b); ahora es configurable por serie vía `trd_series.pdfa_profile` (whitelist).
  *Impacto*: un perfil ausente cae a 2b (retrocompat); uno inválido se degrada a `not_evaluated`, nunca conforme.

- `feat(archive)` + `feat(storage)`: **Renovación WORM del AIP para series de Conservación Total (E10; cierra deuda Alta del cableado del AIP)**
  La renovación de retención cubría índice+acta pero no el AIP (que no tenía espejo cuando se implementó); ahora `run_once_worm_renovacion` renueva los 3 artefactos. Cierra la brecha de preservación donde el paquete OAIS completo del CT podía perder su lock.

- `feat(archive)` + `feat(document)` + `feat(storage)`: **Cableado del AIP OAIS al cierre del expediente (E10 debt; OAIS ISO 14721; PREMIS v3; Ac. AGN 001/2024 art. 4.3)**
  `POST /preservacion/aip` empaquetaba el AIP real pero nadie lo disparaba; ahora archive lo dispara al cerrar el expediente (reconciliación best-effort).
  *Impacto*: nuevo endpoint interno de document-service (sin gate de clearance — acto de sistema/inventario total, inalcanzable por el gateway); advisory lock cierra la carrera de doble-empaquetado WORM irreversible.

- `feat(storage)`: **Validación PDF/A veraPDF real vía subprocess endurecido (E10 Increment C2b; RF-PRE-02)**
  Sustituye el stub honesto por el validador real; el núcleo del cambio es el modelo de seguridad del subprocess (no-shell, ENV scrubbeado, `killpg`+reap garantizado, JSON-only, whitelist de flavour).
  *Impacto*: sigue siendo advisory — un PDF no-conformante no bloquea el AIP; degrada honestamente a stub sin el binario instalado.

- `feat(storage)` + `feat(archive)`: **Renovación de retención WORM para series de Conservación Total (E10 debt; Ley 594/2000 + Ac. AGN 001/2024 art. 4.3.2.6)**
  Los objetos COMPLIANCE de series CT llevaban `retain_until` finito y volvían a ser borrables al vencer; nuevo endpoint interno extiende la retención solo si la nueva fecha es estrictamente mayor (idempotente).
  *Impacto*: se retiró la ruta pública equivalente (dejaba a cualquier `crud≥3` fijar irreversiblemente un lock no-CT) — solo queda la interna.

### Corregido

- `fix(api-gateway)`: **La puerta de enlace descartaba el total de resultados, así que toda pantalla paginada creía tener una sola página**
  Cuando un listado devuelve resultados, el servidor informa aparte de **cuántos hay en total** y, en las bandejas de eventos fallidos, de si la respuesta se quedó corta y hay más elementos antiguos que no se ven. La puerta de enlace central eliminaba ambos avisos antes de entregar la respuesta al navegador. Consecuencia: el total colapsaba al número de elementos de la página actual, de modo que **ninguna pantalla paginada del sistema ofrecía la página siguiente**; y el aviso de "hay más de lo que ves" de las bandejas de eventos fallidos nunca llegaba a mostrarse, con lo que la pantalla afirmaba en silencio que se veía todo.
  Ninguno de los dos fallos rompía nada visiblemente: la interfaz simplemente daba una información incorrecta con toda naturalidad. Es el mismo modo de fallo que ya se había pagado antes en el sentido contrario, con la firma del operador postal que la puerta de enlace tampoco reenviaba.
  *Por qué*: apareció al documentar dos pantallas de administración cuyas garantías de paginación y de aviso de truncamiento resultaron inalcanzables en producción; afecta a todas las pantallas paginadas, no solo a esas dos.
  *Impacto*: corregido junto con una prueba que fija el contrato y que se comprobó al revés — sin el arreglo, la prueba falla.

- `fix(archive)`: **Una prueba de integración quedó rota por la migración anterior**
  La comprobación que verifica el tratamiento de datos heredados aplicaba todas las migraciones **menos** una para reconstruir el escenario histórico, y la migración añadida en la entrega anterior depende precisamente de esa. Su comprobación de seguridad —correcta en un despliegue real, donde las migraciones se aplican en orden— hacía fallar ese escenario artificial. Corregido reordenando la prueba, sin tocar la comprobación de seguridad.
  *Por qué*: la migración se integró sin ejecutar el carril de pruebas contra base de datos real; el fallo salió al correrlo completo.
  *Impacto*: 168 pruebas de integración en verde. Ningún cambio en el comportamiento del sistema.

- `style(frontend)` + `fix(frontend,repo)`: **El verificador automático de calidad volvió a ejecutarse — y al hacerlo destapó tres defectos reales**
  La comprobación automática de calidad del código encadena dos herramientas, y la primera fallaba en 676 archivos por diferencias de formato, con lo que la segunda —la que busca errores de verdad— llevaba tiempo sin llegar a ejecutarse en la integración continua. Se formatearon todos y se corrigieron los doce errores que la segunda reportó al volver a funcionar. Lo importante no es el formateo, sino lo que dejó a la vista:
  **Uno**: diez textos de pantallas de administración estaban **truncados a la vista del usuario**. Llevaban comillas dobles dentro de un texto ya delimitado por comillas dobles, así que el sistema cortaba la frase por la mitad y donde debía leerse *«Crea el primero con "Nuevo usuario"»* solo aparecía *«Crea el primero con »*. Ninguna otra comprobación lo detectaba. **Dos**: una carpeta de archivos generados automáticamente —737 archivos, 16 MB— se había guardado en el repositorio por error, porque la regla que debía excluirla no cubría el nombre con el que quedó. Es la segunda vez que una regla de exclusión demasiado literal deja entrar lo que debía dejar fuera. **Tres**: una opción de configuración de un componente compartido no hacía nada desde que se escribió, y dos pantallas la usaban creyendo que sí.
  Las dos excepciones que se dejaron marcadas están justificadas por escrito, no silenciadas: el punto donde se inserta contenido con formato ya pasó antes por la limpieza de seguridad, y el patrón que busca caracteres invisibles los busca precisamente porque son los que se usan para disfrazar enlaces maliciosos.
  *Por qué*: una comprobación que nunca llega a ejecutarse da luz verde sin haber mirado; y lo que parecía deuda de estilo era lo único que ocultaba un texto cortado en diez pantallas.
  *Impacto*: verificación de tipos sin ningún aviso (antes tenía uno), 1370 pruebas en verde. Queda **un** error sin corregir: catorce archivos del proyecto pertenecen al usuario administrador del sistema por un montaje de Docker mal permisado y no se pueden editar sin elevar privilegios, cosa que no se hizo. Se resuelve devolviendo la propiedad de esos archivos al usuario.

- `fix(archive)`: **La base de datos ya puede garantizar que todo expediente apunta a una serie documental que existe (migración 040)**
  Cuando se introdujo el vínculo entre expediente y serie de la TRD, se dejó a propósito sin verificar el histórico: exigirlo de golpe habría impedido poner en marcha entidades con datos heredados. Quedaba anotado que la verificación completa vendría después, y es lo que hace esta migración. Hasta ahora el sistema impedía crear vínculos rotos nuevos, pero no podía **afirmar** que no hubiera ninguno antiguo.
  La decisión importante es qué hacer con los vínculos rotos que aparezcan: **no se arreglan solos**. Un expediente que apunta a una serie inexistente conserva, en ese dato, la posición que tuvo dentro de la tabla de retención que decidió cuánto debía conservarse; vaciar el campo borraría esa evidencia sin dejar constancia, y borrar el expediente sería destruir un documento público. Ambas son decisiones de archivo que corresponden a una persona con el caso delante, no a un script. Así que la migración **se detiene y los enumera uno a uno**, sin tocar nada, y puede volver a ejecutarse cuantas veces haga falta hasta que estén resueltos.
  *Por qué*: una garantía que la base de datos anuncia pero no comprueba no es una garantía.
  *Impacto*: probada contra una base de datos real en los tres escenarios (con un vínculo roto, sin ninguno, y repetida). 721 pruebas del servicio de archivo en verde.
  **Lo que no se hizo, y no por descuido**: existe una regla escrita que autoriza o deniega la eliminación definitiva de un expediente según el estado del instrumento que la gobierna, y hoy nada la invoca. La razón es que **el sistema todavía no elimina nada de forma definitiva**: esa capacidad —con su calendario de vencimientos, aprobación del comité de archivo, publicidad previa del inventario y acta de eliminación— es una funcionalidad completa aún por construir. La regla se escribió por adelantado para que quien la construya no tenga que reinventar el criterio. Conectarla ahora obligaría a improvisar una eliminación irreversible sin diseño, que es justo lo contrario de lo que exige la ley de archivos.

- `fix(auth,frontend)`: **La interfaz ofrecía acciones que el servidor iba a rechazar, porque no sabía en qué nivel estaba cada permiso (tercer lote de la revisión de experiencia de uso)**
  Un permiso en este sistema no es un sí o un no: tiene **nivel** —consultar, editar, crear o borrar—. El servidor lo sabe y lo exige; la interfaz no lo recibía, porque la consulta que le dice quién es el usuario resolvía el nivel y luego **lo tiraba**, enviando solo la lista de nombres. Resultado: a alguien con permiso de solo consulta se le ofrecían botones de Cerrar, Transferir, Excluir o Firmar acta, y al pulsarlos recibía un "no tienes permiso". Las dos revisiones de experiencia de uso, hechas por separado y sin verse, llegaron a esta misma conclusión y **ambas se negaron a arreglarlo pantalla por pantalla**: el problema no estaba en las pantallas sino en lo que el servidor les cuenta.
  Se arregló en las dos mitades. El servidor ahora envía también el nivel de cada permiso, **sin quitar nada de lo que ya enviaba**, para que nada de lo existente se rompa ni haga falta actualizar todo a la vez. Y la interfaz aprende a preguntar por nivel, con una regla deliberadamente estricta: si el nivel no llega —una sesión abierta antes de este cambio, por ejemplo— la respuesta es **no**, nunca "no lo sé, lo permito".
  Al aplicarlo se revisaron **todos** los botones que escriben, uno por uno, contra lo que su servicio exige de verdad. Eso corrigió la premisa del propio hallazgo: la mayoría de las operaciones solo piden el nivel más bajo, así que **solo cuatro pantallas** necesitaban la restricción alta. Exigir de más habría sido el mismo error al revés, escondiendo funciones a quien sí puede usarlas, y además en silencio. Se anotó, sin tocarlo, que en la bandeja ocurre justo eso: la interfaz pide un permiso que el servidor no exige, porque allí quien puede actuar se decide por tener el trámite asignado, no por permiso.
  *Por qué*: la interfaz debe ser espejo fiel de lo que el servidor autoriza; ofrecer lo que va a ser rechazado es un fallo de funcionamiento, no de apariencia.
  *Impacto*: sin cambios en la base de datos. 199 pruebas del servicio de autenticación y 1370 del frontend, todas en verde, incluida una que reproduce exactamente el caso reportado.

- `fix(frontend)`: **Accesibilidad: contraste insuficiente, tablas inalcanzables sin ratón, errores que no se decían en su campo y sugerencias de IA que no se anunciaban (segundo lote de la revisión de experiencia de uso)**
  Cuatro problemas que dejaban fuera a parte de los usuarios. **Uno**: texto blanco sobre el verde de marca no alcanzaba el contraste mínimo exigido, y no en un efecto pasajero sino en estados permanentes —el número del paso actual en los asistentes de doble factor, credencial de firma y firma; el turno activo de la cadena de firma; los filtros activos de borradores y envíos—. En varios sitios estaba **al revés**: el tono legible se usaba solo al pasar el ratón. **Dos**: las tablas anchas cortaban por la derecha justo las columnas de Estado y Acciones, y las que sí se desplazaban no podían desplazarse con el teclado, así que quien no usa ratón no llegaba a ellas. Se corrigió en los dos componentes compartidos de tabla, con lo que el arreglo alcanza a la mayoría de pantallas. **Tres**: en el perfil, los siete mensajes de error de validación aparecían solo como aviso flotante y nunca junto al campo que fallaba — precisamente en la pantalla que gobierna la credencial de firma electrónica; ahora se anuncian en el campo, y los códigos de un solo uso abren el teclado numérico y se autocompletan. **Cuatro**: las sugerencias de la IA aparecían sin avisar a quien usa lector de pantalla; ahora se anuncian, sin tocar el contenido, que ya cumplía la exigencia normativa de citar fuentes y advertir que fue generado por IA.
  Dos precisiones honestas: los contenedores de tabla que no se modificaron envuelven un componente cuyo desplazamiento ya es alcanzable, y añadirles otro punto de parada del teclado habría creado un destino vacío; y el hallazgo original mencionaba cuatro tablas con una celda de más, que un barrido de las 28 tablas del proyecto **no pudo reproducir** — se deja dicho en vez de forzar la cifra.
  *Por qué*: una columna de acciones que no se ve en el móvil, o un error que un lector de pantalla no puede localizar, no son detalles de acabado: impiden completar la tarea.
  *Impacto*: 1348 pruebas en verde. Queda deliberadamente fuera el código de recuperación del doble factor: es una carencia del servidor y no debe disimularse en la interfaz.

- `fix(frontend)`: **La carga masiva no informaba de su resultado, tres tarjetas de administración llevaban a "sin permiso" y dos pantallas no estaban enlazadas (revisión de experiencia de uso, primer lote)**
  Las ~20 pantallas construidas para poner en la interfaz todo lo que el backend ya exponía se implementaron sin pasar por la revisión de experiencia de uso y accesibilidad, que es el último paso del proceso del proyecto. La revisión se hizo después y encontró tres problemas **de funcionamiento**, no de apariencia; este es su primer lote de corrección. **Uno**: la carga masiva de documentos y expedientes nunca informaba de su resultado, por dos fallos que se sumaban — el panel de progreso no se refrescaba, y la pantalla consultaba el progreso en una dirección que había dejado de existir cuando se corrigió el ruteo. Un administrador podía cargar mil radicados y no saber si se crearon o si falló todo. **Dos**: tres tarjetas del panel de administración anunciaban un permiso distinto del que realmente exige el servidor, así que llevaban a una pantalla de "no tienes permiso"; el caso de Interoperabilidad era doble, porque además ocultaba la pantalla entera a un radicador que sí podía usar parte de ella. Se revisaron las **dieciséis** tarjetas, no solo las tres detectadas, porque este mismo patrón ya había reaparecido cuatro veces. **Tres**: dos pantallas terminadas —preservación digital y claves de firma— no figuraban en el índice de administración, que se presenta como el mapa completo, y solo se alcanzaban escribiendo la dirección a mano.
  Se corrigió también la causa de fondo del primer fallo: varios comentarios del código afirmaban cosas que habían dejado de ser ciertas (que la puerta de enlace no enrutaba la carga masiva, que los servicios de metadatos no pedían permiso). Es la tercera vez en esta versión que un comentario desactualizado provoca un defecto real, así que se borraron en lugar de dejarlos junto al arreglo, y se añadieron pruebas que fijan la dirección exacta que cada pantalla pide al servidor, para que un cambio de ruta no vuelva a romperla en silencio.
  *Por qué*: una pantalla dada por terminada y documentada como completa tenía su función principal inoperante; construir la interfaz descubre las capacidades, pero solo la revisión descubre la interfaz.
  *Impacto*: la carga masiva informa de su resultado; el panel de administración deja de ofrecer lo que negará y deja de esconder lo que autoriza; dos pantallas dejan de ser invisibles. 1339 pruebas automáticas del frontend en verde.

- `fix(repo)`: **Cuatro archivos de la pantalla de interoperabilidad nunca llegaron a guardarse en el repositorio**
  Una regla de exclusión pensada para una carpeta concreta de la raíz del proyecto estaba escrita sin anclar, de modo que se aplicaba a **cualquier** carpeta con ese nombre en cualquier parte del árbol. Coincidió con la carpeta que contiene los archivos de carga masiva de documentos, así que quedaron fuera del registro histórico pese a estar documentados como entregados: en la copia oficial del proyecto, la pantalla llamaba a direcciones que allí no existían. Corregido anclando la regla a la raíz.
  *Por qué*: apareció al reejecutar el trabajo sobre una copia limpia; la revisión anterior no podía verlo, porque lee los archivos de trabajo y allí sí estaban.
  *Impacto*: ninguno en el comportamiento de la aplicación. La lección es que este tipo de exclusión falla **en silencio**: el archivo simplemente no aparece como pendiente de guardar.

### Seguridad

- `fix(signature)`: **Al pedir firmas sobre un documento, era quien hacía la petición quien decidía qué nivel de seguridad se le aplicaba**
  Cuando se abre una cadena de firmas sobre un documento, el sistema anota su nivel de seguridad para saber a quién puede mostrarla después. Ese nivel debía leerse del documento; en la práctica se leía solo si el tipo de objeto venía escrito exactamente como «radicado». Escribirlo con mayúscula inicial, o declararlo como «borrador» apuntando al mismo documento, hacía que el sistema aceptara el nivel que indicara la propia petición. Y aun escribiéndolo bien, si el identificador no correspondía a ningún documento real, también se volvía al valor declarado. En los tres casos, el control que impide que alguien sin habilitación vea el trámite se comprobaba contra un nivel inventado.
  Al corregirlo apareció una cuarta vía por el mismo mecanismo, en la firma personal, donde ese campo no se puede acotar porque por ahí pasan también las actas institucionales. Eso obligó a la corrección de fondo, que es la que vale la pena retener: **el sistema ya no pregunta de qué tipo dice ser el objeto para decidir si comprueba su nivel**. Busca el identificador entre los documentos y expedientes registrados y, si lo encuentra, manda el nivel real, se haya declarado lo que se haya declarado.
  De camino se cerró el mismo agujero en los expedientes, que también tienen nivel propio y también lo estaba declarando el cliente. Y se conservó a propósito el nivel declarado para borradores y documentos sin registrar —ahí no hay nada que consultar y el autor es la única fuente—, con una prueba dedicada: apretar de más habría sido el mismo error al revés.
  *Por qué*: el nivel de seguridad de un documento no puede depender de cómo alguien escriba una palabra en su petición.
  *Impacto*: sin cambios de esquema. Las pruebas del servicio de firma pasan de 295 a 300; las dos que comprueban que el tipo declarado ya no influye están verificadas volviendo a introducir el fallo a propósito.

- `fix(auth)`: **El registro de auditoría dejaba ver documentos clasificados a administradores sin habilitación para conocerlos**
  Todo el sistema impide que alguien sin la habilitación de seguridad adecuada vea un documento clasificado: la búsqueda lo oculta, el expediente lo excluye, la descarga lo niega. La pantalla de auditoría era la excepción: bastaba con ser administrador para leer allí el número de radicado, quién actuó, qué hizo y cuándo, de todo lo que el resto del sistema ocultaba. La confusión de fondo es fácil de cometer y conviene nombrarla: **administrar la entidad no es lo mismo que estar autorizado a conocer sus asuntos reservados**. Son dos cosas distintas y el sistema las tenía mezcladas en un solo permiso.
  Ahora el listado se recorta por la habilitación de quien consulta, y de tres maneras deliberadas. **Se recorta también el total**, no solo la página: si el sistema dijera «hay 412 asientos» y mostrara 9, el número sería por sí mismo una filtración de cuánto hay clasificado. **Lo que no se puede clasificar queda fuera**: si un asiento apunta a un documento que ya no existe, el sistema no lo da por público — no puede comprobar su nivel, así que no lo enseña. Y **quien sí tiene la habilitación máxima ve el registro íntegro**: recortarlo ahí no protegería nada y destruiría el valor probatorio que justifica que exista.
  El filtro está en el servidor, no en el navegador. La pantalla se había negado en su día a esconder los datos en el cliente, con el argumento correcto: si el dato llegó al navegador, ya salió del sistema, y taparlo visualmente solo produce una falsa sensación de seguridad.
  La verificación de que la cadena de auditoría no ha sido manipulada **no** se recorta, a propósito: solo demuestra algo si se recalcula entera, y su respuesta no dice nada de ningún documento — únicamente si la cadena está intacta.
  Una decisión de fondo merece explicarse porque fue el error del primer intento: el recorte **enumera lo que sí se puede ver**, no lo que hay que esconder. Al principio se hizo al revés —una lista de cinco cosas a ocultar— y parecía equivalente. No lo es: el registro de auditoría lo escriben once componentes distintos con unos cuarenta tipos de asiento, y varios que no *son* un documento llevan su número y su asunto dentro. Un fallo de entrega de correo, por ejemplo, guarda el mensaje completo con el número de radicado incluido; una importación masiva guarda la lista literal de los números importados; un anexo guarda su nombre de fichero, que en la práctica suele ser el asunto. Con la lista al revés, cada tipo nuevo —y cada componente nuevo— nacería visible y nadie se enteraría. Ahora nace oculto, y si algo legítimo falta, se ve en pantalla y se corrige. Se prefiere el error que se nota.
  *Por qué*: era un hallazgo abierto y ya declarado por escrito como limitación conocida de la pantalla; cerrarlo era lo que faltaba.
  *Impacto*: sin cambios de esquema ni de formato de respuesta. Las pruebas del servicio pasan de 201 a 218, con dos nuevas contra base de datos real que comprueban el recorte subiendo y bajando la habilitación sobre los mismos datos. Los dos errores del camino los encontraron controles, no lecturas: la comprobación automática descubrió que en una entidad sin tablas de documentos el recorte se omitía por completo —dejaba pasar todo justo cuando debía ser más estricto—, y una revisión de seguridad independiente descubrió que la lista estaba invertida. Ambos corregidos y comprobados volviendo a introducir el fallo a propósito para verificar que la prueba lo detecta.
  *Queda anotado*: el número de orden de cada asiento va en la respuesta y es correlativo, así que los saltos permiten deducir **cuántos** asientos se ocultaron y cuándo, aunque no de qué tratan. No se cierra porque ese mismo número es el que identifica dónde se rompería la cadena si alguien la manipulara. Y se detectó, de paso, un problema **en otro servicio**, sin relación con esta pantalla: al crear una cadena de firma, el tipo de objeto es texto libre, así que escribiéndolo con una mayúscula distinta el sistema deja de consultar el nivel de seguridad real y acepta el que declare quien hace la petición. Se reporta, no se corrige aquí.

- `fix(workflow)`: **Cierre de hallazgos bloqueantes en workflow-service (RF-SEG-03/08; E05)**
  `PATCH /{step_id}/complete` era la única mutación sin identidad del llamante — cualquier tenedor de `PERM_TRAMITAR` podía cerrar el paso activo de otra dependencia, y la traza acusaba a `assigned_to` en vez de a quien ejecutó el cierre.
  *Impacto*: autorización y mutación ancladas en el mismo `UPDATE` (sin ventana TOCTOU); `/evaluate(/batch)` gana el gate `USUA_PERM_ADMIN` que le faltaba. Deuda declarada: `devolver`/`execute_transaction`/`reassign_cascade` siguen sin asiento en `audit_log`.

- `fix(notification)`: **Sellado de `/send` + autorización de webhooks y del historial (RF-SEG-03; D-02; E11)**
  `POST /notifications/send` es servicio-a-servicio (signature-service despacha ahí los OTP de 2FA) pero el gateway lo exponía sin gate — un JWT válido de cualquier tenant enviaba correo arbitrario con la identidad SMTP institucional.
  *Impacto*: sellado con `X-Internal-Token`; `webhooks.py` y `/history` ganan `USUA_PERM_ADMIN` (eran canal de exfiltración de eventos del tenant, respectivamente).

- `fix(workflow)`: **Enforcement RBAC de las transacciones de trámite y las rutas de distribución (RF-SEG-03; E05)**
  `POST /{id}/transactions` ignoraba `transaction_types.requires_permission` — la migración lo daba por "delegado al gateway", pero el gateway solo autentica, nunca autoriza.
  *Impacto*: cualquier usuario autenticado podía ejecutar `anular`/`cerrar_exp`/`vobo` sin el permiso; ahora el permiso por-tipo se aplica en la capa de servicio, fail-closed.

- `fix(document)`: **Cierre del no-read-up en firmas, anulación, respuesta vinculada y envíos (RF-SEG-08; Ley 594/2000 art. 28)**
  El mismo patrón de fuga se repetía en 9 endpoints. `crear_respuesta` copiaba el asunto de un antecedente reservado sin verificar clearance; las rutas de firma no tenían ningún gate y `verify` era un oráculo de contenido.
  *Impacto*: en las escrituras el filtro va dentro del `INSERT` (`WHERE EXISTS`), no como consulta previa, para que comprobación y escritura sean atómicas.

- `fix(document)`: **No-read-up en disposición y PATCH de radicados (RF-SEG-08; RF-MET-08; Ley 594/2000 arts. 24 y 28)**
  `GET`/`PATCH /documents/{id}/disposition` y `PATCH /documents/{id}` se gateaban solo por permiso, no por clearance — permitía fijar la disposición final de un radicado clasificado sin poder leerlo.
  *Impacto*: `max_clearance` pasa a ser obligatorio en las firmas (sin default "sin restricción", el modo de fallo que originó la fuga).

- `fix(archive)`: **Atribución del `audit_log` de la renovación WORM al principal de sistema (E10; RF-SEG-08)**
  Los 6 asientos de la renovación de retención usaban `actor=None` — inconsistente con los asientos de empaquetado/protección que ya usan `SYSTEM_RECONCILER_ID`. Corregido para los 6, acotado solo al eje de renovación.

- `fix(archive)` + `fix(storage)`: **No-read-up en la consulta de eventos de preservación por expediente (E10 Increment C; RF-SEG-08)**
  `GET /api/v1/preservacion/eventos?object_ref={id}` devolvía eventos PREMIS gateado solo por permiso, no por clearance.
  *Impacto*: archive media con no-read-up y 404 neutro; storage sella el endpoint interno con `X-Internal-Token`.

- `fix(archive)` + `fix(storage)`: **No-read-up en la descarga del AIP de preservación (E10 Increment C; RF-SEG-08; Ley 594/2000 art. 22)**
  `GET /api/v1/preservacion/aip/{id}` devolvía el inventario completo (nombres/SHA/tamaños) gateado solo por permiso.
  *Impacto*: archive media con no-read-up y 404 neutro en los tres caminos de denegación; storage sella `get_aip`.

- `fix(workflow)`: **CRUD de reglas de enrutamiento automático sin autorización alguna (RF-SEG-03; E05)**
  `rules.py` no tenía `require_permission` en ninguna ruta — cualquier usuario del tenant podía crear/editar/borrar las reglas que deciden a qué dependencia se asigna la correspondencia entrante.
  *Impacto*: `USUA_PERM_ADMIN` en los cinco endpoints CRUD; `/evaluate(/batch)` queda de solo-lectura sin gate (el motor real las invoca en proceso, no vía HTTP).

- `fix(document)` + `fix(storage)` + `fix(gateway)`: **No-read-up en la descarga de anexos (RF-SEG-08; Ley 1712/2014; D-02)**
  La descarga emitía una presigned URL sin chequeo de clearance — y una presigned salta la auth si se reenvía.
  *Impacto*: document-service ahora media toda descarga con streaming de bytes (nunca presigned al cliente); storage sellado, solo alcanzable vía `X-Internal-Token`.

- `fix(workflow)`: **No-read-up en la bandeja + tenencia en `rollback` + cierre de oráculo en `devolver` (RF-SEG-08)**
  Tres hallazgos: (1) los listados de bandeja no filtraban por clearance; (2) `rollback` mutaba sin verificar tenencia del paso; (3) `devolver` distinguía 403 de 404 según el caso, actuando como oráculo.
  *Impacto*: subconsulta correlacionada de clearance compartida por COUNT y SELECT; `rollback` exige `assigned_to == actor`; ambos casos de `devolver` devuelven el mismo 404.

- `fix(document)`: **No-read-up en las Salidas vinculadas — `GET /documents/{id}/respuestas` (RF-SEG-08; Ley 1712/2014)**
  `listar_respuestas` devolvía las Salidas vinculadas a un antecedente sin filtro de clearance ni resolución del llamante, violando el invariante del servicio. Hueco hallado incidentalmente al revisar la traza de lecturas.

- `fix(archive)`: **Ciclo de vida de transferencias (E12) — carrera TOCTOU letal cerrada + los cuatro actos de custodia con asiento e identidad unívoca (RF-SEG-08; Ac. AGN 001/2024 Tít. 4.4)**
  El defecto más grave (H-A): `update_estado` mutaba sin guarda de estado — dos llamadas concurrentes (`recibir`+`rechazar`) podían comprometer ambas, dejando un expediente `rechazada` y a la vez congelado `transferred`, un estado jurídicamente imposible en un registro indeleble.
  *Impacto*: guarda de estado esperado en el `UPDATE` serializa la transición; se añade re-verificación de fixity al recibir (H-G, bloqueante de conformidad) y `motivo` obligatorio al rechazar. **Cambios de API**: `rechazar` exige `motivo`; `recibir` gana `409 fixity_mismatch`. Con esto el ciclo de transferencias queda **CONFORME** con el Ac. AGN 001/2024 Tít. 4.4.

### Corregido

- `fix(api-gateway,document,archive)`: **Carga masiva inalcanzable — falta el ruteo en la puerta de enlace y el contrato de estado era ambiguo entre servicios**
  Cierra el pendiente que dejó la pantalla de interoperabilidad: la puerta de enlace central no tenía registrada la ruta `/api/v1/batch/`, así que la carga masiva de documentos y de expedientes devolvía siempre "ruta no encontrada". Al investigarlo apareció un problema más de fondo: los dos servicios que atienden la carga masiva usan la misma ruta base, y la consulta de estado del trabajo (`.../batch/{id}/status`) tenía **la forma exacta** en ambos — no hay manera de que la puerta de enlace adivine, solo por el identificador del trabajo, a cuál de los dos preguntar. La solución fue anidar la consulta de estado bajo el tipo de trabajo (documentos o expedientes), igual que ya se hacía al crearlo, para que la decisión sea inequívoca. De paso, la carga masiva de documentos quedó exigiendo el mismo permiso que crear un documento uno por uno — antes no exigía ninguno, a diferencia de la de expedientes.
  *Por qué*: sin esta ruta la pantalla de carga masiva, ya construida, no podía funcionar; el contrato compartido entre los dos servicios había que desambiguarlo en el origen, no parcharlo por fuera.
  *Impacto*: las URLs de consulta de estado de la carga masiva cambian de forma —y la pantalla que las consume se quedó con la forma antigua, corregido después en la entrada de la revisión de experiencia de uso—; la carga masiva de documentos gana control de permisos. Lo que esta entrada dejó pendiente —la carga masiva de documentos sin registro en la bitácora de auditoría— quedó **cerrado** por la primera entrada de esta versión.

- `fix(frontend)`: **Barrido de accesibilidad tras la auditoría del rediseño v1.0 — 27 hallazgos Alto/Crítico (E22, WCAG 2.1 AA)**
  Tres familias: contraste AA (componentes migran a `--op-primary-dark`, 6.56:1), objetivos táctiles ≥44px, y semántica/foco (recuperación de foco visible, confirmación explícita antes de firmar en lote). La mayoría eran defectos preexistentes, solo destapados al calcular los ratios de contraste por primera vez.

- `fix(frontend)`: **Bucle de redirección entre Keycloak y `/login` (E22)**
  Cuando Keycloak devolvía `/login?error=…`, el `load` reiniciaba PKCE sin mirar el parámetro y volvía a mandar al usuario a Keycloak — bucle infinito. Ahora lee `?error=` antes de reiniciar el flujo y muestra un mensaje mapeado desde una tabla cerrada en español (nunca el string crudo de Keycloak).

- `fix(frontend)`: **Comentarios `//` en `<style lang="scss">` rompían la compilación SSR cruda (E22)**
  El test de seguridad SSR compila sin preprocesar SCSS y el parser CSS no entiende `//`; `Modal.svelte` y `Drawer.svelte` quedaban fuera de esa garantía. Cambiados a `/* */`.

- `fix(migrations)`: **Guards de idempotencia sin filtrar por schema rompían la provisión del 2º tenant (multi-tenant)**
  Los guards `IF NOT EXISTS` sobre nombres de constraint/columna comprobaban sin `current_schema()`; como esos nombres se repiten entre tenants, el guard omitía el objeto tras el primer tenant.
  *Impacto*: severidad crítica en las migraciones de DLQ (workflow/notification/signature) — abortaban la provisión de un segundo tenant. Todos los guards acotados a `current_schema()` + backfill schema-scoped para tenants ya provisionados.

- `fix(knowledge)`: **`POST /api/v1/knowledge/search` daba 500 contra Postgres real (E21)**
  El pool de asyncpg no registraba codec `jsonb`, así que `metadata`/`acl` se leían como texto crudo y la validación Pydantic reventaba. Invisible en unit tests (mockean la conexión); descubierto al ejercitar el RAG real. Fix: codec `jsonb` en el `init` del pool, con encode como texto para no doble-encodear las escrituras existentes.

- `fix(scripts)`: **`init_tenant.py` provisionaba tenants incompletos y abortaba a media provisión (E14/E21)**
  Dos defectos: (a) `TENANT_MIGRATIONS` omitía `knowledge-service` y `tenant-service` sin dar error — el tenant quedaba "bien" y el servicio fallaba en runtime (PINAR inservible); (b) `_migrations.filename` es único sobre el nombre desnudo, y varios servicios numeran desde 001 — colisión → `UniqueViolationError` a media provisión.
  *Impacto*: la identidad de migración pasa a `servicio/archivo`, con backfill para tenants ya provisionados con claves desnudas.

- `fix(knowledge)`: **2 tests de contención de inyección de prompt estaban en rojo desde su propio commit (E21 Inc.3)**
  El escape `html.escape` funcionaba, pero los tests buscaban la cadena verbatim y sus cargas de prueba llevaban apóstrofos que el escape transforma. `git log -S` confirma que código y test entraron en el mismo commit sin correr nunca en verde (oculto porque la imagen no compilaba).

- `fix(knowledge)`: **El `requirements.lock` contradecía su propia declaración y hacía irreconstruible el servicio**
  `requirements.txt` declaraba "CPU-only suficiente" pero el lock resolvió la variante CUDA de torch (~5 GB de wheels `nvidia-*`): knowledge-service era el único servicio que no compilaba (agotaba disco).
  *Impacto*: la dependencia pesada se separa a `requirements-ml.txt` con índice CPU; `Dockerfile` gana `ARG WITH_ML` (default 1, sin cambio para un despliegue local; `WITH_ML=0` da una imagen de 212 MB).

- `fix(storage)`: **El gate de disposición deja de postergar la eliminación del contenido por la retención del acta de transferencia (E10; Ley 594/2000 arts. 24-26)**
  El gate bloqueaba la purga de todo el expediente mientras cualquier objeto WORM tuviera retención futura (all-or-nothing); como el acta ancla en `firmado_at` (posterior al cierre), la eliminación mandada por la TRD quedaba postergada indebidamente.
  *Impacto*: nueva taxonomía CONTENIDO (bloquea) vs INSTRUMENTO DE CONTROL (el acta, sobrevive sin bloquear); `legal_hold` sigue siendo bloqueo incondicional.

- `fix(frontend)`: **`radicar` usa el `SchemaField` compartido — cierra la degradación de campos `enum`/`boolean` a texto libre (E22/E03)**
  El mapeo inline de metadatos solo cubría number/date; un campo `enum` o `boolean` degradaba a texto libre, permitiendo un valor inválido que el backend rechazaba con 422. Reemplazado por el componente `SchemaField` compartido con el Form Builder de expediente (sin tocarlo).

- `fix(frontend)`: **Sincroniza la descarga de anexos del workspace de expediente con el nuevo contrato mediado por document-service (D-05, D-02, RF-SEG-08)**
  El backend cerró la descarga directa a storage-service (URL presignada sin clearance) y la reemplazó por un endpoint mediado con streaming y no-read-up. El proxy BFF se reestructura con allowlist de UUID e inyección de `X-User-Id` fail-closed.
  *Impacto*: nota abierta — `ExpedienteRadicadoResponse` aún no incluye `anexos` por radicado; el panel queda sin datos hasta que el backend lo exponga.

- `fix(frontend)`: **El diálogo de confirmación de radicación nunca era visible en producción — faltaba la prop `open` del `Modal`**
  `radicar` montaba el `Modal` sin la prop `open`, que gatea todo su markup — el diálogo nunca renderizaba y no había forma de disparar el submit desde la UI. Defecto crítico preexistente, hallado al migrar `radicar` a `SchemaField`.

### Cambiado

- `chore(compose)`: **knowledge-service configurable para máquinas sin capacidad de modelo local (E21)**
  El compose de desarrollo puede sobreescribir por entorno a `EMBEDDING_PROVIDER=stub` + `AI_PROVIDER=ollama_cloud`.
  *Impacto*: los defaults de `config.py` no se tocan — siguen siendo los soberanos `local_st`+`ollama_local`; `stub` no es un vector falso, es recuperación léxica real (hashing vectorizer), sin migración.

### Añadido

- `feat(archive)` + `feat(storage)`: **Cableado WORM/Object-Lock del acta de transferencia firmada (E10 Inc.2; Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/16/26)**
  El índice ya quedaba protegido (Inc.1); el acta de entrega firmada (sello XAdES) seguía borrable pese al sello — misma brecha de custodia sobre el instrumento que evidencia el traslado del FUID.
  *Impacto*: retención anclada en `firmado_at` del acta (no en el cierre del expediente) porque el acta puede firmarse años después en transferencias secundarias; generalización de storage `proteger-indice`→`proteger-artefacto` por tipo.

- `feat(archive)` + `feat(storage)`: **Cableado WORM/Object-Lock del índice electrónico firmado al cierre (E10 Inc.1; Ac. AGN 001/2024 art. 4.3.2.6)**
  La capacidad WORM de storage existía pero nadie la disparaba — el índice firmado quedaba borrable pese al sello XAdES. Ahora archive lo protege inmutable tras firmarlo (disparo síncrono best-effort + reconciliación).
  *Impacto*: retención derivada de la TRD, fail-closed sin TRD; advisory lock de sesión serializa la carrera entre el disparo síncrono y la reconciliación. Alcance honesto: Object-Lock de MinIO en dev, no repositorio certificado.

- `feat(auth)`: **Purga del material privado de las credenciales de firma revocadas (E17/F4; Ley 527/1999 art. 7)**
  Una privada de firma revocada no tiene uso operativo; retenerla cifrada es un pasivo de seguridad. Tras una ventana configurable (default 30 días) un job de ops anula el material privado, conservando todo lo público.
  *Impacto*: desbloquea retiradas de KEK que las credenciales revocadas mantenían ancladas; es minimización de datos, no cambia el tier legal (sigue art. 7, no acreditado).

- `feat(auth)`: **Rotación de la KEK de custodia de las claves de firma personales — llavero versionado + re-wrap de ops (E17/F4; Ley 527/1999 art. 7)**
  No había forma de rotar la KEK maestra sin re-enrolar a todos los usuarios; ahora es un llavero versionado, con un job de ops que re-cifra (nunca vía HTTP) con round-trip verify antes de persistir.
  *Impacto*: higiene de custodia — no cambia el tier legal (sin sole-control, art. 7). Deuda: HSM/KMS externo como paso hacia art. 28.

- `feat(archive)`: **Firma electrónica personal PKI del acta de entrega — elaborador, cierra el trío de responsables del FUID (E17/F4; Ac. AGN 042/2002 art. 7)**
  El elaborador ("Elaborado por") firma también el acta, como firmante aditivo — no parte del traspaso (esa función la cumplen receptor/remitente de los incrementos anteriores).
  *Impacto*: `firmada_completa` permanece intacta (5 valores); la firma del elaborador se reporta en un campo booleano ortogonal, sin inflar el veredicto de completitud.

- `feat(archive)`: **Firma electrónica personal PKI del acta de entrega — remitente + `firmada_completa` bilateral (E17/F4; Ac. AGN 001/2024 Anexo FUID)**
  Solo el receptor podía firmar; ahora quien envió firma también, en cualquier orden, completando "Entregado por"/"Recibido por" del FUID.
  *Impacto*: el rol se deriva de la identidad congelada, nunca del cliente (403 `firmante_no_es_parte` para un tercero); el auto-traslado (misma persona en ambos roles) se permite y se señala.

- `feat(archive)` + `feat(signature)`: **Firma electrónica personal PKI del acta de entrega — receptor (E17/F4; Ac. AGN 001/2024 Anexo FUID; Ley 527/1999 art. 7)**
  El acta ya se sellaba institucionalmente, pero ninguna persona la firmaba con su certificado; ahora quien recibe firma como paso explícito posterior a `recibir()`, con TOTP.
  *Impacto*: se exige que el firmante sea el receptor real (403 antes de quemar el TOTP) — cierra un bloqueante hallado en auditoría donde un impostor podía bloquear al receptor legítimo vía la restricción de unicidad.

- `feat(signature)`: **Gate `firma_personal_require_xades` por nivel de seguridad — Inc.3 del epic (E17/F4; Ley 527/1999 art. 7)**
  Cierra un fail-open silencioso: un certificado ausente o auth-service caído degradaban a HMAC nativo incluso para actos RESERVADOS/CLASIFICADOS. Nuevo flag de tres modos (`off`/`clasificados`/`todos`) que corre antes de consumir cualquier factor 2FA.
  *Impacto*: rollout — fijar `clasificados` en producción solo tras completar el enrolamiento offline de firmantes con clearance≥2.

- `feat(auth)` + `feat(signature)`: **Revocación en línea de la firma personal PKI + anclaje de la sub-CA de usuarios en `/verify` — Inc.2 del epic (E17/F4; Ley 527/1999 art. 7)**
  Revocar hace que `/verify` reporte `valida=false` al instante, incluidas las firmas emitidas antes de la revocación; se valida además que la sub-CA de usuarios esté anclada a la CA institucional.
  *Impacto*: limitación conocida — la revocación es por estado actual, sin sellar la fecha de firma (grace-period ETSI requeriría TSA acreditada).

- `feat(signature)` + `feat(auth)`: **Firma electrónica personal PKI por-usuario (XAdES-B/T), custodia servidor — Inc.1 del epic (E06/E17 F4; Ley 527/1999 art. 7)**
  La firma personal usaba un HMAC opaco con un secreto único de servidor; ahora cada firmante con clave activa firma con su propio certificado (sub-CA local de dev).
  *Impacto*: split firma-remota — la privada nunca sale de auth-service; es la interfaz exacta de un HSM, así que conmutar a firma cualificada no toca signature-service. Sigue siendo art. 7, no acreditado, sin sole-control.

- `feat(archive)` + `feat(signature)`: **Firma XAdES del acta de entrega de transferencias (H-H, E06/F4; Ac. AGN 001/2024 Anexo FUID)**
  El acta ya tenía tres garantías (trigger de inmutabilidad, fixity, anclaje en la cadena de hash) pero ninguna firma XAdES. Ahora recibe un sello XAdES-B/T/LT/LTA + OCSP institucional enveloped, best-effort con reconciliación.
  *Impacto*: el sello atribuye a los tres responsables por identidad autenticada, pero no son las firmas personales de cada uno (esa es la deuda que cierran los incrementos posteriores de E17).

- `feat(archive)` + `feat(signature)`: **Reconciliación del sellado del índice electrónico al cierre (E15/E06 Inc.8; Ac. AGN 001/2024 art. 4.3.2.4)**
  El sellado del índice al cerrar es best-effort post-commit; un fallo dejaba el índice `pendiente_firma` permanentemente. Se añade intento síncrono acotado + job de reconciliación con backoff + endpoint de visibilidad de pendientes.
  *Impacto*: cierra el modo de fallo permanente, pero no garantiza firma síncrona al instante del cierre — con el sello ausente el índice sigue quedando pendiente hasta intervención humana.

- `feat(signature)`: **OCSP stapled en la firma XAdES-LT del índice (E06 Inc.7, ADR-016 addendum; RFC 6960; ETSI EN 319 132)**
  La revocación se verificaba solo por CRL; ahora, opt-in, se añade una respuesta OCSP incrustada firmada por un responder delegado emitido offline por la CA del tenant (la clave de la CA nunca entra al servicio).
  *Impacto*: enriquece la evidencia, no sube la acreditación — sigue `acreditado=false`.

- `feat(archive)` + `feat(signature)`: **[duplicado] Reconciliación del sellado del índice electrónico al cierre (E15/E06 Inc.8)**
  Entrada repetida en el changelog original, idéntica a la anterior.

- `feat(signature)`: **[duplicado] OCSP stapled en la firma XAdES-LT del índice (E06 Inc.7)**
  Entrada repetida en el changelog original, idéntica a la anterior.

- `feat(document)` + `fix(frontend)`: **El panel de Anexos del expediente ya muestra datos — endpoint de listado gateado por clearance (RF-SEG-08)**
  El panel estaba vacío porque asumía un campo `anexos` en la respuesta de archive que nunca existió (los anexos viven en document-service). Nuevo endpoint dedicado con no-read-up; frontend con carga perezosa al expandir.

- `feat(mcp)` + `feat(gateway)`: **El asistente E18 gana la tool `buscar_conocimiento` — búsqueda semántica (E21) *as-the-user* con ACL (ADR-019/ADR-006)**
  El asistente puede hacer recuperación semántica sobre el knowledge-service auto-poblado; el clearance lo impone el knowledge-service desde el usuario real, así cada usuario ve solo fragmentos a su clearance.
  *Impacto*: el modelo no puede colar un `acl` propio — el input se filtra al esquema declarado antes de despachar.

- `feat(knowledge)` + `feat(document)`: **Purga-en-anulación del índice semántico (E21; RF-SEG-08; Ley 1712/2014)**
  Un radicado anulado seguía consultable en la búsqueda semántica. document-service emite un evento al anular; knowledge-service marca el chunk como purgado permanentemente y `search` lo excluye a cualquier clearance.

- `feat(knowledge)` + `feat(document)`: **Ingesta event-driven del índice semántico (E21, MVP) (ADR-006/010/021; RF-SEG-08)**
  El knowledge-service se auto-puebla consumiendo eventos de document-service, con ACL fail-closed (nivel ausente → CLASIFICADA) y un guard que bloquea material reservado hacia proveedores de embeddings externos.
  *Impacto*: invariante de monotonía bajo entrega at-least-once en cualquier orden — dos rondas adversariales de auditoría hallaron y cerraron dos fugas reales de reordenamiento.

- `feat(workflow)`: **Traza de auditoría de las lecturas de la bandeja de trámite y hoja de ruta (RF-BUS-10; Ley 1712/2014)**
  Última pieza del barrido RF-BUS-10 (tras archive y document). Traza agregada de la bandeja + traza por-radicado de la hoja de ruta y del trámite puntual. Cierra RF-BUS-10 para los tres servicios.

- `feat(document)`: **Traza de auditoría de las lecturas de radicados (RF-BUS-10; Ley 1712/2014)**
  document-service aplicaba no-read-up en las lecturas pero no auditaba ninguna — consultar un radicado reservado no dejaba rastro (hallazgo al revisar el asistente E18).
  *Impacto*: lectura individual y búsqueda agregada distinguidas; minimización de PII — nunca se registra el texto crudo de la búsqueda.

- `feat(mcp)` + `feat(gateway)`: **Asistente conversacional E18 — backend end-to-end (loop LLM Claude *as-the-user*, v1 solo-lectura) (ADR-019/ADR-020; RF-SEG-08)**
  El slice de frontend ya existía pero era no funcional. Nuevo endpoint que orquesta un loop manual sobre Claude: el modelo pide tools, el mcp-server las ejecuta re-propagando el token del usuario (as-the-user, cada tool revalida RBAC/clearance).
  *Impacto*: v1 es solo lectura por doble defensa (la tool de escritura se excluye del prompt y del despacho); se remedió un IDOR intra-tenant en la llave del historial de conversación.

- `feat(frontend)`: **Form Builder en la creación de expediente — campos dinámicos desde el `json_schema` de la plantilla de metadatos (E22/E03/ADR-007)**
  La creación de expediente enviaba `metadata: {}` vacío; las plantillas por serie existían en el backend pero el front no ofrecía sus campos. Nuevo mapeo JSON Schema → control compartido cliente/servidor.

- `feat(frontend)`: **Slice del asistente conversacional (E22/ADR-020) — UI de chat lista contra un contrato definido, backend E18 pendiente en ese momento**
  El asistente es full-stack pero su backend era scaffold; se construye el frontend completo, honesto sobre la falta de funcionalidad end-to-end (nunca respuesta simulada). Sanea el output del asistente server-side antes de cruzar al cliente.

- `feat(archive)`: **Origen/destino obligatorio + cotejado contra la ubicación real, condicionado a la tenencia física (E12/E17; Ley 594/2000 arts. 15/26, 23)**
  Origen/destino eran opcionales; un expediente con tenencia física localizada ahora los exige y coteja el origen contra la ubicación actual. Un expediente puramente electrónico queda exento (rama B).

- `feat(archive)`: **Integridad de la creación de transferencias — unicidad de transferencia activa (H-L) + coherencia origen/destino por tipo (H-M) (E12; Ley 594/2000 art. 23)**
  Nada impedía N transferencias solapadas sobre el mismo expediente, ni validaba que origen/destino fueran coherentes con el tipo de traslado (gestión→central→histórico).
  *Impacto*: índice único parcial serializa la concurrencia (409, nunca 500); origen/destino siguen opcionales por decisión — exigirlos acoplaría E12 a un E17 aún parcial.

- `feat(archive)`: **Inmutabilidad forzada por el motor del acta de entrega + tamper-evidence anclada en la hash-chain (E12; Ley 594/2000 art. 16)**
  El acta congelada era solo inmutable procedimentalmente (sin ruta de UPDATE en el código), pero seguía mutable por SQL directo sin oposición del motor.
  *Impacto*: triggers append-only rechazan UPDATE/DELETE/TRUNCATE; el SHA del acta se ancla además en el asiento hash-encadenado, así un bypass del trigger se vuelve detectable.

- `feat(archive)`: **Congelar el FUID como acta de entrega inmutable al recibir la transferencia (E12 H-I; Ley 594/2000 arts. 15/26)**
  El FUID se generaba al vuelo y perdía valor de acta en cuanto el expediente se tocara. Ahora, al recibir, se congela un acta probatoria con el XML canónico y los tres responsables con sus fechas.
  *Impacto*: la inmutabilidad es procedimental + verificable por fixity en este incremento, no forzada por trigger de motor (eso llega en un incremento posterior).

- `feat(frontend)`: **Vista de listado de expedientes (E22 T-12) — reemplaza el único listado stub del frontend**
  Era la última vista de dominio sin implementar. Listado SSR puro sobre `GET /api/v1/expedientes/search` (ya filtra por clearance no-read-up); todo el estado vive en la URL, el `access_token` nunca llega al cliente.
  *Impacto*: `nivel_seguridad` deliberadamente no se muestra — toda fila visible ya está a-nivel-o-por-debajo del clearance, así que un badge no informa y para clearance alto sería un inventario de qué exfiltrar.

- `feat(archive)`: **Traza agregada de consulta de expedientes (RF-BUS-10) — una entrada de auditoría por búsqueda/listado, no por fila**
  El path de listado/búsqueda emitía cero traza, contra RF-BUS-10 y sobre-declarando conformidad. Ahora una entrada por consulta exitosa, con `q` minimizado (solo longitud/presencia, nunca el texto).
  *Impacto*: cubre solo listado/búsqueda de expedientes — la traza RF-BUS-10 global seguía pendiente en este punto.

- `test(frontend)` + `fix(frontend)`: **Cobertura de tests de UI + correcciones a11y/SSR en componentes overlay (E22)**
  El frontend funcional tenía 1 test unitario y 0 E2E. Infra montada (Vitest + Testing Library + jsdom + Playwright) destapó y corrigió bugs reales: guard SSR faltante, apilamiento incorrecto de overlays, race de restauración de foco.

- `feat(storage)`: **Validación PDF/A en la ingesta del AIP — contrato + stub honesto (E10 Preservación, Increment C2a, ADR-023 addendum)**
  El AIP no validaba el formato de los documentos; OAIS/PREMIS esperan validación en ingesta. Nuevo validador con stub honesto (`not_evaluated` por defecto, nunca finge un veredicto) — el validador real llega en C2b.

- `feat(storage)`: **Réplica best-effort del AIP a un segundo bucket WORM (E10 Preservación, Increment C1b, ADR-023 addendum)**
  OAIS exige múltiples copias. Al empaquetar el AIP, opt-in, se crea una réplica inmutable en un segundo bucket (local o cross-site vía endpoint secundario).
  *Impacto*: un fallo de réplica jamás revierte el AIP primario — es no-fatal por diseño.

- `fix(storage)`: **PREMIS v3 conformidad plena — el AIP valida contra el XSD oficial de la LoC, no contra un perfil propio (E10 Preservación, Increment C1a, ADR-023 addendum)**
  El AIP validaba contra un XSD local propio, que no prueba conformidad. Se vendoriza el `premis.xsd` oficial de la Library of Congress y el `<object>` emite `xsi:type="premis:file"` correcto.

- `feat(storage)`: **AIP real (BagIt RFC 8493) + PREMIS v3 subido al bucket WORM (E10 Preservación, Increment B, ADR-023 addendum)**
  El AIP era un stub textual en BD; ahora es un paquete OAIS real (Bag conforme RFC 8493 + manifiesto PREMIS v3), subido al bucket WORM con retención de la TRD.
  *Impacto*: remedió dos hallazgos Alta antes de merge — colisión de nombres que podía invalidar el Bag bajo WORM, y el endpoint `GET /aip` sin RBAC.

- `feat(storage)`: **WORM / Object-Lock real (MinIO) sobre el índice electrónico firmado + gate de disposición (E10 Preservación, Increment A, ADR-023)**
  El índice firmado solo tenía inmutabilidad lógica; un SGDEA exige inalterabilidad de infraestructura durante la retención. Nuevo bucket WORM por tenant con Object-Lock; el gate de disposición bloquea la purga bajo retención futura o legal hold.
  *Impacto*: primer RBAC en storage-service; modo COMPLIANCE por defecto con guard de arranque fail-closed.

- `feat(auth,signature)`: **2FA por TOTP (RFC 6238) en la firma personal — secreto en auth-service (AESGCM), elevando el OTP-correo (E06 Inc.6, ADR-016 addendum)**
  El segundo factor era OTP-correo (posesión de canal); TOTP añade posesión de dispositivo, sin infra externa. El secreto vive cifrado en auth-service; la regla "no desactivable para nivel≥2" se preserva.

- `feat(signature)`: **XAdES-LT/LTA del índice con CA local de desarrollo — material de validación a largo plazo, no acreditado (E06 Inc.5, ADR-016 addendum)**
  La firma llegaba a XAdES-T pero sin material de validación no sobrevive a la expiración del certificado. Escala a XAdES-LTA cuando el sello del tenant trae material de CA (la clave de la CA nunca se monta en ningún contenedor).
  *Impacto*: degrada observablemente a XAdES-T sin material CA; `acreditado` siempre false.

- `feat(archive)`: **Índice electrónico v4 "conforme completo" — dependencia productora + fecha de declaración + pista de auditoría + política de acceso (E15, ADR-022 addendum)**
  v3 era "conforme extendido" con 4 residuales sin fuente en archive; v4 los cierra a nivel de capacidad sin sobre-declarar: dependencia productora por documento, fecha de declaración, historial de acceso instrumentado y política de acceso portable en la cabecera.
  *Impacto*: `content_hash` v4 cubre el árbol completo canonicalizado (v3 solo hasheaba `<documentos>`); los índices v1-v3 firmados quedan inmutables.

- `feat(archive)`: **Índice electrónico v3 "conforme extendido" — orden documental estable + exclusión trazable + ACL + contexto (E15, ADR-022 addendum)**
  El `<orden>` era recalculado y se renumeraba al excluir, contra el principio de orden original. Nuevo `orden_documental` estable e inmutable; la exclusión pasa de DELETE físico a soft-delete trazable (`<documento excluido="true">`).
  *Impacto*: v3 no se declara "completo" — el disclaimer lista los 4 residuales que cierra v4.

- `feat(signature)`: **XAdES-T — sello de tiempo RFC 3161 del índice (E06 Inc.4, ADR-016 addendum)**
  El XAdES-B usaba `SigningTime` del reloj (no oponible); se inserta un `xades:SignatureTimeStamp` (token RFC 3161) firmado por una TSA local in-process, post-procesado como propiedad no firmada (no invalida el XAdES-B).
  *Impacto*: `/verify` valida el sello con pinning del fingerprint TSA; `acreditado=false` (TSA local sin ONAC) — fecha cierta verificable, no acreditada.

- `feat(archive)`: **Metadatos RT-15 del índice electrónico — perfil v2 (E15/E06 Inc.3, ADR-022)**
  El Acuerdo AGN 001/2024 art. 4.3.2.3 (RT-15) exige por documento formato, tamaño y foliación, que el índice omitía. Nuevo perfil v2 que los emite (omitiendo el elemento si el dato es nulo, sin romper compatibilidad con v1 firmado).
  *Impacto*: v2 se etiqueta "ampliado (RT-15 parcial)", no "conforme completo" — aún faltan orden documental/ACL/contexto (resueltos por v3/v4).

### Seguridad

- `fix(workflow)`: **`GET /workflows/inbox`/`/pending` acotados por dependencia (RF-SEG-08, E05)**
  Los dos listados de bandeja no tenían ninguna dependencia de permiso — cualquier autenticado del tenant obtenía la carga de trabajo activa de toda la entidad.
  *Por qué*: ese listado sin acotar era además la primitiva de enumeración que hacía explotable el `step_id` ajeno cerrado en un fix anterior.
  *Impacto*: el alcance por defecto pasa a ser lo propio (asignado al actor o a su dependencia); ampliar el alcance ahora exige `PERM_TRAMITAR`/`USUA_PERM_ADMIN`, rechazado en vez de ignorado en silencio.

- `test(archive)`: **Tier de integración contra Postgres real — la atomicidad no es verificable con mocks**
  El carril unit mockea la conexión, así que `conn.transaction()` es un context manager de mentira: su ausencia no es observable. Los últimos incrementos de atomicidad estaban respaldados por tests que habrían pasado igual sin transacción real.
  *Impacto*: nuevo carril de integración (piloto en archive-service) con conexión asyncpg real, schema desechable y commits reales. 19/19 mutantes muertos al validar por mutación cada garantía.

- `fix(archive)`: **Cerrada la ruta alterna `PATCH /expedientes/{id}` que transicionaba el ciclo de vida sin dejar rastro (Ley 594/2000 arts. 22/24)**
  `ExpedienteUpdate.status` ejecutaba las mismas transiciones que `close`/`transfer` pero con un UPDATE pelado: sin asiento, sin evento, sin disposición TRD materializada, sin transacción.
  *Por qué*: el historial muestra que esta ruta alterna ya se había detectado dos veces y cada vez se tapó solo una dimensión del problema.
  *Impacto*: `status` se retira de `ExpedienteUpdate` con `extra="forbid"` (load-bearing — sin `forbid` un `{"status":...}` se ignoraría en silencio, peor que el bug original).

- `fix(archive)`: **Atomicidad transición↔asiento en cierre y transferencia + TOCTOU de disposición + ruta gemela de transferencia sin auditoría**
  `close`/`transfer` no abrían ninguna transacción (mutación y asiento en autocommits sueltos); `transition_expediente` no tenía guarda de estado esperado, así que dos `close` concurrentes podían ambos commitear.
  *Impacto*: el tramo de BD se envuelve en una única transacción con el asiento como último write; el sellado XAdES queda fuera (post-commit, no revertible por ROLLBACK).

- `fix(archive)`: **Atomicidad vínculo/exclusión↔asiento de auditoría en link/unlink de radicados (incremento gemelo, RF-SEG-08 residual)**
  Mismo residual de la creación, ahora en los tres métodos de composición del expediente: mutación + evento + asiento en autocommit suelto. Envueltos en transacción; el batch usa una única tx para el lote (todo-o-nada).

- `fix(archive)`: **Atomicidad expediente↔asiento de auditoría + carrera de código duplicado en la creación (integridad, RF-SEG-08 residual)**
  El INSERT del expediente y su asiento no eran atómicos; y `next_expediente_code` podía dejar que dos creadores concurrentes leyeran el mismo consecutivo.
  *Impacto*: colapsado a un único `INSERT…ON CONFLICT…RETURNING`; con la transacción, el row-lock serializa concurrentes sin duplicados ni huecos.

- `fix(archive)`: **No-read-up al enumerar los expedientes de una unidad física + auditoría de la creación por batch (barrido RF-SEG-08)**
  `GET /unidades/{id}/expedientes` enumeraba contenido de una caja sin filtro de clearance; la creación por batch no dejaba asiento de auditoría. Ambos corregidos; la gemela `GET /expedientes/{id}/unidades` (custodia ortogonal) queda deliberadamente sin filtrar.

- `fix(archive)`: **No-read-up en el FUID — el inventario documental deja de filtrar expedientes clasificados (E12/V2, RF-SEG-08)**
  El FUID (asunto/serie/signatura/folios) es descripción de contenido y se servía sin filtro de clearance por 3 puertas.
  *Impacto*: single → 404 total; multi → filtro por fila con `max_clearance` obligatorio. Cierra el último diferido de RF-SEG-08 en expedientes.

- `fix(archive)`: **Respuesta acotada de link/unlink/rebuild — sin oráculo de pertenencia/volumen/ciclo de vida de expedientes clasificados (M2, RF-SEG-08)**
  Estos endpoints filtraban a un usuario de bajo clearance la pertenencia, el volumen y el estado de un expediente clasificado, aunque su mutación es legítima sin clearance de lectura.
  *Impacto*: la mutación SIEMPRE ocurre y SIEMPRE audita; solo se redacta la respuesta cuando el nivel supera el clearance del llamante. Cierra la terna M1/M2/M3 de RF-SEG-08.

- `fix(archive)`: **No-write-up al crear expedientes — no se puede originar un expediente por encima del propio clearance (M3, RF-SEG-08)**
  Un usuario con permiso pero clearance bajo podía crear una CLASIFICADA y perderla de vista de inmediato (el no-read-up ya la oculta).
  *Impacto*: `403` explícito, no clamp silencioso (rebajar el nivel sería una desclasificación de facto sin traza).

- `fix(archive)`: **Disposición de expedientes clasificados sin 404 espurio + respuesta acotada que no filtra contenido (M1, RF-SEG-08)**
  `close`/`transfer` terminaban leyendo el expediente sin `user_id` → clearance PUBLICA → 404 espurio pese a que la mutación ya se había comprometido, incluso para un archivista con clearance real.
  *Por qué*: la disposición es ortogonal al clearance de lectura (Ley 594 arts. 22-24) — un archivista mueve series al AGN sin leer cada expediente reservado.
  *Impacto*: respuesta estrecha sin re-leer, sin exponer `metadata`/`description`/`radicados`.

- `fix(archive)`: **No-read-up en el listado y el PATCH de expedientes (RF-SEG-08) — cierra la enumeración de expedientes reservada/clasificada**
  `GET /api/v1/expedientes` no filtraba por `nivel_seguridad` — cualquier portador del permiso enumeraba expedientes clasificados y su total. El mismo leak existía en `PATCH /{id}`.
  *Impacto*: se elimina el default peligroso `max_clearance=3`; los call sites diferidos pasan explícitamente la constante nombrada `CLEARANCE_SIN_RESTRICCION`, con el riesgo visible en el código, no escondido en una firma.

- `fix(shared,auth,archive,document,workflow,signature,notification)`: **Auditoría de accesos denegados — el gate RBAC deja traza en `audit_log` al denegar (cierra el último residual RBAC)**
  Las denegaciones de permiso no dejaban rastro — invisible una escalada de privilegios. Nuevo helper compartido `append_denial`, best-effort, invocado antes del 403 en los 7 servicios.

- `fix(archive)`: **Mínimo privilegio en el gate RBAC — umbral `min_crud` por operación; disposición exige `crud≥3` (RF-SEG-03)**
  `require_permission` concedía con cualquier `crud>0` sin distinguir lectura/escritura/disposición — un usuario con nivel Leer podía cerrar/transferir expedientes.
  *Impacto*: elevado a `min_crud=3` en las 9 operaciones de disposición de archive; se cerró además la ruta alterna del PATCH que las eludía.

- `fix(archive)`: **Cierre del residual RBAC en la config archivística + trazabilidad**
  Las 6 mutaciones de configuración archivística (TRD, tipos documentales, plantillas) no aplicaban `require_permission` — cualquier usuario del tenant podía alterar los instrumentos que gobiernan retención/disposición. Ahora exigen `USUA_PERM_TRD` y auditan.

- `fix(archive)`: **Cierre del gap RBAC en la gestión de expedientes**
  Los 12 endpoints de `expedientes.py` no aplicaban `require_permission` — cualquier usuario del tenant podía crear/cerrar/transferir/leer expedientes sin el permiso.
  *Impacto*: destapó y cerró dos bypass adicionales en `batch.py` y un bug de aislamiento cross-tenant (tarea detached reutilizaba la conexión request-scoped).

- `fix(notification,signature)`: **Cierre de la fuga del OTP — notification-service no persiste el cuerpo de correos sensibles (seguimiento de E06 Inc.2, RF-FIR-13)**
  El OTP viajaba en el cuerpo del correo y notification-service persistía el `body` completo, dejando el OTP en claro en BD/backups durante 5 minutos.
  *Impacto*: nuevo flag `sensible` — el correo se envía con el body real pero se persiste redactado. La única copia persistente del OTP es su HMAC en `firma_challenge`.

### Añadido

- `feat(signature)`: **2FA por OTP-correo en la firma personal (E06 Inc.2, RF-FIR-13, ADR-016 addendum)**
  signature-service exige un segundo factor en el acto de firma, ligado a un `challenge_id` de un solo uso vinculado al hash del documento (Ley 527/1999 art. 7).
  *Impacto*: solo el HMAC del OTP se persiste, nunca el OTP en claro; **BREAKING**: `POST /batch` pasa a requerir un OTP por documento. No desactivable para nivel≥2.

- `feat(signature,archive)`: **Firma XAdES-B real del índice electrónico + sello institucional (E06 Inc.1, ADR-016 addendum)**
  signature-service estrena un proveedor de firma enchufable; el índice se sella con el certificado institucional del tenant, atribuido a la persona que cierra vía `X-User-Id`+auditoría.
  *Impacto*: si el sellado falla, el cierre no se bloquea — el índice queda `pendiente_firma` y bloquea la transferencia hasta reintentarse. Sin CA acreditada ONAC, todo queda `no_acreditado`.

- `feat(signature,workflow,notification)`: **Pulido de 3 seguimientos Baja de la gestión del dead-letter (ADR-021)**
  Cap señalizado en las candidatas de clearance (con header `X-Truncated`), resolución de nivel en lote (elimina el N+1) e identidad unificada en el listado de workflow.

- `feat(signature)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en signature-service**
  Filtro de clearance fila-a-fila en el listado admin del DLQ, auditoría de vista del detalle, y `origin_group` obligatorio con índice compuesto.

- `feat(workflow)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en workflow-service**
  Mismo diseño que signature/notification: filtro de clearance en el listado, auditoría de vista, `origin_group` obligatorio.

- `feat(notification)`: **Cierre de los 3 seguimientos no-bloqueantes de la Fase 5 (ADR-021) en notification-service**
  Mismo diseño que signature/workflow, aplicado sobre el listado admin de notification.

- `feat(notification)`: **Replay/gestión del dead-letter + purga selectiva (Fase 5 Inc.2 + Fase 4 MVP-3 de notification, ADR-021 — cierra el roadmap de eventos)**
  notification estrena la superficie admin completa (listar/detalle/replay/discard) gateada por `PERM_DLQ_ADMIN`, con replay best-effort (claim-then-send) y purga solo de estados terminales.
  *Impacto*: auditoría como dependencia dura — si el módulo de auditoría no está, `replay`/`discard` fallan con 503 en vez de commitear sin traza. Cierra el roadmap ADR-021 en los tres servicios.

- `feat(signature,workflow)`: **Purga selectiva de `evento_dead_letter` (Fase 4 MVP-3, ADR-021)**
  Job que purga solo estados terminales con gracia de 90 días configurable; `pending` nunca se purga.

- `feat(signature,workflow,auth)`: **Gestión/replay del dead-letter — endpoints admin (Fase 5 Inc.1, ADR-021)**
  Superficie admin por servicio gateada por el nuevo permiso `PERM_DLQ_ADMIN`; replay atómico con `SELECT FOR UPDATE` + reproceso en la misma transacción + auditoría (`evento.dead_letter.replayed`).

- `feat(events)`: **Retención del buffer DLQ Redis vía `XTRIM` (Fase 4 MVP-2, ADR-021)**
  Los streams dead-letter de Redis crecían sin tope; nuevo helper compartido recorta por edad (30 días configurable) — la copia autoritativa vive en Postgres, Redis es solo buffer.

- `feat(notification)`: **Retención del cache de deduplicación (Fase 4 MVP-1, ADR-021)**
  Job que purga los marcadores de deduplicación vencidos (7 días configurable), iterando tenants activos de forma aislada por schema.

- `feat(notification)`: **Dead-letter durable + idempotencia de eventos (Fase 3, ADR-021 — completa el roadmap de eventos)**
  notification-service consume dos streams sin ser idempotente; nueva tabla de eventos procesados con patrón insert-then-send evita doble correo/webhook.
  *Impacto*: trade-off documentado — una caída entre el claim y el envío puede perder ese correo, aceptado porque E16/E11 son efectos de cortesía, no el registro legal.

- `feat(workflow)`: **Recuperación de pendientes + dead-letter durable de eventos (Fase 2, ADR-021)**
  workflow-service (que auto-distribuye `radicado.created`, el camino más sensible) estrena el mismo dead-letter durable+atómico que signature, cerrando un modo de fallo donde el `xack` se emitía incondicionalmente aunque la distribución fallara.

- `feat(events,signature)`: **Dead-letter durable y atómico — cierre de R-4 (ADR-021)**
  La traza del dead-letter era best-effort tras el `xack` y solo vivía en Redis (volátil); ahora se persiste atómicamente en Postgres junto con el asiento de auditoría, con el `xack` gateado por el éxito.

- `feat(events,signature)`: **Recuperación de pendientes + dead-letter de eventos (Fase 1, ADR-021)**
  Nuevo helper compartido que recupera mensajes vencidos del PEL de Redis Streams y, tras el máximo de reintentos, deriva el evento a un dead-letter por grupo consumidor con traza inmutable en `audit_log`.

- `feat(frontend)`: **UI de reclasificación del nivel de seguridad en la bandeja**
  Drawer de detalle del radicado con chip de nivel y botón "Reclasificar" gateado por `PERM_RECLASIFICAR`; modal con motivo obligatorio y campos de reserva condicionales.

- `feat(document)`: **`nivel_seguridad` expuesto en el read path de radicados**
  La columna existía y se usaba en filtros de clearance, pero no se devolvía en ninguna respuesta; ahora se incluye en todas las lecturas.

- `feat(document,signature,auth)`: **Reclasificación del nivel de seguridad de un radicado (E08) + re-sync del snapshot de firma**
  Nuevo `PATCH /api/v1/documents/{id}/security-level` con no-read-up, no-write-up y `motivo` obligatorio; signature-service re-sincroniza su snapshot de nivel al recibir el evento.

- `feat(frontend)`: **T-12d-firma — bandeja del firmante (UI de la cadena E06)**
  Cierra el ciclo E06 de extremo a extremo: bandeja cross-documento, firma individual y en lote, con espejo RBAC de `PERM_FIRMA`.

- `feat(signature)`: **E06 — cadena de firma (bandeja del firmante)**
  Nuevo modelo de cadena de firma con turnos ordenados; el turno se respeta con `SELECT FOR UPDATE` (cierra TOCTOU de doble firma); archive verifica la firma del índice contra este servicio.

### Corregido

- `fix(signature,workflow)`: **Scoping por `origin_group` de la tabla compartida `evento_dead_letter`**
  Las filas se distinguen por `origin_group` pero ninguna query lo filtraba — la purga de un servicio borraba dead-letters del otro. Las 5 queries pasan a filtrar explícitamente.

- `fix(workflow,notification)`: **Los consumidores de eventos ya no tratan la expiración normal de `xreadgroup(block=...)` como error**
  redis-py lanza `TimeoutError` al expirar el bloqueo sin entradas nuevas; caía en el `except` general y generaba backoff/ruido innecesario. Ahora se captura en rama propia con `continue` silencioso.

- `fix(signature)`: **Consumidor de re-sync ya no loguea la expiración normal de `xreadgroup(block=...)` como error**
  Mismo patrón que el fix anterior, descubierto primero en signature-service durante un smoke test E2E (cientos de líneas de log/min sin errores reales).

- `fix(frontend)`: **D-16 — SSR 500 en todas las rutas `(app)` por `onDestroy` que toca `document`**
  `AppLayout`, `AssistantDock`, `Drawer` y `Modal` limpiaban su listener de teclado con `onDestroy`, que también corre en SSR donde `document` no existe — `ReferenceError` → HTTP 500 en `/dashboard`, `/firmas` y toda ruta del layout privado.
  *Impacto*: la limpieza pasa al retorno de `onMount` (solo cliente); verificado en navegador real junto con la bandeja de firmas y el no-read-up.

- `fix(auth)`: **D-07 — espejo RBAC de la UI roto**
  `GET /auth/me` no devolvía `permissions` — el frontend hacía `me.permissions ?? []`, siempre vacío, así que toda la UI de control por rol quedaba desactivada. `UserInfoResponse` ahora incluye los permisos efectivos resueltos desde la BD del tenant.

### Seguridad

- `fix(archive)`: **[duplicado] Ciclo de vida de transferencias (E12) — carrera TOCTOU letal cerrada**
  Entrada repetida en el changelog original, idéntica a la de la sección Seguridad anterior.

- `fix(archive)`: **[duplicado] No-read-up en la consulta de eventos de preservación por expediente**
  Entrada repetida en el changelog original.

- `fix(archive)`: **[duplicado] No-read-up en la descarga del AIP de preservación**
  Entrada repetida en el changelog original.

- `fix(workflow)`: **[duplicado] CRUD de reglas de enrutamiento automático sin autorización**
  Entrada repetida en el changelog original.

- `fix(document)` + `fix(storage)` + `fix(gateway)`: **[duplicado] No-read-up en la descarga de anexos**
  Entrada repetida en el changelog original.

- `fix(workflow)`: **[duplicado] No-read-up en la bandeja + tenencia en `rollback`**
  Entrada repetida en el changelog original.

- `fix(document)`: **[duplicado] No-read-up en las Salidas vinculadas**
  Entrada repetida en el changelog original.

- `fix(document)`: **[duplicado] Cierre del no-read-up en firmas, anulación, respuesta vinculada y envíos**
  Entrada repetida en el changelog original.

- `fix(document)`: **[duplicado] No-read-up en disposición y PATCH de radicados**
  Entrada repetida en el changelog original.

- `fix(archive)`: **[duplicado] Atribución del `audit_log` de la renovación WORM**
  Entrada repetida en el changelog original.

- `fix(notification)`: **[duplicado] Sellado de `/send` + autorización de webhooks**
  Entrada repetida en el changelog original.

- `fix(workflow)`: **[duplicado] Enforcement RBAC de las transacciones de trámite**
  Entrada repetida en el changelog original.

- `fix(workflow)`: **[duplicado] Cierre de hallazgos bloqueantes en workflow-service**
  Entrada repetida en el changelog original.

### Cambiado

- `docs(rebrand)`: **Nombre visible del proyecto `OrfeoMCP` → `OrpycaMCP`**
  *Por qué*: el proyecto es la evolución de Orfeo bajo el concepto Orpyca ("Orfeo Pyme Calidad") con conexión a IA vía MCP.
  *Impacto*: convención por superficie — marca `OrpycaMCP`, repo/imágenes `orpyca-mcp`, imports Python `orpycamcp_common`, datos/infra `orpycamcp` (sin la sigla, por restricciones de Postgres/MinIO).

- `refactor(infra)`: **Renombrado del plano de datos `orfeomcp` → `orpycamcp`**
  Completa el rebrand en datos/infra: BD, credenciales, realm Keycloak, buckets, streams Redis, paquete Python.
  *Impacto*: **requiere recrear volúmenes** (postgres/minio/keycloak) — los datos de desarrollo previos no son compatibles.

- `docs(claude)`: **`CLAUDE.md` sincronizado con la arquitectura real** (11 servicios, sin SQLAlchemy/Alembic, fases por specDrive, rutas corregidas).

- `chore(ci)`: **CI unificada en GitLab** — eliminado `.github/workflows/`; README/badges/clone apuntan a GitLab.

### Agregado

- `feat(infra)`: **Frontend en su propio contenedor**
  Split de URLs público/interno, variables para el `redirect_uri` del PKCE tras el proxy, y pinning de issuer para el desfase navegador↔red Docker.

- `fix(workflow)`: **`GET /workflows/{id}/events` acotado por clearance (E05 §10 / RF-SEG-08)** — la hoja de ruta del radicado solo es visible para quien pueda leerlo.

- `feat(archive)`: **Hoja de ruta del expediente (E02 §9)** — tabla append-only de eventos y `GET /expedientes/{id}/eventos` acotado por clearance.

- `feat(audit)`: **Cobertura de `public.audit_log` (E08 §9)** — workflow y archive emiten a la auditoría forense transversal vía la librería compartida.

- `chore(compose)`: **Puertos de desarrollo movidos a rango alto (19xxx/15432/16379)** para no chocar con otras pilas de desarrollo. Comunicación interna sin cambios.

- `fix(compose)`: **signature-service y knowledge-service esperan postgres *healthy*** — antes salían con `ConnectionRefused` en el arranque.

- `fix(security)`: **Blindaje total de lectura — GET de detalle y búsqueda semántica (RF-SEG-08 / RF-BUS-04)**
  Cierra las superficies de lectura que aún no aplicaban clearance: acceso directo por id/tracking, y la búsqueda semántica de knowledge-service, que confiaba en el `acl` enviado por el cliente en vez de resolverlo en el servidor.
  *Impacto*: control de acceso de lectura cerrado en TODAS las superficies (búsqueda, listado, detalle, recuperación semántica).

- `fix(document)`: **Control de acceso por clasificación en la búsqueda (RF-SEG-08 / RF-BUS-04)**
  La búsqueda solo aislaba por tenant; dentro de un tenant cualquier usuario veía todo radicado sin importar clearance.
  *Impacto*: nueva columna `nivel_seguridad` con DEFAULT 1 (no rompe lo existente); filtro no-read-up fail-closed a PUBLICA.

- `fix(archive)`: **Misma acotación por clearance en listado de radicados y búsqueda de expedientes (RF-SEG-08 / RF-BUS-04)**
  Completa el control de acceso de lectura en las superficies que aún exponían todo dentro del tenant.

- `docs(adr)`: **ADR-020 — sistema de diseño Orpyca + asistente conversacional del frontend (E22)**
  Fija las decisiones transversales de diseño/UX e IA antes de codificar el frontend: tokens `--op-*`, UI por rol espejo del RBAC, asistente que traduce lenguaje natural a tools MCP sin ejecutar acciones por sí mismo.

- `chore(quality)`: **Saneamiento previo al primer commit** — `ruff check` limpio (36 hallazgos corregidos), `.env.example` completados, notas de trabajo movidas fuera del árbol versionado.

- `chore(license)`: **Encabezado de atribución/licencia en todo el código fuente** — añadido a 437 archivos con la nota AGPL v3.

- `feat(mcp-server)`: **OAuth por sesión — el MCP obtiene/refresca el token Keycloak (E18, ADR-019)**
  El mcp-server gestiona el ciclo de vida del token vía ROPC/refresh de auth-service, con caché por expiración. Completa la capa MCP (tools + resources + prompts + stdio + HTTP + OAuth).

- `feat(mcp-server)`: **Resources y prompts MCP (E18, ADR-019)** — completa las tres capacidades MCP (tools + resources + prompts) resueltas contra el gateway vía el catálogo.

- `feat(mcp-server)`: **Transporte MCP HTTP streamable para clientes remotos (E18, ADR-019)** — endpoint `POST /mcp` con `StreamableHTTPSessionManager`; el contexto de sesión llega por cabeceras.

- `feat(mcp-server)`: **Servidor MCP funcional por stdio (E18, ADR-019)** — binding del SDK MCP oficial sobre el catálogo/dispatcher existentes, con handshake verificado.

- `docs(ops)`: **Notas de operación de integraciones externas** (ES+EN) — documenta el punto de swap y contrato estable para cada capacidad enchufable (PKI, WORM, PDF/A, MCP, RAG, operador postal).

- `docs(i18n)`: **Documentación publicable completa en español e inglés** — traducidas al inglés las 14 páginas del nav; `mkdocs build --strict` limpio en ambos idiomas.

- `feat(document)`: **Dashboard estadístico ampliado + export CSV (E09, exigible AGN)** — nuevo desglose por dependencia y exportación CSV del resumen de radicados.

- `feat(document)`: **Radicación ciudadana de PQRS (público, Ley 1755/2015)** — endpoint público sin autenticación que crea un radicado de Entrada con seguimiento por código de verificación.

- `feat(document)`: **Plantillas + borradores de Salida (paridad legado)** — flujo crear/editar/aprobar/radicar antes de asignar número oficial.

- `docs(api)`: **Documentar la administración del tenant (E14)** — festivos/días no hábiles y cálculo de días hábiles, que existían en código pero no en `api.md`.

- `feat(workflow)`: **Vistos buenos secuenciales (paridad legado)** — cadena ordenada de revisores; un rechazo detiene la cadena.

- `feat(document)`: **Respuesta rápida — Salida vinculada a su antecedente de Entrada (paridad legado)** — cubre el 30-50% de la producción diaria de Salidas.

- `feat(document)`: **Anulación de radicado en dos pasos (paridad legado)** — la ley prohíbe borrar radicados; se anulan con aprobación supervisora, el número se conserva.

- `feat(workflow)`: **Devolución al remitente (paridad legado)** — re-enrutar un radicado mal asignado, la acción más frecuente tras recibir.

- `feat(tenant)`: **Catálogos de operador — causales, formas de envío, soportes, mensajes rápidos (paridad legado, E14)**.

- `feat(archive)`: **Inclusión masiva de radicados a un expediente (paridad legado)** — hasta 500 por lote, idempotente, regenera el índice una sola vez.

- `feat(storage)`: **Empaquetado AIP (BagIt + manifiesto PREMIS) (E10 RF-PRE-05/07, ISO 14721 OAIS)** — primera versión del empaquetado OAIS, con WORM real y veraPDF pendientes de incrementos posteriores.

- `feat(archive)`: **Firma del índice electrónico al cierre del expediente (E15-F3, RF-EXP-06)** — best-effort: si signature-service no responde, el cierre no se bloquea.

- `feat(signature-service)`: **Nuevo servicio de firma electrónica (E06, ADR-016)** — proveedor pluggable (nativo por defecto), firma y verificación de payloads.

- `fix(gateway)`: **Enrutar el motor de reglas `/api/v1/workflow/rules/` (E05)** — el prefijo singular dejaba el motor de reglas inalcanzable vía gateway (404).

- `docs(domain)`: **Actualizar `domain.md` con las entidades F3-F6** (índice electrónico, firma, archivo físico, FUID, transferencia, preservación, envío postal, webhook).

- `docs(adr)`: **ADR-014 — búsqueda con PostgreSQL FTS (tsvector + pg_trgm + JSONB), no Elasticsearch** — coherente con el aislamiento por schema, sin infra externa.

- `feat(document)`: **Ingesta de correo IMAP → radicado de Entrada (E19/F2)** — crea un radicado de Entrada por correo no leído, con remitente y anexos en metadata.

- `feat(document)`: **Consulta pública por código de verificación (E13/F2)** — un ciudadano verifica trazabilidad sin login (Ley 1712/2014), solo campos no sensibles.

- `docs(adr)`: **ADR-017 — archivo físico: Ubicación (dirección recursiva) ≠ Unidad de conservación (contenedor móvil)**, patrón ArchivesSpace.

- `feat(document)`: **Envíos postales de radicados (E20/F5, vía E11)** — proveedor pluggable, máquina de estados registrado→en_transito→entregado/devuelto/fallido.

- `docs(adr)`: **ADR-019 — capa MCP como fachada fina cliente del gateway** — catálogo de tools declarativo, sin lógica de negocio ni BD propia.

- `feat(knowledge-service)`: **Nuevo servicio de capa de conocimiento (E21/F6, ADR-006)** — pgvector por tenant, proveedor de embeddings pluggable (stub local por defecto, sin LLM), capa advisoria/derivada.

- `feat(mcp-server)`: **Nuevo servicio de capa MCP (E18/F6, ADR-019)** — catálogo declarativo de tools mapeadas 1:1 a endpoints REST, filtradas por permisos del usuario.

- `feat(storage)`: **Plan de preservación digital + eventos PREMIS (E10/F5, RF-PRE-01/07, AGN 001/2024 art. 4.3.2.6)** — registro inmutable de intervenciones sobre el acervo.

- `docs(adr)`: **ADR-018 — interoperabilidad saliente por webhooks HTTP firmados (HMAC-SHA256)** — suscritos por tenant, sin exponer el bus interno.

- `feat(notification)`: **Webhooks salientes firmados (E11/F5, ADR-018)** — entrega best-effort con reintentos que nunca bloquea el procesamiento del evento origen.

- `feat(archive)`: **Transferencias documentales primarias/secundarias (E12/F5, AGN 001/2024 art. 4.4.x)** — ciclo preparada→enviada→recibida/rechazada; el expediente se congela al recibir.

- `feat(archive)`: **FUID — Formato Único de Inventario Documental (E17 RF-ARF-10, AGN 042/2002)** — inventario canónico consumido por las transferencias.

- `feat(archive)`: **Préstamos e historial de movimientos físicos (E17 RF-ARF-07/08)** — control de salida/devolución de unidades, traza append-only.

- `feat(archive)`: **Modelo de archivo físico nuclear (E17/F5, ADR-017)** — ubicación recursiva + unidad de conservación con signatura topográfica.

- `feat(archive)`: **Verificación de integridad del índice (E15 T-14)** — contrasta la huella registrada con el hash actual de cada documento.

- `feat(archive)`: **Valor huella por documento en el índice (E15 T-08)** — el hash SHA-256 viaja con el vínculo, lo aporta quien conoce el contenido.

- `feat(archive)`: **Actualización automática del índice + inmutabilidad al vincular/excluir (E15 RF-EXP-04)** — un expediente no `open` rechaza cambios de vínculo.

- `feat(archive)`: **Índice electrónico del expediente (E15, Acuerdo AGN 001/2024)** — pieza que da validez legal al expediente electrónico; XML con huella canónica y versionado append-only.

- `docs(adr)`: **ADR-016 — firma electrónica nativa (SHA-256 + identidad + sello de tiempo)**, PKI/PDF diferida.

- `feat(document)`: **Firma electrónica de radicados/anexos (E06/F3, ADR-016)** — deja constancia verificable de quién firmó qué y cuándo.

- `feat(archive)`: **Ciclo de vida del expediente — cierre/transferencia/índice (E02/F3, ADR-015)** — aplica la disposición TRD al cerrar.

- `docs(adr)`: **ADR-015 — modelo TRD/CCD** (jerarquía serie/subserie, retención en dos fases, disposición AGN CT/E/S/M).

- `feat(archive)`: **Modelo TRD/CCD y cálculo de retención (E04/F3, ADR-015)** — subseries, retención en dos fases, cálculo de disposición final.

- `feat(archive)`: **Búsqueda full-text de expedientes (E09/F2, ADR-014)** — FTS con ranking, filtros por estado/fecha/metadata.

- `feat(document)`: **Reportes/estadísticas de radicados (E09/F2)** — conteos por tipo/estado/mes.

- `feat(document)`: **Búsqueda avanzada (E09/F2, ADR-014)** — texto opcional, filtros combinables, ranking cuando hay texto.

- `fix(gateway)`: **Enrutamiento de todos los prefijos de dominio (consolidación F1)** — varios prefijos de dominio quedaban inalcanzables (404) vía gateway.

- `feat(auth)`: **auth-service — Keycloak ROPC, JWT validation, audit log, endpoints `/token` `/refresh` `/logout` `/validate` `/me`**.
- `feat(tenant)`: **tenant-service — CRUD de instituciones + provisioning de schema PostgreSQL por tenant**.
- `feat(gateway)`: **api-gateway — proxy centralizado, validación JWT, rate limiting Redis, inyección de headers de identidad**.
- `feat(document)`: **document-service — radicación E/S/I, numeración atómica, gestión de referencias a anexos**.
- `feat(storage)`: **storage-service — upload a MinIO, SHA-256, deduplicación, URLs pre-firmadas, buckets por tenant**.
- `feat(workflow)`: **workflow-service — flujos de distribución entre dependencias, historial, eventos Redis Streams**.
- `feat(archive)`: **archive-service — expedientes con ciclo de vida, vinculación de radicados, TRD con retención documental**.
- `feat(notification)`: **notification-service — consumer Redis Streams, SMTP, historial en Redis**.
- `refactor(all)`: **Retirar SQLAlchemy/Alembic** — asyncpg directo y migraciones SQL en archivos numerados.

### Fase 1 — Cimientos (en progreso, 2026-06-16)

- `feat(shared)`: **Librería compartida `orpycamcp_common` (ADR-010)** — auditoría (cadena de hash) y sobre de eventos idénticos en todos los servicios.

- `feat(auth)`: **`public.audit_log` canónico append-only + migración de auditoría (ADR-008/010)** — tabla particionada por mes con trigger de inmutabilidad y cadena de hash.

- `chore(infra)`: **Contexto de build en la raíz + `.dockerignore`** — permite `COPY shared` en los Dockerfile; patrón a replicar en servicios que usen la librería compartida.

- `feat(infra)`: **Runner de migraciones por tenant (ADR-012)** — separa `migrations/global/` (→ `public`) de `migrations/tenant/` (→ cada schema), idempotente.

- `feat(auth)`: **RBAC de 4 tablas + URD + clasificación de seguridad (E08)** — Keycloak autentica, la BD autoriza (RF-SEG-03/04/05); seed de 15 permisos.

- `feat(auth)`: **Resolución de permisos + endpoints de administración RBAC (E08)** — `RBACService.resolve_effective_permissions` (MAX de crud por permiso, ROOT primero); pendiente el gate `require_permission` (espera el JWT enriquecido).

- `feat(tenant)`: **Configuración por tenant — calendario de días hábiles, parámetros y catálogos (E14)** — festivos colombianos (Ley 51/1983) y cálculo de días hábiles para el plazo de respuesta.

- `feat(auth)`: **Clasificación de seguridad y verificación de acceso (E08, RF-SEG-08, Ley 1712/2014)** — catálogo de niveles y resolución del clearance del llamante.

- `feat(auth)`: **URD multi-dependencia + cambio de contexto (E08, RF-SEG-04, ADR-013)** — un usuario con rol en varias dependencias fija su dependencia activa sin reemitir token.

- `feat(auth)`: **Consulta y verificación de auditoría (E08, ADR-008)** — `GET /audit` y `/audit/verify` recalculan la cadena de hash; repositorio de solo lectura.

- `feat(auth)`: **Gate `require_permission` en endpoints RBAC + decisión R4 (E08, ADR-013)** — los endpoints de administración RBAC ganan el gate que les faltaba; Keycloak autentica, la BD autoriza.

- `docs(adr)`: **ADR-013 — autorización resuelta en la BD por request** — se descarta firma propia y token-exchange; permisos siempre frescos, revocación inmediata.

- `feat(tenant)`: **CRUD de catálogos de referencia (E14)** — tipos de identificación/remitente/anexo y medios de recepción parametrizables por institución.

- `fix(tenant)`: **Endpoints de tenants devolvían 404 con barra final** — `redirect_slashes=False` sin rutas registradas con barra; alineado a la convención `/api/v1/{resource}`.

- `feat(notification)`: **Alertas de vencimiento de trámite (E16↔E05, RF-FLU-07)** — el barrido de vencimientos de workflow ahora dispara un correo de aviso.

- `feat(workflow)`: **Motor de transacciones de trámite (E05, RF-FLU-01)** — catálogo de 12 transacciones (informar, NRR, VoBo, anular, cerrar exp...) con evento append-only por acto.

- `feat(workflow)`: **Vencimientos y semáforos del trámite (E05, RF-FLU-07)** — semáforo calculado en lectura (verde/amarillo/rojo/vencido); barrido periódico que alerta y marca sin duplicar.

- `feat(workflow)`: **Bandejas tipadas de trámite (E05, RF-FLU-03)** — listado de pendientes por tipo (Entrada/Salida/Internos), priorizado por antigüedad.

- `feat(workflow)`: **Rollback de asignación + reasignación en cascada (E05, RF-FLU-08)** — deshace la última asignación errónea; reasigna masivamente sin radicados huérfanos.

- `feat(workflow)`: **Distribución automática por enrutador (E05, RF-FLU-04)** — al radicar, workflow-service evalúa las reglas activas y autoasigna, idempotente.

- `feat(workflow)`: **Historial de trámite append-only `flow_events` (E05, RF-FLU-02)** — trigger que rechaza UPDATE/DELETE; cada transacción escribe su evento en la misma tx que el paso.

- `feat(workflow)`: **Motor de reglas con operadores, AND/OR y evaluación batch (E05)** — operadores comparativos sobre 8 campos, combinables; evaluación de hasta 500 radicados en una sola lectura de reglas.

- `refactor(storage,workflow)`: **Migraciones reorganizadas a `tenant/` (ADR-012)** — ambos servicios compartían tablas entre instituciones (aislamiento roto).

- `feat(notification)`: **Reintentos SMTP con backoff (E16)** — hasta 3 intentos con backoff lineal; estado persistido.

- `feat(notification)`: **Historial de notificaciones persistido en BD por tenant (E16, ADR-002/012)** — el historial vivía solo en Redis (volátil, TTL 30 días); ahora durable y aislado por institución.

- `feat(document)`: **Filtro de consulta por metadato (GIN) + disposición documental (E03, RF-MET-02/08)** — consulta por campo de metadato vía contención JSONB; columna de disposición gestionable.

- `feat(archive)`: **Catálogo de tipos documentales (3er nivel TRD) (E03, RF-MET-07)** — CRUD con plantilla de metadatos asociada.

- `feat(document)`: **Catálogo de elementos de metadato (E03, RF-MET-03)** — definición reutilizable de campos con tipo, ocurrencias y mapeo Dublin Core.

- `feat(document)`: **Clasificación y foliación de anexos (E07, RF-DIG-01/RF-DIG-10)** — tipo documental, marca de principal y número de folios por anexo.

- `feat(storage)`: **Versionado inmutable de anexos (E07, RF-DIG-01)** — "reemplazar" crea una nueva versión conservando las anteriores, no sobrescribe.

- `feat(storage)`: **Integridad en descarga + validación de formatos (E07, RF-DIG-02/04)** — recalcula el SHA-256 al descargar y valida el MIME contra una allowlist en la subida.

- `feat(archive)`: **Metadatos flexibles del expediente (E03, ADR-007)** — JSON Schema por serie, igual que el radicado.

- `refactor(archive)`: **Migraciones reorganizadas a `tenant/` (ADR-012)** — el seed institucional de FondeCund deja de auto-aplicarse a todos los tenants.

- `feat(document)`: **Metadatos flexibles por tipo documental (E03, ADR-007)** — JSON Schema versionado por tipo; radicar sin cumplir el schema falla con 422.

- `refactor(workflow)`: **Migración al sobre canónico de eventos (E05, ADR-010)** — workflow-service adopta el mismo sobre que document-service; elimina el formato plano legado.

- `feat(notification)`: **Consumo del stream canónico de document (E16, ADR-010)** — al radicar, la dependencia destino recibe notificación; despacho por nombre de stream.

- `feat(document)`: **Auditoría inmutable + eventos de dominio en la radicación (ADR-008/010, E01)** — cada radicación escribe en `audit_log` atómicamente con el INSERT y publica el evento (best-effort).

- `feat(document)`: **Vínculo de radicados con el organigrama de dependencias (E01)** — valida `origin_dept`/`dest_dept` contra el catálogo, con snapshot legal del nombre.

- `feat(document)`: **Fecha de vencimiento en días hábiles (RF-RAD-04, E01)** — calcula el plazo consumiendo el calendario de tenant-service vía HTTP.

- `fix(document)`: **Aislamiento por-tenant de radicación (ADR-012, E01)** — la numeración de radicados (`tracking_sequences`) quedaba compartida entre instituciones antes de este fix.

- `refactor(document)`: **Routers de radicación/búsqueda/batch usan `get_tenant_conn` (ADR-002/012, E01)** — `search`/`batch` no fijaban `search_path` y solo funcionaban con las tablas en `public`.

- `feat(tenant)`: **CRUD jerárquico de dependencias (organigrama, E14)** — árbol jerárquico, consumido por la radicación para numerar por dependencia.

### Decisiones de arquitectura — ADR (2026-06-15)

- `docs(adr)`: **ADR-006 — proveedor de IA configurable multi-proveedor** — soberanía local por defecto, recomendada.
- `docs(adr)`: **ADR-007 — metadatos `JSONB` validados por plantilla** — se descarta EAV.
- `docs(adr)`: **ADR-008 — auditoría inmutable `public.audit_log`** — append-only particionado por mes con cadena de hash por tenant.
- `docs(adr)`: **ADR-009 — historial de flujos por eventos** — `flow_events` append-only como fuente de verdad, `flow_steps` reconstruible.
- `docs(adr)`: **ADR-010 — librería compartida `orpycamcp_common`** — auditoría y sobre de eventos idénticos en todos los servicios.
- `docs(adr)`: **ADR-011 — frontend SvelteKit en el monorepo** — stack alineado con sgdINTI (SSR + Vite + Bulma).
- `scaffold(frontend)`: **Estructura inicial de `frontend/`** — herramientas y árbol fijados, dependencias no instaladas (Docker-only).

### Fase 6 — Características Avanzadas (2026-06-05)

- `feat(phase6)`: **TRD seed data migration** — `scripts/migrate_trd_from_fondecund.py` con 150+ series de FondeCund.
- `feat(batch)`: **Batch Documents** — `POST /api/v1/batch/documents` (1-1000 radicados, 202 Accepted, job tracking).
- `feat(batch)`: **Batch Expedientes** — `POST /api/v1/batch/expedientes` (1-100 expedientes con radicados).
- `feat(search)`: **Full-Text Search** — `GET /api/v1/search?q=...` con PostgreSQL pg_trgm + tsvector.
- `feat(workflow)`: **Workflow Rules Engine** — CRUD de reglas + evaluación automática + audit log.
- `docs(adr)`: **ADR-005 documenta decisión de Batch API** (async jobs vs sync).

### Fase 5 — Comunidad y Publicación (2026-06-04)

- `infra(deploy)`: `docker-compose.prod.yml` + `infra/.env.example` + `docs/es/deployment.md`.
- `docs(contributing)`: `CONTRIBUTING.md` con workflow, testing y requisitos de documentación.
- `docs(readme)`: `README.md` en inglés con quick start, arquitectura, features.
- `docs(en)`: `docs/en/getting-started.md` + `docs/en/api.md` — documentación bilingüe.
- `infra(ci)`: GitHub Actions workflows — `docker-build.yml` + `ci.yml` (lint, test, E2E).
- `feat(openapi)`: agregación de specs en api-gateway (`GET /api/v1/openapi.json`).
- `infra(registry)`: `infra/DOCKER_REGISTRY.md` — guía GHCR + tagging strategy.

### Fase 4 — Calidad e Integración (2026-06-04)

- `infra(lint)`: `pyproject.toml` centralizado — ruff + mypy en todos los 8 servicios.
- `infra(health)`: `/health` orquestado verifica Redis + 7 servicios en paralelo.
- `test(e2e)`: `docker-compose.e2e.yml` con tests de flujo completo.
- `docs(adr)`: ADR-003 documenta decisión de asyncpg vs ORM.

---

## [0.1.0] — 2026-06-03

### Agregado

- Estructura base del proyecto con 8 microservicios FastAPI
- docker-compose.yml con infraestructura completa de desarrollo
- Realm Keycloak con roles RBAC y usuarios de prueba
- Schema isolation PostgreSQL por tenant
- Pipeline GitLab CI/CD con documentación automática
