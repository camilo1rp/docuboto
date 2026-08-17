# Changelog

Todos los cambios notables se documentan aquí.

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.0.0/)

> **Este es el registro completo**, entrada a entrada, en la lengua de trabajo del
> proyecto. La versión en inglés (`docs/en/changelog.md`) **no es una traducción**:
> encabeza con un resumen de qué es la línea base v0.01 —lo que necesita un
> evaluador externo— y recoge después una selección de cambios. La decisión es
> deliberada: mantener 300+ entradas de proceso interno duplicadas en dos idiomas
> fue lo que produjo el desfase que arrastrábamos, y perseguirlo cada bloque
> costaba más de lo que aportaba.

---

## [Unreleased]

### Añadido

- `fix(document,storage)`: **reclasificar con permiso de lectura, subir archivos sin ningún permiso, y radicar un borrador que nadie aprobó**
  Cierre de la deuda que el plan MCP funcional fue declarando sin arreglar. Los seis hallazgos se verificaron uno a uno contra el código antes de tocarlos, y **dos se disolvieron al mirarlos** — se dicen igual, porque un hallazgo falso que se queda en un informe hace perder el tiempo tanto como uno real que no se reporta.
  **`PATCH /documents/{id}/security-level` exigía nivel de LECTURA.** `PERM_RECLASIFICAR` con el `min_crud` por defecto (1), de modo que quien lo tuviera solo para consultar podía subir o bajar la reserva de un radicado oficial. Sus dos gemelos ya exigían 3 y su propio comentario explicaba por qué: `publicacion` usa el MISMO permiso con nivel de escritura porque «publicar y clasificar son las dos caras de la misma competencia sobre el régimen de acceso», y `disposition` se había corregido antes por lo mismo. Este era el tercer hermano y el único que se quedó en 1. No deja fuera a nadie: quien puede publicar ya necesitaba nivel 3 sobre ese permiso.
  **`POST /storage/upload` no tenía ningún gate.** Es el ÚNICO endpoint de storage-service que el gateway expone al exterior —los que sirven bytes, URL o metadata están sellados con `X-Internal-Token` (D-02)—, así que cualquier autenticado de la entidad subía archivos al almacén. Pasa a exigir `PERM_RADI` con nivel 1: el permiso no se inventa, se toma del único consumidor real (el flujo de radicación sube el anexo antes de radicar), y el nivel iguala al de `POST /documents` porque pedir aquí más que en el acto que este archivo acompaña sería exigir de más — el defecto simétrico, que además falla en silencio. **Nadie que hoy trabaje se queda fuera**, que es la comprobación obligada antes de encender un control apagado.
  **`radicar_borrador` no exigía la aprobación, y la pantalla sí.** El ciclo documentado es crear → editar → aprobar → radicar, y `/borradores` lo respeta (`canRadicar = estado === "aprobado"`), pero el servicio solo comprobaba que no estuviera ya radicado: un `POST` directo saltaba la aprobación y consumía el número igualmente. Un control que vive solo en el navegador no es un control, porque el navegador es la parte hostil (D-05). Ahora es 409 `borrador_sin_aprobar`, y no rompe nada porque la única interfaz que llama a esa ruta nunca la ofrece sin aprobar. Se corrigen de paso las descripciones de `aprobar_borrador` y `radicar_borrador` en el catálogo MCP, que decían lo contrario.
  **`PERM_RADI_ENTRADA` y `PERM_RADI_INTERNO` siguen sin limitar nada, y ahora lo dicen.** Ningún gate los consulta (0 apariciones): una entidad puede concederlos creyendo que restringe a un funcionario a radicar solo entradas, y no restringe. Las dos salidas de fondo son decisiones de producto y ninguna es inocua — **aplicarlos** es funcionalidad nueva y choca con que `PERM_RADI_SALIDA` hoy signifique «gestionar envíos» (6 gates) y no «radicar salidas»; **retirarlos** es un borrado que arrastraría las concesiones ya hechas. Mientras se decide, la migración `017` corrige lo que sí es inaceptable: la **descripción**, que es lo que un administrador lee en la pantalla de grupos antes de conceder. Cambio no destructivo, ninguna concesión se toca.
  **Dos hallazgos míos que resultaron falsos.** (1) `/vistos-buenos/decidir` «sin gate»: **sí autoriza**, y mejor que con un permiso — exige ser miembro de la cadena de revisores de ESE radicado y que sea tu turno. Autorización por-instancia, más fuerte que un permiso genérico; añadirle `PERM_VOBO` habría podido bloquear a revisores designados. Vi que faltaba `require_permission` y di por hecho que no había control. (2) `PATCH /expedientes/{id}` y `vincular` «a nivel de lectura»: no es un olvido sino una **convención coherente** — `min_crud=3` marca los actos irreversibles (cerrar, transferir, desvincular) y 1 la escritura ordinaria, igual en document-service. Que la convención use «leer» como suelo de escritura es discutible, pero es una decisión de política de todo el proyecto, no un defecto de estos endpoints.
  Y un pendiente **caducado** retirado de `auth-service/CLAUDE.md`: decía que `USUA_PERM_TRD` estaba sembrado y no se aplicaba en ningún router. Se aplica en **25**.
  *Por qué*: eran gates de seguridad que el plan declaró y no tocó porque eran de otras épicas; cerrado el plan, ya no había nada que los tapara.
  *Impacto*: quien tenga `PERM_RECLASIFICAR` solo con nivel de consulta deja de poder reclasificar (era el defecto); subir archivos exige `PERM_RADI`; radicar un borrador exige aprobarlo antes. Migración `auth-service/017` (solo descripciones). Reconstruir document-service y storage-service.
  Verificado por mutación: devolver `security-level` a nivel de lectura y quitar el gate de `/upload` caen cada uno en su test; el de upload comprueba además que la denegación ocurre **antes de tocar MinIO**, porque un gate que rechazara después habría dejado el fichero subido y solo se habría notado en la factura.

- `fix(auth,mcp)`: **seis permisos de trámite que el código exigía y que nadie podía tener — y el guardián que debía cazarlos llevaba desde siempre saltándose**
  Hallazgo de la **verificación extremo a extremo** del plan MCP funcional, con el stack levantado y un tenant real: `tenant_demo.auth_permissions` traía 20 filas y el código exigía **seis** que no estaban — `PERM_TRAMITAR`, `PERM_VOBO`, `PERM_ANULAR`, `PERM_CERRAR_EXP`, `PERM_INFORMAR`, `PERM_AGENDAR`. Un permiso que no está en esa tabla no se puede conceder a ningún grupo, así que `resolve_effective_permissions` nunca lo devuelve y el gate niega a todo el mundo salvo a ROOT, que hace bypass. **Efecto real**: los doce tipos de transacción sembrados y las cinco escrituras de flujo (`assign`, `transfer`, `complete`, `devolver`, `rollback`) eran ROOT-only. Falla cerrado —el lado correcto— y aun así es capacidad que existe en el código y que ninguna entidad podía repartir entre sus funcionarios.
  Es **exactamente el defecto `RADI_CREAR`** que abrió este plan, a mayor escala. Aquel se cazó porque alguien fue a usar la tool; estos, porque se levantó el stack.
  **Y el guardián escrito para impedirlo estaba apagado.** `test_todo_permiso_del_catalogo_existe_en_el_sistema` vivía en la imagen de mcp-server, que **no monta el repositorio**: no encontraba las migraciones de auth-service y hacía `pytest.skip` en cada corrida. En el resumen, «11 passed, 1 skipped» se lee igual de bien que «12 passed». Un `skip` que oculta un defecto real es otro mecanismo que reporta éxito sin verificar nada — y este llevaba el nombre del defecto que dejaba pasar.
  Se mueve a `shared/tests/test_permisos_existen.py`, el único carril de CI que ve el repositorio entero (allí el `skip` deja de ser posible) y **se amplía a las tres fuentes de exigencia**: el catálogo MCP, los `require_permission` de cualquier router de cualquier servicio, y la columna `requires_permission` de las filas sembradas. Los seis que faltaban **no los declara el catálogo**: los declara el gate de un router y una fila de `transaction_types`. Mirar una sola fuente es cómo sobrevivieron a un control que existía para cazarlos. Las tres se derivan del código; no hay ninguna lista a mano.
  Los nombres **no se inventan**: se toman literalmente de lo que el código y las migraciones de workflow-service ya exigen. Renombrar el gate habría sido la corrección de menos riesgo aparente y la peor — dejaría las filas de `transaction_types` de las entidades ya provisionadas apuntando a un permiso que vuelve a no existir.
  *Por qué*: exponer el trámite por MCP amplifica un gate que nadie podía satisfacer; el plan exigía cerrar antes lo que amplificaba.
  *Impacto*: migración `auth-service/016_permissions_tramite.sql` (aditiva, `ON CONFLICT DO NOTHING`); hay que **correr el runner de auth-service en cada tenant existente** o los flujos siguen siendo ROOT-only. La suite de mcp-server pasa de «142 passed, 1 skipped» a **143 passed, 0 skipped**.
  Verificado por mutación: retirar la migración pone en rojo los dos tests nuevos, y retirar solo `PERM_VOBO` —que no lo declara ningún gate, solo una fila sembrada— pone en rojo exactamente el que mira esa fuente.

- `test(e2e)`: **el criterio de éxito del plan, comprobado contra el stack levantado**
  El plan decía que su criterio de éxito no era la suma de bloques sino el recorrido completo con el sistema en marcha. Verificado con `docker compose` y un operador real de `tenant_demo`: el gateway **enruta** `/api/v1/mcp/` (200, frente a `route_not_found` en los controles `/api/v1/tenants/` y una ruta inventada); el marco de alcance devuelve 15 operaciones ejecutables, 29 consultables y 1 denegada **con su motivo**, derivadas de los permisos reales; invocar una escritura devuelve una propuesta firmada y la base **no cambia** (0 radicados); confirmarla **sin la frase** da `confirmacion_requerida` y la base **sigue sin cambiar**; con la frase da 201 y `2026-DEMO-E-000001`; reintentar el mismo token da `propuesta_consumida`; y el radicado queda con el `registered_by` del usuario real y su asiento `document.radicado_created` en `audit_log` con el actor correcto — **el mismo resultado que a mano**. Control negativo: una tool cuyo permiso el usuario no tiene no aparece en el catálogo (44 de 45) e invocarla igualmente da 403 sin tocar la base.
  *Pendiente declarado*: el turno del asistente con el modelo real exige `ANTHROPIC_API_KEY` y no se ha ejercido; lo verificado es todo el camino determinista, que es donde viven los gates.

- `feat(frontend)`: **dictado por voz — y el botón no aparece si dictar significaría mandar la voz a un tercero**
  Último bloque del plan MCP funcional. `VoiceInput.svelte` en el compositor del asistente: reconocimiento en `es-CO`, resultados parciales pintados sobre una base para que la frase no se repita palabra a palabra, y errores que dicen lo que son —permiso, micrófono, idioma— en vez de un «no se pudo dictar» que deja a la persona sin saber qué hacer.
  **La premisa del plan era falsa y por eso el diseño cambia.** El plan justificaba usar el reconocimiento del navegador con que «el audio no sale del equipo», y decía que así cumplía «mejor» el objetivo de soberanía que el `POST /assistant/transcribe` con Whisper. En Chrome y Edge —los navegadores con los que se trabaja— la Web Speech API clásica **envía el audio a un servicio de reconocimiento de Google**. ADR-020 había decidido lo contrario y por escrito: «STT en servidor con Whisper; el audio no sale a terceros», «soberanía del dato: LLM y STT locales». Implementarlo tal cual habría mandado la voz de un funcionario público a un tercero en un sistema que gestiona radicados reservados, con una justificación escrita que afirmaba justo lo contrario.
  Así que el botón **no se ofrece porque la API exista**: se ofrece solo cuando el navegador puede reconocer **en el dispositivo** y lo confirma él mismo (`SpeechRecognition.availableOnDevice` para ese idioma, más `processLocally`). Un modelo `downloadable` no vale: hasta que esté, el navegador puede recurrir al servicio remoto sin decir nada. Y la comprobación es fail-closed por construcción — si la API de disponibilidad no existe, o revienta, el resultado es que no hay botón. El peor caso posible es quedarse sin dictado; nunca dictar hacia fuera sin que nadie lo sepa. Es la degradación honesta del plan (11.2) aplicada a la soberanía, no solo al soporte.
  **El dictado nunca envía ni confirma** (11.3): rellena el compositor y ahí se acaba. Una propuesta de escritura se aprueba con la mano, en su tarjeta — una acción con consecuencias sobre un registro oficial no puede dispararse por lo que el reconocedor creyó oír. Hay un test que comprueba que el componente no emite ningún otro evento.
  ADR-020 queda anotada con lo implementado y con su límite: el `transcribe` con Whisper sigue **sin construir** y es el único camino soberano para todos los navegadores. Se declara como pendiente, no como algo ya resuelto por la vía del navegador.
  *Por qué*: era el último punto del plan, y su justificación escrita no se sostenía.
  *Impacto*: dictado disponible donde el navegador reconoce localmente; invisible donde no. 1488 tests vitest, `svelte-check` 0/0.
  Verificado por mutación: aceptar la API por el mero hecho de existir, dar por buena una disponibilidad `downloadable` y dejar de pedir `processLocally` caen cada uno en su test — el primero en cuatro.

- `feat(mcp,frontend)`: **el asistente prometía por escrito una confirmación que no existía — ahora la tarjeta está construida**
  El panel del asistente declaraba que «toda acción de escritura pedirá confirmación explícita» sobre una interfaz **que no estaba hecha**: no había render de propuestas, ni diálogo, ni botones. Y el backend lo resolvía por el otro lado, prohibiendo toda escritura (v1 solo-lectura). Las dos mitades eran coherentes entre sí y ninguna cumplía lo prometido: la frase describía un mecanismo, y el mecanismo estaba sin construir.
  **Backend**: el asistente deja de excluir las tools de escritura del conjunto que ve el modelo. Una `tool_use` sobre una de ellas **no llega al dispatcher**: se convierte en una propuesta firmada (`write_gate.preparar`) que viaja en `AssistantMessageResponse.propuestas` — campo aditivo, sin despliegue coordinado. Recortar la lista nunca fue lo que hacía seguro el sistema (el gate vive en `_execute_tool`); lo que hacía era dejar al modelo sin manera de decir «puedo preparártelo».
  **El anclaje del historial**, que es lo que impide la mentira más probable: el `tool_result` que vuelve al modelo dice `ejecutado: false`, `status: "propuesta_emitida"` y «NO SE HA EJECUTADO NADA… no encadenes acciones que dependan de su resultado». Sin él, el modelo lee su propia `tool_use` como si hubiera surtido efecto y redacta «he radicado el documento» — un hecho falso sobre un registro oficial, que es la regla de no-invención aplicada a sus propios actos. El token **no** viaja al modelo: no lo necesita, y meterlo en su contexto lo pondría al alcance de cualquier cosa que consiga que lo repita.
  **La frase de los actos irreversibles, exigida por el backend a partir del TOKEN.** Un clic no basta cuando el acto no se deshace, y quién dice que este acto es irreversible no es el cliente: es la propuesta firmada, de la que sale la tool. Así no hay manera de confirmarlo declarando que no lo era. La interfaz deshabilita el botón por cortesía —para no mandar a la persona contra un error previsible—, pero la barrera está en `write_gate`: una frase validada solo en el navegador es teatro, porque el navegador es la parte hostil (D-05). La elicitación por stdio pide la frase en su propio esquema, para que los actos irreversibles no sean justamente los únicos que ese camino no puede aprobar.
  **Frontend**: `ProposalCard.svelte` en el dock, proxy `/asistente/api/confirmar`, y estado por propuesta en el store. Una confirmación **por acto**, también dentro de una cadena — un botón que aprobara «todo lo pendiente» sería el booleano global que este diseño retiró, con otra forma. La tarjeta la redacta el **catálogo**: `operacion` y etiquetas salen de la declaración de la tool, y los valores se pintan como texto y nunca como HTML — los compuso el modelo a partir de una conversación que puede incluir el contenido de un radicado de Entrada, es decir, texto de un tercero, y es el último sitio del sistema donde convendría relajar el escapado.
  **Tres defectos propios, cazados por sus tests.** (1) Al pasar de «no ejecutar escrituras» a «firmarlas», `coerce_args` empezó a correr en ese camino y su `ProposalError` se escapaba: un argumento que el modelo puede corregir solo tumbaba el turno entero con un 500. (2) La allowlist del proxy de confirmación, hecha para impedir que se cuelen argumentos, se llevaba por delante la **frase** — y con ella la única pieza que sí tenía que pasar: ningún acto irreversible se habría podido confirmar desde la web. (3) El test del nivel CRUD **sobrevivió a su mutación** porque comprobaba la primera barrera (el conjunto ofrecido) dos veces y la segunda (`authorize`) ninguna; se sustituye por uno que rompe la capa de delante a propósito, que es la única forma de ejercer una defensa en profundidad.
  *Por qué*: una promesa de confirmación sin interfaz detrás es peor que no prometer nada — la persona confía en un control que no existe.
  *Impacto*: el asistente puede preparar radicaciones, expedientes, vinculaciones, asignaciones y borradores; **ninguno se ejecuta sin que alguien lo apruebe en su tarjeta**. Reconstruir mcp-server. Frontend: 1472 tests, `svelte-check` 0/0.
  Verificado por mutación: despachar la escritura en vez de proponerla, decir en el `tool_result` que sí se ejecutó, dejar escapar el `ProposalError`, saltarse la segunda barrera de autorización y reenviar el cuerpo íntegro en el proxy caen cada uno en su propio test.

- `fix(mcp,gateway)`: **la cabecera de permisos que nadie producía, el ajuste de entorno que nadie consultaba, y el marco de alcance que solo veía el asistente web**
  Este bloque cierra la Parte 0 del plan (marco de alcance) por la superficie MCP y el bloque 9 (exposición a clientes externos). Los dos comparten causa: **la autorización se leía de sitios que el backend no consulta.**
  **Tres puntos leían `X-User-Permissions`**, una cabecera que ningún componente produce — el gateway inyecta `X-User-Id`, `X-Username`, `X-Tenant-Slug`, `X-User-Roles`, `X-Canal` y `X-Real-Ip`, y la descarta si la manda un cliente, que está bien porque sería forjable. Llegaba siempre vacía: `GET /api/v1/mcp/tools` —el endpoint que existe precisamente para decirle a alguien qué puede hacer— le contestaba a todo el mundo con las tools sin permiso, y el transporte streamable ofrecía lo mismo. `X-User-Roles` **sí** llega, de modo que un ROOT lo veía todo y el defecto quedaba invisible justo para quien podía detectarlo.
  **Y su gemelo por stdio era peor**: `MCP_USER_PERMISSIONS`/`MCP_USER_ROLES` sí se podían poner, así que quien los configuraba creía estar concediendo algo. Lo único que lograba era ver ofrecidas tools que el backend le negaría al usarlas — anunciar lo inutilizable, servido por la propia configuración. Los dos ajustes se retiran. La identidad sale de `GET /auth/me` en los tres transportes, con el `permission_levels` incluido: `build_tools` no pasaba `levels`, así que tener `PERM_RADI` para *leer* mostraba `tipificar_radicado`, que exige nivel de creación.
  **El marco de alcance llega a MCP**: tool `mi_alcance` y resource `orfeo://alcance`, los dos derivados de `build_scope` — dos puertas al mismo dato, no dos versiones. Se ofrece **siempre**, aunque el usuario no alcance ninguna escritura, y ahí está la mitad que faltaba: el conjunto de tools dice qué se puede hacer y **calla** el resto, y callar es exactamente cómo un límite acaba descubriéndose fallando. El marco dice qué no se puede, por qué, y por dónde sí.
  **El gateway enruta `/api/v1/mcp/`**, que antes quedaba fuera a propósito. Eso dejaba a medias la decisión de «un solo catálogo y un solo gate para el asistente integrado y para clientes MCP externos»: los externos no tenían por dónde entrar salvo hablando con el servicio a espaldas del gateway, sin validación JWT ni `X-Tenant-Slug`. Se puede exponer **porque el gate de escritura ya no depende del cliente**: invocar una escritura devuelve una propuesta firmada y no ejecuta nada. Con `X-Confirm-Write` —un booleano que cualquiera fabricaba— enrutar esto habría sido abrir la escritura al exterior. El transporte streamable se monta también en `/api/v1/mcp/stream` para caber por la tabla de ruteo sin abrirle al proxy una forma de ruta fuera de `/api/v1/...`.
  **Un guardián nuevo en `shared/tests/`**: ninguna tool puede apuntar a `/api/v1/mcp` ni `/api/v1/assistant`. Hasta ahora era imposible por accidente —el gateway no los enrutaba, así que habría sido un 404—, y al exponerlos esa protección de hecho desaparece. Una tool así abriría recursión (invoke despachando invoke, con el token del usuario en cada salto) y, peor, saltaría el gate: llamar a `/proposals/execute` como si fuera una lectura del catálogo ejecutaría una propuesta sin que nadie confirmase nada.
  **Un hueco propio, cazado por mutación y corregido.** Mi control negativo —«mandar la cabecera forjada no concede nada»— **sobrevivió** a volver a leerla: pasaba, pero por otra razón, porque el permiso colado llega sin su nivel y `visible_tools` compara también el nivel. Defensa en profundidad real y a la vez un test que afirmaba una cosa comprobando otra; los dos defectos se habrían compuesto en cuanto alguien tocara `levels`. Se sustituye por un control **estructural** sobre el AST —la cabecera y los ajustes no pueden aparecer como parámetro, nombre, atributo ni literal en ningún módulo de `app/`— y se inspecciona el árbol y no el texto a propósito: los ficheros están llenos de comentarios que la nombran para explicar por qué se retiró, y un `grep` habría encontrado esas explicaciones y dado el hallazgo por bueno.
  *Por qué*: un límite legítimo que solo se descubre fallando hace perder el tiempo a dos personas y parece una avería.
  *Impacto*: `/api/v1/mcp/` pasa a estar enrutado por el gateway (hereda su JWT). `MCP_USER_PERMISSIONS` y `MCP_USER_ROLES` **dejan de existir**: quien los tuviera puestos no pierde nada, porque no concedían nada. `build_tools` es `async` y recibe el cliente del gateway. Reconstruir mcp-server y api-gateway. Corregidas afirmaciones falsas en `docs/{es,en}/api.md` e `integraciones-externas.md`.
  Verificado por mutación: quitar `levels`, retirar `mi_alcance`, volver a leer la cabecera, restaurar el ajuste de entorno y quitar el montaje de `/api/v1/mcp/stream` caen cada uno en su propio test.

- `feat(mcp)`: **instrumentos y archivo físico — el catálogo cierra en 45 tools, todas de consulta**
  **5 tools nuevas** (bloque 8, solo lectura): `consultar_serie` y `consultar_retencion` (qué tiempos y qué disposición gobiernan lo que se clasifique en una serie de la TRD), `ubicacion_expediente` (dónde está físicamente, con su signatura), `listar_prestamos` y `reporte_radicacion`. Con esto el catálogo llega a **45 tools, 16 de escritura**, y las 45 cruzan contra el gate de su endpoint: ninguna queda en la holgura del control de cobertura.
  **Dos descripciones que acotan lo que la tool NO es**, porque el modelo tiende a usar lo que tiene a mano: `consultar_retencion` es un CÁLCULO sobre una serie y una fecha de cierre, no la disposición registrada del expediente concreto; y `reporte_radicacion` devuelve conteos por tipo, estado, mes y dependencia —ni número, ni asunto, ni remitente—, así que se dice que no es un listado, para que un agregado no se presente como si fuera un inventario. `ubicacion_expediente` dice dónde está el expediente y no qué contiene: el backend no le aplica no-read-up a propósito (es gestión de custodia, ortogonal al clearance de lectura), y la descripción refleja ese límite en vez de sugerir que devuelve contenido.
  **Primer GET con un obligatorio fuera de la ruta.** `consultar_retencion` exige `closed_at` como query. El reparto por `location="auto"` ya lo resolvía, pero ese caso no estaba ejercido por ningún test: si la fecha acabara en el cuerpo, el backend devolvería 422 por un argumento que el usuario sí había dado. Ahora hay un test que comprueba las tres cosas —la serie en la ruta, la fecha en la query y el GET sin cuerpo—; verificado por mutación moviendo el parámetro al cuerpo y renombrándolo.
  *Por qué*: son las consultas que sostienen los actos de los bloques anteriores — tipificar sin poder mirar la retención de la serie es firmar a ciegas.
  *Impacto*: el catálogo pasa de 40 a 45 tools; ninguna escritura nueva. Reconstruir la imagen de mcp-server.

- `feat(mcp)`: **el ciclo de la respuesta — antecedentes, borrador y radicación — y un 422 que dejaba de descubrirse fallando**
  **10 tools nuevas** (bloque 7 del plan). Recuperación: `buscar_antecedentes` (radicados anteriores parecidos, cada uno con su número para verificarlo en la fuente) y `redactar_con_antecedentes` (texto apoyado en el archivo, **con citas**, que no escribe nada). Respuesta: `listar_respuestas` y `crear_respuesta_vinculada`. Borradores: `listar_plantillas`, `listar_borradores`, `crear_borrador`, `editar_borrador`, `aprobar_borrador` y `radicar_borrador`. El catálogo queda en **40 tools, 16 de escritura**.
  El borrador es la pieza que faltaba para poder **proponer sin comprometer**: no tiene número, no entra en el índice y se edita o se abandona sin consecuencia; solo `radicar_borrador` consume el número, que es inmutable. Por eso los dos actos declaran `destructive` distinto aunque toquen el mismo objeto.
  **Un límite que dejaba de descubrirse fallando.** `buscar_antecedentes` admite el asunto o el radicado de referencia, nunca ambos ni ninguno: lo impone `AntecedentesRequest` con un `model_validator` y JSON Schema no lo expresa con las piezas del catálogo. Anunciados los dos como simplemente opcionales, el modelo manda ambos —que es lo natural cuando tiene los dos datos a mano— y recibe un 422 **después** de haber hecho esperar a la persona. Se declara con `exactly_one_of` en el catálogo y `coerce_args` lo rechaza antes de que salga la petición. Es el mismo principio que el `parametro_no_declarado`, en pequeño: el límite se dice, no se tropieza.
  Con él va su invariante, porque una declaración también se corrompe en silencio: un grupo que nombra un parámetro inexistente rechaza **toda** llamada a la tool con un mensaje sobre un argumento que el modelo no puede enviar, y un parámetro obligatorio dentro del grupo no admite ninguna llamada válida. Ninguna de las dos la caza el test de comportamiento, que solo prueba los grupos tal y como están hoy.
  **Un aviso que va en la propia descripción de la tool**: el ciclo documentado del borrador es crear → editar → aprobar → radicar, pero `POST /borradores/{id}/radicar` **no comprueba** la aprobación — solo que no esté ya radicado. La aprobación es hoy una marca de procedimiento, no un control. Se expone `aprobar_borrador` porque el procedimiento existe, y se dice lo que la marca no hace, para que el asistente no la presente como una barrera inexistente. No se corrige aquí (ADR-019: es de la épica de document-service).
  *Por qué*: «prepárame la respuesta con antecedentes» era parte explícita del criterio de éxito de extremo a extremo, y sin borradores solo quedaba radicar a la primera.
  *Impacto*: el catálogo pasa de 30 a 40 tools; ninguna quedó sin cruzar contra el gate de su endpoint. Reconstruir la imagen de mcp-server.
  Verificado por mutación: quitar el enforcement de `exactly_one_of`, quitar su declaración de la tool, contar las claves crudas en vez de los valores efectivos y corromper un nombre del grupo caen cada uno en su propio test; y declarar `listar_plantillas` «sin permiso» cae en el guardián compartido, que es la prueba de que las tools nuevas están dentro del cruce y no fuera de él.

- `fix(workflow,mcp)`: **devolver y revertir un radicado no exigían ningún permiso**, y el bloque de flujos era el que faltaba para cerrar un trámite
  `POST /workflows/{id}/devolver` y `POST /workflows/{id}/rollback` no tenían **ningún** `require_permission`, mientras sus tres hermanas del mismo router —`assign`, `transfer` y `complete`— exigen `PERM_TRAMITAR` sobre el mismo radicado. Mismo fichero, mismo objeto, solo algunas gateadas: es omisión, no diseño, y ellas son el control positivo. Cualquier autenticado de la entidad podía sacar un radicado de la dependencia que lo tramita y mandarlo de vuelta al origen, o deshacer la última asignación hecha por otra persona.
  **Y un tercer hueco, este sí documentado como decisión.** En `execute_transaction`, un tipo con `requires_permission = NULL` era «abierto»: se ejecutaba sin permiso alguno. Había un test que lo afirmaba expresamente, así que se cambia **declarándolo**, no borrándolo. El motivo: `transaction_types` es un catálogo del tenant, de modo que una fila con la columna vacía —sembrada por una migración futura, o por alguien con acceso a la base— quedaba ejecutable por cualquiera. Dejar un campo en blanco no puede ser la manera más fácil de abrir una puerta. Ahora cae al suelo `PERM_TRAMITAR`. **No cambia nada observable hoy**: los 12 tipos sembrados declaran el suyo. Y el suelo va en el servicio, no como gate de ruta, porque el permiso aquí es POR TIPO: exigir `PERM_TRAMITAR` a todas rompería a quien tiene `PERM_VOBO` para dar vistos buenos y nada más.
  **8 tools nuevas** (bloque 6, el que permite cerrar un trámite de principio a fin, donde antes no había ni una sola de distribución): `pendientes_equipo`, `tipos_transaccion`, `hoja_ruta_radicado`, `paso_actual`, `asignar_radicado`, `trasladar_radicado`, `completar_paso` y `ejecutar_transaccion`. El catálogo queda en **30 tools, 11 de escritura**.
  `completar_paso` declara su parámetro como lo que es —`step_id`, el id del PASO, no el del radicado— en vez de heredar el común: esa ruta cuelga de otra entidad que el resto del router y era el error más probable de todo el bloque.
  **Una excepción que hubo que declarar, no saltarse.** `ejecutar_transaccion` resuelve su permiso en tiempo de ejecución, así que no hay decorador que comparar y el guardián `test_permisos_declarados.py` la marcaba como «exige un permiso que su endpoint no pide». Se resuelve con un campo explícito del catálogo (`permiso_en_runtime`) **más un test propio que comprueba que esa marca no esté puesta sobre una ruta que sí tiene gate** — porque así es como una excepción legítima acaba convertida, con el tiempo, en la manera de callar hallazgos.
  *Por qué*: exponer una operación por MCP la hace más alcanzable, así que los gates flojos que amplificaría se cierran antes, no después.
  *Impacto*: `devolver` y `rollback` exigen `PERM_TRAMITAR` (quien no lo tenga recibirá 403 donde antes pasaba). Reconstruir las imágenes de workflow-service y mcp-server. Dos afirmaciones falsas corregidas en `docs/{es,en}/api.md`: la autorización de `devolver` y el «tipo abierto».
  Verificado por mutación: quitar cada gate, devolver el `NULL` a «abierto» y usar la marca de permiso-en-runtime donde sobra caen cada uno en su propio test.

- `feat(mcp)`: **tres tools se ofrecían a quien iba a recibir un 403 — y ahora hay quien lo vigile**
  Al ampliar el catálogo (bloques 4 y 5 del plan: de 9 tools a 22) salió el reflejo exacto del defecto de `RADI_CREAR`. `consultar_expediente`, `indice_expediente` y `consultar_fuid` declaraban **«sin permiso»** sobre endpoints gateados con `USUA_PERM_EXPEDIENTE`. Allí se escondía algo utilizable; aquí se **anunciaba algo inutilizable**, que es peor por lo que este proyecto se comprometió a hacer: el marco de alcance existe precisamente para que nadie descubra un límite fallando, y un catálogo que promete lo que el backend niega lo convierte en una lista de buenas intenciones.
  El invariante que ya existía comprueba que el permiso **exista** en las migraciones de RBAC — habría cazado `RADI_CREAR` y no caza esto: un `None` es un nombre perfectamente existente. Lo que faltaba es comparar contra **el gate del endpoint concreto**, y para eso hacen falta los dos árboles a la vez. Ahora lo hace `shared/tests/test_permisos_declarados.py`, con un test por sentido —declarar de menos y declarar de más son dos fallos distintos con dos víctimas distintas— y un tercero que mide la **cobertura del cruce**, sin el cual los otros dos serían decorativos: si el parseo dejara de casar rutas, todo se saltaría y quedarían verdes para siempre.
  Su gemelo, `test_rutas_mcp_alcanzables.py`, cierra el hueco que **ya se coló una vez**: la carga masiva se construyó entera contra endpoints que existían y el gateway no tenía `/api/v1/batch/` en su tabla de ruteo, así que devolvía `404 route_not_found`. Una tool cuya ruta no esté enrutada se ofrece, se propone, se confirma… y falla. Los dos viven en `shared/tests/` porque es el único carril de CI que ve el repositorio entero: el contenedor de mcp-server no tiene el código del gateway y el del gateway no tiene el catálogo. Ninguno de los dos mantiene una lista propia — ambos lados se derivan del código.
  **13 tools nuevas.** Radicación y clasificación: `consultar_por_numero` (el número es como lo nombra la gente; el id no sale en el sello), `listar_anexos`, `consultar_disposicion`, `actualizar_radicado`, `tipificar_radicado`, `listar_tipos_documentales`. Expedientes: `crear_expediente`, `buscar_expedientes`, `vincular_radicado`, `actualizar_expediente`, `cerrar_expediente`, `consultar_foliado`, `hoja_ruta_expediente`. Los parámetros se verificaron contra los routers reales, no contra la tabla del plan — y ese cruce es el que destapó tanto el hueco de la tipificación como estos tres permisos.
  **Dos exclusiones deliberadas, dichas en voz alta**: `actualizar_expediente` declara exactamente los tres campos de `ExpedienteUpdate` y ni uno más, porque ese schema lleva `extra="forbid"` y `status` está retirado allí a propósito (era una ruta alterna al cierre sin auditoría ni disposición); y la vinculación **masiva** (hasta 500 radicados) se queda fuera mientras su tarjeta de confirmación no pueda leerse — una tarjeta que dice «500 radicados» no permite confirmar con conocimiento de causa, que es justo para lo que existe.
  **Dos tests existentes se pusieron en rojo, y por la razón correcta**: pasaban sobre la premisa equivocada (el usuario de pruebas solo tenía `PERM_RADI` y el catálogo no exigía más). Corregidos concediendo el permiso real, no relajando la comprobación.
  *Por qué*: ofrecer una capacidad que el backend va a negar hace perder el tiempo a la persona y convierte un límite legítimo en una avería aparente — el defecto que este plan viene a corregir, cometido dentro del propio plan.
  *Impacto*: el catálogo pasa de 9 a 22 tools (6 de escritura, todas por propuesta firmada). `cerrar_expediente` es el único irreversible que entra, con `crud >= 3`, porque «archivar» era parte explícita de lo pedido. Reconstruir la imagen de mcp-server.
  Verificado por mutación: devolver una tool a «sin permiso» y declarar un permiso inexistente caen cada uno en su test; apuntar una tool a una ruta no enrutada cae en el suyo.

- `feat(document,mcp)`: **tipificar un radicado no se podía deshacer ni rehacer: el tipo documental solo existía al radicar**
  Salió al verificar el bloque 4 del plan MCP contra los routers reales, en vez de dar por buena la tabla del plan. `doc_class` —el tipo documental— **solo se fijaba al crear el radicado** y en la importación masiva; `PATCH /api/v1/documents/{id}` aceptaba `subject`, `dest_dept`, `observations`, `status` y `pages`, y el tipo no estaba. **No había ningún endpoint que lo hiciera, ni por esta vía ni por otra.** Un documento mal tipificado en ventanilla —o radicado sin tipificar, que el schema permite— se quedaba así para siempre, y con él la serie TRD que gobierna su retención y su disposición final (Ley 594/2000 art. 24). No era una restricción deliberada: era un hueco. Y explica por qué el frontend solo sugiere tipo **al radicar** (`TrdSuggestChips`): después no había a dónde mandar la corrección.
  **Retipificar revalida los metadatos contra la plantilla del tipo nuevo.** Es el corazón del arreglo, no un extra: sin eso, retipificar habría sido un `UPDATE doc_class` que deja los campos variables del tipo anterior colgando, y el radicado habría pasado a declarar una conformidad con una plantilla que ya no era la suya. Se usa **la misma comprobación que hace `create`**, a propósito — un radicado retipificado tiene que ser indistinguible de uno radicado así desde el principio. Los metadatos se **reemplazan enteros**, no se fusionan (una fusión deja residuo del tipo anterior, que es exactamente lo que hace que un expediente deje de cuadrar), y la versión de plantilla usada se reescribe siempre, **incluido el `NULL`** cuando el tipo nuevo no tiene plantilla activa; sin ese `NULL` explícito el radicado seguiría apuntando a la plantilla del tipo anterior. Enviar solo `metadata` también revalida: si no, la validación obligatoria de la radicación sería puenteable con un PATCH posterior.
  **El gate del acto nuevo, sin tocar el de los que ya existían.** El PATCH está gateado con `PERM_RADI` a nivel 1 —de lectura— y así se queda: subirlo entero habría roto a todo el que hoy corrige un asunto o un número de folios, y alinear ese umbral es una decisión propia de la épica, no un efecto colateral de este cambio. Pero `doc_class` decide cuánto se conserva el documento y si acaba destruido, así que se comprueba aparte con `crud >= 3`. El acto nuevo entra con su nivel correcto; los antiguos conservan el suyo.
  **Un defecto propio, cazado por mis propios tests**: añadí el modo de fallo (validación de metadatos) sin su respuesta — `MetadataValidationError` no estaba en el `except` del PATCH, solo en el del POST, así que la validación salía como **500**. Una validación sin su mapeo de respuesta no es una validación, es una caída.
  **Y un test que no discriminaba**, cazado por mutación: comprobaba que `metadata_template_id` apareciera en el SQL del `UPDATE`, y lo encontraba en el `RETURNING` — daba verde con la escritura puesta **y** quitada. Corregido a `metadata_template_id = $N` y a comprobar el valor enviado. Enésima aparición del mismo patrón: la cadena buscada aparece en otro sitio del mismo texto.
  *Por qué*: el tipo documental no es un campo descriptivo, es lo que ata el radicado a su serie TRD; dejarlo inmutable por omisión convierte un error de ventanilla en una retención equivocada para toda la vida del documento.
  *Impacto*: `PATCH /api/v1/documents/{id}` acepta `doc_class` y `metadata`; `422 metadata_validation_failed` cuando no encajan; `403` al tipificar sin nivel de creación. Nuevas tools MCP `tipificar_radicado` (escritura, `min_crud=3`, con su vía alternativa declarada) y `listar_tipos_documentales`, que es de donde sale el nombre que la primera espera. Reconstruir las imágenes de document-service y mcp-server.
  Verificado por mutación, cada control en rojo por su propio test: quitar el gate de nivel, quitar la revalidación, no reescribir la versión de plantilla, y sacar `doc_class` de los campos auditables.

- `feat(mcp)`: **confirmar «algo» no era confirmar «esto» — la escritura pasa a ser una propuesta firmada**
  El gate de escritura de la capa MCP era `X-Confirm-Write: true`: un booleano que cualquier cliente podía poner y que no ataba la confirmación a ningún acto concreto. Valía igual para consultar que para radicar. El binding MCP directamente lo daba por puesto (`confirm_write=True` fijo, «porque el human-in-the-loop lo aporta el cliente MCP» — una garantía convertida en suposición sobre software ajeno), y el asistente prohibía toda escritura. **Tres comportamientos distintos según por dónde entraras** es garantía de que alguno está mal, y el que estaba mal era el que menos se miraba.
  Ahora hay **uno solo para los tres transportes**: invocar una escritura **devuelve una propuesta y no ejecuta nada**. La propuesta es un token opaco firmado con HMAC-SHA256 que lleva dentro el acto completo —tool y argumentos ya validados—, y al confirmar los argumentos se toman **del token**, nunca del cuerpo de la petición: «lo que se firmó» es «lo que se ejecuta» por construcción, no por disciplina. Que una escritura devuelva una propuesta **en vez de un 412** es deliberado: un cliente antiguo u hostil que fabrique cualquier cabecera obtiene una descripción del acto, no un efecto.
  **Firmado y no guardado en memoria** porque el único almacén del servicio es un diccionario por proceso, declarado no válido para varias réplicas: con memoria, proponer en una réplica y confirmar en otra rompería el botón que la persona sí pulsó.
  **La reautorización al confirmar no es redundante.** Entre proponer y confirmar median los segundos que tarda alguien en leer la tarjeta, y en ese hueco le pueden haber retirado el permiso: la propuesta acredita *qué* se aprobó, no que quien la presenta siga pudiendo hacerlo. Confundir esas dos cosas es lo que convierte un token en un salvoconducto. Y **la tarjeta la redacta el catálogo, no el modelo**: un texto generado podría describir mal el acto que se está firmando, y eso sería ingeniería social sobre la propia persona que confirma.
  **Para clientes MCP externos**, que no tienen ni tarjeta ni endpoint propio: la tool genérica `confirmar_accion(token)` —ofrecida solo a quien alcanza alguna escritura, porque sugerírsela a quien no puede escribir insinúa un poder que no tiene—, `annotations` estándar (`readOnlyHint`, `destructiveHint`) y **elicitación** en banda donde el transporte es bidireccional (stdio). La aprobación por elicitación no ejecuta de otra manera: pasa por el mismo token firmado, así que se reautoriza y se consume igual — si ejecutara por su cuenta sería la auto-confirmación retirada, con otro nombre.
  **Dos defectos encontrados al implementarlo**, ambos del tipo «mecanismo que informa de éxito sin verificar nada»: el endpoint de ejecución **nunca llegó a insertarse** —una verificación por `grep` de una cadena que también aparecía en un comentario dio por bueno lo que no existía; ahora se verifica por el símbolo definido—, y las propuestas emitidas por el transporte MCP **nacían muertas**: se firmaban con una clave del diccionario de sesión que nunca existió, así que el `sub` iba vacío y la verificación las rechazaba después como ajenas. Salía una propuesta de aspecto impecable e inconfirmable; el fallo solo aparecía al pulsar el botón.
  `destructive` se declara **explícitamente** en toda tool de escritura, con default `None` a propósito: con default `False`, olvidarlo habría equivalido a afirmar «este acto se deshace» sin que nadie lo comprobara, y un cliente externo habría bajado su fricción confiando en esa afirmación fantasma. Un invariante lo exige.
  *Por qué*: un booleano en una cabecera no ata la confirmación a un acto, y una garantía que depende de que el cliente pregunte no es una garantía.
  *Impacto*: `X-Confirm-Write` deja de leerse (quien la envíe recibe una propuesta igualmente). Nuevos `POST /api/v1/mcp/proposals/execute` y `POST /api/v1/assistant/confirmar` —este último bajo `/assistant/` porque es el único prefijo del mcp-server que el gateway enruta—. `MCP_PROPOSAL_SECRET` es **obligatoria**: el servicio no arranca sin ella (una firma con clave vacía la reproduce cualquiera y dejaría el gate decorativo). Esto obliga a construir `mcp-server` con el contexto en la raíz del repo para instalar `shared/`. Reconstruir su imagen.
  *Límites declarados, no disimulados*: el consumo por `jti` vive en el proceso —cubre el doble clic y el reintento, no el multi-réplica—, y `Idempotency-Key` se emite como gancho, no como garantía; en HTTP streamable (`stateless`) no hay canal de vuelta para elicitar y queda el camino de dos tools.

- `fix(mcp)`: **Un identificador manipulado podía hacer que el asistente consultara otra cosa con las credenciales del usuario**
  Cuando el asistente consulta un documento por su identificador, ese identificador se insertaba en la dirección interna **sin sanear**. Un valor manipulado con `../` no producía un error: hacía que el sistema consultara **un endpoint distinto del previsto, con las credenciales de quien preguntaba**. Es el mismo problema de direcciones torcidas que se corrigió en la puerta de entrada, reaparecido un paso más adentro. Corregido, y con doble barrera: los identificadores se validan antes, así que la mayoría ni llega a la segunda.
  Además, la capa de traducción solo sabía hacer dos tipos de petición, y la mayoría de las operaciones del sistema usan un tercero. Sin eso, buena parte de las tareas no se podían expresar siquiera.
  *Por qué*: una capa que traduce peticiones y no sanea lo que inserta deja de ser una fachada y pasa a ser una puerta.
  *Impacto*: interno; ninguna funcionalidad cambia de comportamiento.

- `feat(mcp)`: **El asistente ahora dice qué puede hacer cada persona antes de empezar**
  Hasta ahora los permisos funcionaban como un filtro invisible: el asistente escondía lo que el funcionario no podía hacer, de modo que el límite se descubría **fallando**. Alguien pedía «envía este documento» y, o aparecía un error incomprensible, o se quedaba esperando una acción que con sus credenciales nunca iba a ocurrir.
  Ahora, al abrir el asistente, la persona ve **qué puede consultar, qué puede ejecutar y qué no** — y, cuando algo no está a su alcance, **por dónde sí puede llegar**: «no puede enviar el documento, pero puede trasladarlo a correspondencia para que lo envíen». Esa segunda parte es la útil; negar sin más deja a la gente igual de atascada.
  Se corrigieron además dos cosas que estaban rotas por dentro. La capacidad de **radicar desde el asistente** llevaba apagada desde el principio, porque estaba amarrada a un permiso que no existe en el sistema; nadie lo notó porque las propias pruebas se inventaban ese permiso. Y el asistente anunciaba límites de búsqueda distintos de los reales del sistema, con una prueba que daba por buena esa diferencia.
  El marco de alcance también **declara lo que el sistema no distingue**, para no prometer precisión que no tiene: quien puede radicar puede radicar cualquier tipo de documento, y lo que cada quien ve no depende de su dependencia sino de su nivel de acceso a la información.
  *Por qué*: enterarse de un límite fallando hace perder el tiempo a dos personas, y una negativa sin alternativa no resuelve nada.
  *Impacto*: el asistente sigue siendo de solo consulta; lo que cambia es que ahora dice con claridad qué está a su alcance. Hay que reconstruir las imágenes al desplegar.

- `fix(tooling)`: **La comprobación automática de código podía fallar sin decir por qué**
  Apareció al ejecutarla mientras otra instancia estaba en marcha: cuatro servicios en rojo —dos de ellos sin ningún cambio— y ningún mensaje que explicara nada. No fallaba el código: fallaba la instalación de las herramientas dentro del contenedor, y esa instalación tenía los mensajes de error **suprimidos**, de modo que un problema de red o de saturación se presentaba como «este servicio no pasa» sin más.
  La misma lección ya estaba escrita dos líneas más abajo en el propio archivo, para otra instalación. Estaba aplicada solo a la mitad.
  *Por qué*: una comprobación que da fallos inexplicables enseña a no hacerle caso, y entonces deja de proteger.
  *Impacto*: solo herramientas de desarrollo.

- `feat(archive,frontend)`: **La decisión de publicar ya alcanza también a los expedientes**
  Faltaba la mitad. Cuando se corrigió que publicar fuera una decisión y no un efecto de no haber clasificado, el cambio se aplicó a los documentos individuales y no a los expedientes: el canal de consulta pública seguía difundiendo el código, el nombre, la descripción, las fechas y el número de documentos de **todo expediente no clasificado**. Lo detectaron por separado las dos revisiones —de seguridad y de conformidad normativa— hechas sobre el trabajo anterior.
  Ahora los expedientes tienen su propio acto de publicación, con el mismo permiso, el mismo fundamento obligatorio y el mismo registro que los documentos, y con su pantalla construida en el mismo cambio: dejar la capacidad sin pantalla ya nos costó una corrección hace dos entregas.
  Dos detalles que no son evidentes y se decidieron a propósito. Un expediente **cerrado o transferido sigue siendo publicable**: al contrario que un documento anulado, no está retirado de la circulación, sino en un estado normal de su ciclo de vida — copiar la regla del documento habría dejado de difundir, sin avisar, la mayor parte del archivo histórico. Y sobre un expediente reservado la pantalla no ofrece publicarlo, pero **sí retirar** una publicación anterior, que es justo cuando más urge poder hacerlo.
  Lo que se difunde de un expediente es siempre agregado: nunca se enumeran los números de los documentos que contiene, porque un expediente público puede incluir documentos reservados.
  *Por qué*: una decisión que vale para un tipo de documento y no para otro no es una decisión, es una excepción.
  *Impacto*: **los expedientes existentes quedan sin publicar**. Si su institución usa el canal de consulta pública de expedientes, debe publicar expresamente lo que quiera difundir. Hay que reconstruir las imágenes al desplegar.

- `fix(document,shared,ci)`: **La decisión de publicar no alcanzaba a los archivos adjuntos, y reclasificar un documento la deshacía sin que nadie lo decidiera**
  Auditoría cruzada de seguridad y de conformidad archivística sobre lo construido. Los tres problemas tienen la misma forma: la corrección anterior era correcta, pero **no llegaba a todas partes**.
  **Los adjuntos se seguían entregando.** El cambio anterior hizo que un documento solo se difundiera si la entidad lo decidía expresamente. Eso quedó bien para la ficha del documento —número, asunto, dependencia—, pero el canal que entrega **el archivo adjunto completo** conservaba la regla antigua escrita aparte, y nadie la actualizó. Con los canales de consulta pública activos, cualquiera en internet podía descargar el adjunto de cualquier documento no clasificado, aunque la entidad no lo hubiera publicado nunca. Y como los números de radicado son consecutivos, se podían recorrer uno a uno. Corregido de forma que la regla ya no se pueda copiar mal: ahora hay una sola, y las dos consultas la construyen desde el mismo sitio.
  **Reservar un documento no retiraba su publicación.** Si un documento publicado se clasificaba después como reservado, dejaba de difundirse —correcto—, pero la marca de «publicado» se quedaba puesta por dentro. Meses más tarde, al levantar la reserva por cualquier otro motivo, el documento **volvía solo a los canales públicos**, sin que nadie hubiera decidido publicarlo de nuevo. Ahora reservar retira la publicación, y volver a publicar exige el acto motivado de siempre.
  **Y no se podía retirar la publicación de un documento ya reservado**, que es justo cuando más urge. También corregido.
  Además se cerraron dos huecos de fontanería: una de las herramientas internas podía dar por aplicada una actualización de base de datos que nunca corrió (el mismo fallo que se corrigió antes, reaparecido un paso más allá), y las pruebas de la librería compartida —la que guarda el registro de auditoría inalterable— **no se estaban ejecutando en el sistema de integración continua**, porque la lista de proyectos a probar enumera los once servicios y la librería no es uno de ellos.
  *Por qué*: una decisión de publicar que no alcanza a los archivos no es una decisión, y un acto que otro deshace sin explicación no es un acto.
  *Impacto*: si su institución ya tenía documentos publicados y luego reservados, conviene revisarlos antes de activar los canales públicos. Hay que reconstruir la imagen al desplegar.

- `feat(frontend)`: **Los expedientes que quedaron pendientes de revisar ya se pueden ver**
  Cuando se implantó el control de instrumentos de retención, el sistema marcó los expedientes **ya cerrados** sobre los que no podía afirmar bajo qué tabla de retención se habían cerrado. La marca era correcta —es más honesto señalarlo que inventar el dato—, pero no había ninguna pantalla que la mostrara: quedaban marcados indefinidamente sin que nadie llegara a saberlo.
  Ahora hay una pestaña en la administración de la TRD que los lista y, sobre todo, **distingue los casos por lo que se puede hacer con ellos**: unos se resuelven solos en cuanto se convalide la serie correspondiente —y se dice dónde hacerlo—, y otros **no tienen hoy ninguna forma automática de resolverse** y necesitan una decisión del área de archivo. La pantalla lo dice tal cual, en vez de ofrecer un botón que no arreglaría nada.
  *Por qué*: un aviso que nadie puede ver no avisa de nada.
  *Impacto*: solo interfaz. Reconstruir la imagen al desplegar.

- `docs(en)`: **La documentación en inglés deja de ir por detrás: ahora dice qué es la versión 0.01**
  El registro de cambios en inglés iba **135 entradas** por detrás del español, todas correspondientes a una misma versión que todavía no se ha publicado. Al medirlo quedó claro que el problema no era de traducción sino de propósito: quien llega al proyecto desde fuera y lee en inglés no necesita ciento treinta y cinco anotaciones internas de «se corrigió tal hallazgo», necesita saber qué hace el sistema y qué no hace.
  La versión inglesa empieza ahora por un resumen de la línea base organizado por áreas —radicación y expedientes, retención, índice electrónico y firma, preservación, seguridad, flujos, interoperabilidad, inteligencia artificial, interfaz y operación— y declara los límites donde corresponde: la firma **no está acreditada**, el repositorio de preservación **tampoco**, y el conector con el operador postal es todavía un simulacro. Cada fichero dice en su cabecera qué es, para que nadie vuelva a medir un desfase contra una equivalencia que ya no se persigue.
  *Por qué*: mantener más de trescientas anotaciones internas duplicadas en dos idiomas era la causa del desfase, no su consecuencia.
  *Impacto*: solo documentación.

- `fix(document,frontend)`: **Se podía dejar de publicar un documento, pero no publicarlo**
  En el cambio anterior se corrigió algo importante: hasta entonces, un radicado se difundía por los canales de consulta pública **solo por no estar clasificado como reservado** — es decir, no haber decidido nada equivalía a haber decidido publicar. La ley pide lo contrario: publicar es un acto de la entidad. Se corrigió, y todos los radicados pasaron a nacer sin publicar.
  Lo que faltó fue la otra mitad: **no se construyó la pantalla para publicarlos**. La capacidad existía en el servidor, pero ninguna pantalla la usaba, así que una institución que actualizara se quedaba con todo su archivo sin publicar y sin ninguna forma de publicar nada desde la aplicación. Se cerró una puerta indebida y se dejó el edificio sin la puerta buena.
  Al construir la pantalla apareció un segundo hueco: el sistema permitía **marcar** un documento como publicado, pero no **consultar** si lo estaba. Ni la pantalla podía mostrarlo ni la entidad podía responder «¿qué hemos publicado?», que es justo lo que hay que poder acreditar.
  Ahora hay un panel en la bandeja que muestra el estado real y ofrece siempre la acción contraria a la vigente —publicar si no lo está, retirar si lo está—, exige explicar el motivo en ambos casos, y avisa de lo que pasa al publicar: el documento queda consultable y descargable desde internet por cualquiera. Sobre un documento **reservado o anulado no se ofrece el formulario**: se explica el motivo, en vez de dejar que alguien redacte una justificación para recibir después un rechazo incomprensible.
  *Por qué*: una decisión que la ley obliga a tomar, y a poder acreditar después, no puede depender de que alguien entre a la base de datos.
  *Impacto*: los documentos siguen naciendo sin publicar; ahora hay cómo publicarlos y cómo saber qué se publicó. La institución debe reconstruir las imágenes al desplegar.

- `fix(shared,gateway,document,archive,auth,storage,infra)`: **Publicar información dejaba de ser una decisión, y una incidencia no se podía seguir de principio a fin**
  **Lo que se publica de oficio ahora se decide, no se deduce.** El sistema ofrece dos canales por los que otras entidades y el público pueden cosechar los documentos de la institución. Hasta ahora, entraba en esos canales todo radicado que no estuviera marcado como reservado — es decir, **no haber decidido nada equivalía a haber decidido publicar**. Un documento recién radicado, o uno desclasificado con otro propósito, salía solo. La Ley 1712 de 2014 pide justo lo contrario en su artículo 12: un esquema de publicación, un acto por el que la entidad decide qué difunde. Ahora cada radicado tiene una marca de publicación que empieza **apagada** y solo se enciende con un acto explícito, motivado y registrado, que además queda anotado con quién lo hizo y cuándo. La reserva sigue mandando: marcar como publicable un documento reservado no es posible.
  **Una incidencia no se podía seguir entre servicios.** El sistema son once servicios que se llaman entre sí. Cuando algo fallaba, no había forma de saber qué le había pasado a *una* petición concreta: cada servicio anotaba lo suyo sin nada que los cosiera. Existía un identificador de petición… que se generaba **al terminar**, cuando ya no servía para etiquetar nada. Ahora se asigna al entrar, viaja por todos los servicios y vuelve al navegador, y cada servicio deja una línea con la entidad, el usuario, la operación y cuánto tardó. Quien atiende una incidencia puede reconstruir el recorrido completo con un solo dato — y el texto de las búsquedas y los números de radicado no quedan en esos registros.
  **Contraseñas de ejemplo que funcionaban de verdad.** El código de este proyecto es público. Dos claves —la que protege las firmas electrónicas y la del almacén de archivos— traían un valor de ejemplo **que funcionaba**: una institución que se olvidara de cambiarlas no recibía ningún aviso, y quedaba operando con una clave que cualquiera puede leer en internet. Ahora el sistema **se niega a arrancar** sin ellas. La configuración de ejemplo del sistema de identidad, que incluía dos usuarios con contraseña publicada —uno de ellos administrador—, pasa a llamarse claramente «de desarrollo», y producción parte de una plantilla que no se puede usar sin rellenar.
  **Cualquier página de internet podía llamar al sistema con la sesión del visitante.** La puerta de entrada tenía una configuración de permisos entre sitios web abierta de par en par y escrita en el código, sin forma de cambiarla. Ahora está cerrada por defecto —el sistema no la necesita para funcionar— y quien la abra tiene que enumerar los sitios autorizados.
  **La guía de instalación mandaba copiar una plantilla que no existía.** La del servidor web que va delante del sistema. Sin ella, tres cosas fallaban en silencio: el inicio de sesión no completaba, la dirección de red que se sella en el registro de auditoría era la del propio servidor en lugar de la del ciudadano, y las descargas grandes se volvían a acumular en memoria. Ya está incluida y comprobada.
  **La contabilidad de actualizaciones de la base de datos estaba llevada por duplicado.** Dos herramientas anotaban lo aplicado en sitios distintos y sin verse, con el agravante de que identificaban cada actualización solo por el nombre del archivo — y hay cuatro nombres repetidos entre servicios. La consecuencia: la actualización del segundo servicio se daba por hecha **sin haberse aplicado**. Ahora hay un libro único que identifica cada actualización por servicio, y lo ya aplicado se importa sin repetir nada.
  **El registro de auditoría no estaba dividido por meses.** La estructura estaba prevista desde el principio, pero la tarea que crea las divisiones nunca se escribió: todo se acumulaba en un mismo bloque. Además de la lentitud, esto se agrava con el tiempo, porque dividir es cada vez más difícil cuanto más histórico hay. Ya existe la tarea, que crea las divisiones de los próximos meses **y las protege contra borrado**, igual que el resto del registro.
  **Tres controles de acceso que faltaban.** (1) La ingesta automática de correo la podía disparar cualquier usuario con sesión, y como el buzón es uno solo para el servidor, un usuario de una institución podía llevarse a la suya correos dirigidos a otra —y quedaban marcados como leídos, así que no volvían a aparecer—. Ahora el buzón declara a qué institución pertenece y hace falta permiso de administración. (2) Marcar un documento **para eliminación** exigía el mismo permiso que consultarlo. Ahora exige permiso de escritura, obliga a motivar la decisión y queda registrada. (3) El inventario documental (FUID) salía sin cabecera: sin decir de qué entidad ni de qué oficina era, ni para qué se levantó. Ya la lleva, con el nombre de la entidad resuelto por el propio sistema, y con la columna de frecuencia de consulta calculada a partir de los accesos reales en lugar de estimada de memoria.
  *Por qué*: publicar información pública es una decisión de la entidad, no un efecto secundario de no haber clasificado; y una clave de ejemplo que funciona es una clave conocida.
  *Impacto*: **los radicados existentes quedan como no publicables** hasta que alguien ejecute el acto de publicación; si la institución usa los canales de cosecha, debe publicar expresamente lo que quiera difundir. Además hay que definir tres variables obligatorias antes de desplegar (la clave de firmas, las dos del almacén de archivos) y, si se usa la ingesta de correo, indicar a qué institución pertenece el buzón.

- `fix(shared,gateway,*)`: **El reparto automático de la correspondencia podía detenerse en silencio, y el sistema seguía diciendo que todo iba bien**
  Cuatro procesos internos se encargan de reaccionar a lo que ocurre en el sistema: repartir automáticamente la correspondencia que entra, enviar los correos, sellar las firmas y alimentar el buscador. Si el servicio de mensajería interna no estaba disponible en el momento exacto del arranque —o se reiniciaba después—, esos procesos **morían y no volvían**. El sistema seguía funcionando en apariencia: se podía radicar, consultar y firmar. Pero los documentos nuevos dejaban de repartirse a las dependencias, los correos no salían y el buscador se quedaba con la información de antes. Nada lo avisaba. Ahora vuelven a levantarse solos, con esperas crecientes para no saturar, y cada caída queda registrada.
  **La comprobación de salud daba un «todo bien» automático.** Tres servicios respondían que estaban sanos **sin comprobar nada**: lo decían igual con la base de datos caída. Y el caso contrario también estaba mal: la puerta de entrada del sistema se declaraba enferma cuando fallaba **cualquier** servicio detrás, con lo que el orquestador reiniciaba precisamente lo único capaz de seguir atendiendo. Ahora hay dos comprobaciones distintas: una responde «el proceso está vivo» —la que decide reinicios— y otra, nueva, informa de verdad del estado de la base de datos, la mensajería interna, el correo y los procesos internos, para que el balanceador deje de enviar tráfico a un servidor que no puede atenderlo, sin reiniciarlo.
  Además, esas comprobaciones **publicaban nombres y puertos internos** en el texto del error, y cualquiera podía consultarlas sin autenticarse. Ahora informan del estado con una palabra; el detalle queda solo en el registro del servidor.
  **Los fallos de inicio de sesión sin causa aparente.** El sistema pedía a la base de datos más conexiones simultáneas de las que ésta concede, y comparte servidor con el sistema de autenticación. El resultado no se veía donde estaba el problema: bajo carga documental, lo que fallaba era **entrar al sistema**. La cuenta está ahora hecha, escrita y ajustada, y una consulta atascada ya no retiene su conexión indefinidamente.
  *Por qué*: un proceso que muere en silencio es correspondencia oficial que deja de repartirse sin que nadie se entere, y una comprobación que siempre dice «bien» desactiva la vigilancia que aparenta dar.
  *Impacto*: la institución debe reconstruir las imágenes y desplegar el manifiesto actualizado de producción, que ajusta la configuración de la base de datos. Quien vigile el sistema con la dirección `/health` y espere ver ahí el estado de las dependencias debe pasar a `/health/ready`.

- `fix(frontend)`: **Pasar el ratón por el icono de salir cerraba la sesión**
  El icono de cerrar sesión era un enlace, y la interfaz está configurada para ir adelantando el trabajo de una página **cuando el puntero pasa por encima**. Como el cierre de sesión ocurría en esa carga previa, rozar el icono —sin llegar a hacer clic— cerraba la sesión de verdad. Peor: al estar el efecto en una simple visita a la dirección, también lo disparaba un enlace o una imagen puestos en un correo. Ahora hace falta pulsar el botón, y quien escriba la dirección a mano encuentra una pantalla de confirmación.
  **Los mensajes de error mostraban la red interna de la institución.** Cuando algo fallaba, el texto que aparecía en pantalla incluía nombres de servidores, direcciones y puertos internos. Ahora se muestra un mensaje claro y un **código de referencia** que el usuario puede dictar a soporte; el detalle técnico queda solo en el registro del servidor. Y se añade una pantalla de error propia: antes, un enlace roto llevaba a una página en blanco con texto técnico, sin marca ni forma de volver.
  **La interfaz podía quedarse esperando para siempre.** Si el sistema central tardaba en responder, las pantallas no daban error ni se recuperaban: se quedaban cargando indefinidamente, incluida la de inicio de sesión. Ahora hay un plazo máximo de espera. Las descargas grandes —exportación de un expediente, paquete de preservación, FUID— quedan expresamente excluidas del plazo: lo que se corta es un servidor que no contesta, no uno que está enviando un archivo grande.
  **El botón Atrás mostraba la bandeja del usuario anterior.** El navegador guardaba las páginas y las volvía a mostrar sin pedirlas de nuevo. En un puesto de ventanilla compartido, el siguiente funcionario podía ver así radicados y asuntos del anterior —incluidos documentos clasificados que él no tiene autorización para consultar—, y cerrar sesión no lo evitaba. Ahora ninguna pantalla con información de trabajo se guarda en el navegador.
  Se añade además una **política de seguridad de contenido**: una segunda barrera del navegador que impide ejecutar código introducido en un documento, en una plantilla o en una respuesta del asistente, incluso si la limpieza de texto que ya existía fallara.
  Por último, cuando el sistema no logra cargar los permisos del usuario, la aplicación **lo dice**. Antes el menú simplemente aparecía vacío, indistinguible de que le hubieran retirado los permisos: el funcionario llamaba al administrador, que veía sus permisos intactos y no encontraba nada que corregir.
  *Por qué*: una acción con consecuencias no puede ocurrir por pasar el ratón, y una pantalla que oculta que está funcionando a medias hace perder el tiempo a dos personas.
  *Impacto*: el botón de cerrar sesión se comporta igual al pulsarlo. La institución debe reconstruir la imagen de la interfaz al desplegar.

- `chore(deps,docker)`: **Dependencias al día y los servicios dejan de ejecutarse con privilegios de administrador**
  Se actualizaron las bibliotecas de los once servicios, incluidas las de criptografía, las del servidor web y **la que firma el índice electrónico**. Por eso el cambio no se dio por bueno leyendo la lista: se ejecutaron las más de 2.500 pruebas y las de base de datos real antes de aceptarlo.
  La interfaz pasa a Node 22 —la versión anterior dejó de recibir soporte en abril de 2025 y era la que se desplegaba— y a la última versión de su marco de trabajo, que incluye el arreglo del límite de tamaño de archivos corregido en el bloque anterior.
  Diez de los once servicios se ejecutaban **como administrador dentro de su contenedor**. Todos procesan información que no controlan —documentos, correos, formularios—, así que un fallo en cualquier biblioteca daba el control completo del contenedor en vez de solo el del proceso. Ahora corren con un usuario sin privilegios. Y la imagen del almacén de documentos deja de seguir la etiqueta «última versión»: ahí vive la protección de retención irreversible, y que cambie sola en una actualización rutinaria no es aceptable.
  *Por qué*: ejecutar como administrador anula el aislamiento del contenedor precisamente donde se procesa información no confiable, y una versión sin soporte no recibe correcciones de seguridad.
  *Impacto*: ningún cambio visible en el uso. Las imágenes deben reconstruirse al desplegar.

- `fix(gateway,auth,frontend)`: **La dirección de red que quedaba registrada la elegía el propio visitante**
  El sistema confiaba en una cabecera que cualquiera puede escribir para saber desde qué dirección llegaba cada petición. Dos consecuencias: el límite de peticiones se esquivaba cambiándola en cada intento, y —más grave— **la dirección que quedaba grabada en el registro de auditoría**, que es inmutable y tiene valor probatorio, era la que el visitante decidía poner. Un dato de la cadena de custodia que se puede falsificar es peor que uno vacío, porque el vacío no engaña a nadie.
  Ahora esa dirección se calcula en el servidor siguiendo la cadena de intermediarios **declarados como de confianza**, y se transmite internamente por una vía que el visitante no puede alterar. Si la institución no ha declarado sus intermediarios, la cabecera se ignora por completo — se prefiere un dato inútil a uno falso.
  Se corrige además un problema que afectaba al uso diario: **todas las peticiones de todos los usuarios se contaban como si vinieran de la misma dirección**, porque la interfaz no transmitía la del navegador. Con varias consultas por pantalla, eso producía bloqueos por «demasiadas peticiones» en una mañana normal de ventanilla. Y el alta ciudadana de PQRS estrena un límite propio y más estricto: crea radicados sin autenticación y **consume números que no se reutilizan**.
  Por último, el registro de auditoría deja de anotar una entrada **por cada petición** para decir que el token era válido. Eso sepultaba los hechos que de verdad importan —accesos, firmas, reclasificaciones— bajo un ruido enorme, y volvía el registro ilegible justo para quien viene a investigar. Los intentos con token inválido sí se siguen registrando.
  *Por qué*: un registro con valor probatorio en el que casi todas las filas son ruido no sirve para probar nada, y una dirección que elige el visitante tampoco.
  *Impacto*: la institución debe declarar sus intermediarios de red al desplegar (`TRUSTED_PROXIES`); sin ello el límite por dirección no distingue usuarios.

- `fix(ci)`: **Las pruebas automáticas nunca se habían ejecutado en el sistema de integración continua**
  El pipeline que debía verificar cada cambio moría en su **primera línea**, por una diferencia de configuración entre la forma en que lo invocaba y la que usa el proyecto. Como la herramienta se detiene en el primer fallo, **la ejecución de las pruebas nunca llegaba a ocurrir** — y el pipeline llevaba tiempo dando la impresión contraria.
  Ahora el sistema de integración continua usa **exactamente la misma comprobación** que ejecuta un desarrollador en su máquina, en vez de una copia que podía divergir; ejecuta las pruebas de los once servicios en paralelo; y estrena dos carriles que no existían: pruebas **contra una base de datos real** —256 pruebas que estaban escritas pero jamás se habían ejecutado— y pruebas del **frontend**, que no tenía ninguna.
  Se añade también la publicación de imágenes etiquetadas por versión, que es lo que hace posible volver atrás tras una actualización fallida.
  Al ejecutarlo de verdad aparecieron tres problemas que la lectura no revelaba, incluidos dos en el propio arreglo. Se corrigieron antes de darlo por bueno.
  *Por qué*: un pipeline que no ejecuta las pruebas no es una verificación, es una decoración — y da una confianza que no corresponde.
  *Impacto*: solo integración continua. Ningún cambio en el comportamiento del sistema.

- `feat(infra)`: **El respaldo y la restauración existen de verdad, y se han probado de punta a punta**
  El procedimiento de respaldo vivía únicamente como un ejemplo dentro de la guía, y ese ejemplo **no funcionaba**: se conectaba con un usuario de pruebas, copiaba los archivos a una carpeta que no existe, y —lo más grave— si el volcado fallaba generaba igualmente un fichero vacío **y borraba los respaldos anteriores**. Ejecutado de madrugada por una tarea programada, eso significa acumular meses de copias vacías con apariencia de normalidad. El procedimiento de recuperación, por su parte, apagaba la base de datos y acto seguido intentaba restaurar sobre ella.
  Ahora hay dos programas reales. El de **respaldo** copia la base de datos y los documentos, **comprueba que la copia se puede leer** antes de darla por buena, y solo entonces borra las antiguas; si algo se interrumpe, la copia queda marcada como incompleta y el programa de restauración se niega a usarla.
  El de **restauración** pide confirmación escrita, y al terminar **verifica la cadena de integridad del registro de auditoría**: que la base arranque no demuestra que la traza documental esté intacta, y es precisamente eso lo que una entidad tiene que poder acreditar. Si algo se manipuló, lo dice y señala el asiento.
  El ciclo completo —copiar, destruir, restaurar, verificar— se ha ejecutado sobre un entorno real, y la verificación se puso a prueba alterando deliberadamente un asiento: la detecta.
  Se añade además el procedimiento de **actualización y vuelta atrás**, que no existía, incluyendo la advertencia de que revertir el programa no revierte los cambios de estructura de la base: para eso hace falta el respaldo.
  *Por qué*: un respaldo cuya restauración nunca se ha probado no es un respaldo, es un archivo. Y ninguna institución debería poner documentos oficiales en un sistema sin esa garantía.
  *Impacto*: solo despliegue y operación.

- `fix(gateway,storage,frontend)`: **Ya se pueden adjuntar documentos escaneados: antes fallaba todo lo que pasara de 512 KB**
  El servidor de la interfaz traía un límite de tamaño de **512 KB** por petición que nunca se había configurado, y como los adjuntos pasan por él, **cualquier PDF escaneado real era rechazado** con un escueto «Error al subir el archivo». La importación masiva de expedientes era directamente inservible.
  Subir el límite a secas habría sido peor: el sistema copiaba cada archivo **entero en memoria** dos veces —una en la puerta de entrada y otra en el servicio de almacenamiento— y no existía ningún tope de tamaño, así que un archivo grande no habría dado un error sino que habría tumbado el servicio. Por eso el cambio hace las tres cosas a la vez: los archivos ahora se transmiten **por trozos** sin acumularse en memoria, hay un tope explícito que devuelve un mensaje claro cuando se supera, y el límite de la interfaz sube a 512 MB.
  *Por qué*: un sistema de gestión documental en el que no se puede adjuntar un documento escaneado no cumple su función.
  *Impacto*: los adjuntos y la importación masiva funcionan. Un archivo por encima del tope recibe ahora un error explícito con el tamaño máximo admitido, en vez de un fallo genérico.

- `fix(document)`: **Modificar un radicado ya deja constancia, y una anulación no se deshace editando un campo**
  Cambiar el asunto, el destinatario, el estado o el número de folios de un radicado no quedaba registrado en ninguna parte — llamativo, porque **consultarlo** sí quedaba. Un documento oficial con número inmutable que se puede editar sin dejar constancia no cumple lo que la traza documental exige.
  Además, un radicado **anulado** —decisión que pasa por solicitud y aprobación de un supervisor— podía volver al estado activo con una simple edición, sin causal, sin aprobador y sin registro. Ahora el sistema lo rechaza y explica por qué: revertir una anulación, si alguna vez procede, es un acto propio con su justificación, no el efecto lateral de editar un campo.
  Cada modificación registra ahora **qué campo cambió, de qué valor y a cuál**, junto con quién la hizo. Y si por cualquier motivo esa constancia no pudiera guardarse, la modificación **no se aplica**: se prefiere que un cambio falle a que ocurra sin dejar rastro.
  *Por qué*: la trazabilidad de un documento de archivo no admite huecos, y una anulación que se deshace por una vía lateral no es una anulación.
  *Impacto*: intentar modificar un radicado anulado devuelve ahora un error explícito.

- `fix(document,archive)`: **El registro de correspondencia dejaba de ser público por defecto**
  El sistema ofrece dos canales de cosecha (OAI-PMH y CMIS) pensados para que otras instituciones y portales de datos abiertos consulten lo que la entidad ha decidido publicar. Estaban **activos desde el primer arranque**, y lo que dejaban ver dependía de que cada documento estuviera clasificado como reservado: como el valor por defecto de un radicado es «público», la práctica era «se publica todo salvo que alguien se acuerde». Por ahí salían el número, el **asunto** y la dependencia de cada radicado, y podía descargarse el contenido de los anexos, **sin quedar registro de quién lo hizo**.
  Ahora estos canales vienen **apagados** y la entidad los enciende cuando lo decide, que es lo que corresponde: publicar el registro de correspondencia forma parte del esquema de publicación de la entidad (Ley 1712, art. 12), no de la instalación del software. Mientras están apagados, responden como si no existieran. Y cuando se encienden, **cada consulta y cada descarga quedan registradas** en la traza de auditoría, con qué se pidió y cuánta información se entregó.
  *Por qué*: qué información publica una entidad es una decisión suya, y necesita poder responder después a quién consultó su archivo.
  *Impacto*: **si su institución ya usaba estos canales, hay que activarlos explícitamente** tras actualizar.

- `fix(gateway,tenant,storage)`: **Una dirección web torcida podía llevar a un sitio distinto del que el sistema creía autorizar**
  El portal de entrada decidía a qué servicio mandar cada petición mirando la dirección tal como llegaba, pero la libreria que la reenviaba la **simplificaba después** —igual que `..` en una ruta de carpetas sube un nivel—. Resultado: una dirección construida a mano podía autorizarse como una cosa y ejecutarse como otra. Por esa vía se alcanzaba el registro de instituciones, que estaba deliberadamente fuera del alcance público, y se podía sustituir o dar de baja el archivo adjunto de un radicado **sin que la traza registrara quién**.
  Ahora el portal **rechaza** esas direcciones en la puerta, antes de comprobar la identidad y antes de decidir el destino. Y no se ha confiado solo en eso: el registro de instituciones y las operaciones que modifican archivos adjuntos exigen además su propia credencial de servicio, de modo que si una de las dos protecciones fallara, la otra sigue en pie.
  *Por qué*: si la puerta de entrada autoriza una dirección y ejecuta otra, ninguna de sus decisiones vale. Y no aparecer en una lista de rutas no es una protección: es una ausencia.
  *Impacto*: ningún cambio visible en el uso normal del sistema.

- `fix(infra)`: **El manifiesto de despliegue en producción nunca se había ejecutado**
  El fichero que describe cómo levantar OrpycaMCP en una institución real estaba escrito pero nunca se había probado, y fallaba en cadena: siete de los once servicios no llegaban a construirse, la preparación inicial de la base de datos apuntaba a una ruta que no existe (y Docker, en vez de avisar, monta una carpeta vacía en silencio), el gestor de identidades moría al arrancar por una configuración de certificados incompleta, y ninguno de los secretos que los servicios usan para hablar entre sí estaba definido — lo que no daba un error visible, sino un sistema aparentemente en marcha en el que no se descarga un solo anexo y nadie con segundo factor puede entrar. Además, cualquier reinicio del servidor apagaba la institución de forma permanente.
  Todo eso está corregido y **comprobado sobre contenedores reales**, no solo leído: los doce servicios se construyen, la base de datos ejecuta su preparación inicial y el gestor de identidades arranca. El fichero de ejemplo de variables documenta ahora cada secreto, qué dos servicios lo comparten, y **cuáles hay que respaldar con la misma disciplina que la base de datos** — perder la clave del segundo factor obliga a que toda la plantilla lo vuelva a configurar.
  *Por qué*: sin un manifiesto que arranque no hay despliegue posible, y ninguna otra comprobación de seguridad puede verificarse de verdad.
  *Impacto*: solo despliegue. Queda pendiente el ensayo completo con los dieciséis servicios a la vez, que necesita un realm de identidades de producción.

- `fix(frontend)`: **La pantalla de perfil ya no ofrece emitir la credencial de firma a quien no puede, y muestra los códigos de recuperación**
  Dos correcciones en la misma pantalla. La primera: emitir la credencial de firma requiere un permiso que no todos los usuarios tienen, pero la pantalla ofrecía el botón a todos y el rechazo llegaba al pulsarlo. Ahora se dice antes, con el motivo y a quién acudir. La segunda: los códigos de recuperación del segundo factor se muestran al activarlo —advirtiendo que no volverán a mostrarse—, se avisa cuando quedan dos o menos, y se pueden generar de nuevo. Al pedir el código de confirmación, la pantalla reconoce sola si es de la aplicación autenticadora o de recuperación, sin que haya que indicarlo.
  *Por qué*: una interfaz que ofrece lo que el sistema va a rechazar convierte un permiso faltante en un error incomprensible; y unos códigos que existen pero no se muestran no existen para quien los necesita.
  *Impacto*: solo interfaz.

- `feat(auth)`: **Perder el teléfono ya no deja la cuenta bloqueada: el segundo factor tiene códigos de recuperación**
  El segundo factor (TOTP) vive en el teléfono, y hasta ahora **todas** las formas de dejar de depender de él —desactivarlo, cambiarlo a un teléfono nuevo, o retirar la credencial personal de firma— pedían un código de ese mismo teléfono. Quien lo perdía se quedaba fuera, y la única salida era que alguien tocase la base de datos a mano.
  Al activar el segundo factor se entregan ahora **10 códigos de un solo uso**. Cualquiera de ellos sirve para desactivar el segundo factor o para pasarlo a un teléfono nuevo. También se puede pedir un juego nuevo cuando quedan pocos, lo que anula el anterior.
  Los códigos se muestran **una sola vez**: en el sistema solo queda una huella irreversible de cada uno, así que no hay forma de volver a consultarlos —solo de generar otros—. La pantalla puede avisar de cuántos quedan sin usar, pero nunca mostrarlos. Se entregan en el mismo momento en que el segundo factor queda activo, para que no exista un intervalo en el que esté protegiendo la cuenta sin haber dado la vía de recuperación.
  **Un código de recuperación no sirve para firmar.** Firmar sigue exigiendo el teléfono: un código impreso o guardado en un gestor de contraseñas no demuestra que quien firma tenga el dispositivo, y esa demostración es justamente lo que da valor a la firma. Recuperar el acceso y firmar son actos distintos y se piden pruebas distintas.
  *Por qué*: era una brecha de producto reconocida — sin ella, perder el teléfono impedía incluso retirar la propia credencial de firma.
  *Impacto*: nada de lo existente cambia; los campos nuevos son opcionales. Requiere aplicar la migración `015` en cada entidad.

- `feat(archive,frontend)`: **Ya se puede cargar un fondo acumulado en masa, y la pantalla de expedientes lo distingue del archivo corriente**
  El incremento anterior permitía **cerrar** expedientes de fondo acumulado —documentación antigua, típica de entidades liquidadas— pero no había forma práctica de **cargarlos**: la carga masiva no declaraba el origen del expediente, así que todos entraban como archivo corriente y el sistema los rechazaba uno a uno al ver que su instrumento era una tabla de valoración. Migrar un fondo acumulado son miles de expedientes; la única alternativa era crearlos de uno en uno.
  Ahora la carga masiva acepta el origen y las fechas extremas por expediente, y la pantalla de creación tiene un selector de origen que **cambia el catálogo que ofrece**: un expediente corriente se clasifica bajo la TRD y uno de fondo acumulado bajo la TVD, nunca al revés. Si no hay tablas de valoración registradas, la pantalla lo dice y enlaza a su administración en lugar de ofrecer un catálogo que el sistema va a rechazar. El aviso de la fecha extrema final advierte que el calendario de retención puede resultar **ya vencido** — que en un fondo acumulado es lo normal, no un error.
  *Por qué*: sin esto, la gestión de fondo acumulado existía pero era inalcanzable para su caso de uso real.
  *Impacto*: los campos nuevos son opcionales, así que nada de lo que ya funcionaba cambia. Queda deliberadamente fuera el cálculo de la retención en almacenamiento inmutable para fondo acumulado: hoy el sistema se abstiene de fijarla en vez de fijarla mal, porque esa fecha no se puede corregir después.

- `docs(en,es)`: **La documentación en inglés ya cubre lo mismo que la española**
  No era una diferencia de matices: la referencia de API en inglés omitía **95 de los 249 endpoints** y, de forma sistemática, todas las notas de control de acceso —qué permiso exige cada ruta, qué se recorta por nivel de seguridad, por qué un `404` a veces significa "no puedes verlo"—. Es justo lo que alguien de fuera necesita para juzgar si el sistema sirve, así que una página que parecía terminada estaba engañando por omisión. Se completaron los bloques ausentes (segundo factor y credencial de firma, administración de usuarios y grupos, PINAR, preservación digital, firma electrónica, envíos postales, interoperabilidad OAI-PMH/CMIS, importación y exportación, asistente y búsqueda semántica) y las otras cuatro páginas pendientes: primeros pasos, dominio documental, despliegue y normativa. En despliegue faltaban dos apartados cuya ausencia deja una instalación que rechaza todas las sesiones o que nunca completa el inicio de sesión.
  La cobertura se comprobó **contando**, no leyendo: los 249 endpoints de ambos idiomas casan exactamente, y las 40 pantallas del sistema figuran en las tablas de los dos idiomas.
  *Por qué*: la documentación en inglés es la única puerta por la que una institución o un contribuidor no hispanohablante evalúa el proyecto.
  *Impacto*: solo documentación. De paso se corrigieron dos afirmaciones que habían quedado desactualizadas y decían que hay cosas sin construir que sí están construidas (el asistente conversacional y la firma XAdES del índice electrónico); lo que sigue pendiente en firma es la acreditación ante la ONAC, que es otra cosa.

- `feat(frontend)`: **La aplicación ya lleva el logotipo institucional de Orpyca**
  Hasta ahora no lo llevaba. Lo que hacía de logotipo era una imagen de relleno —un orbe genérico recortado de una pieza publicitaria— y el panel lateral, que es la superficie de marca que se tiene delante toda la jornada, ni siquiera usaba esa: mostraba un icono de archivador de una librería de iconos. Entran los originales: el **isotipo** (el anillo verde con la serpiente) y el **logotipo completo** con el nombre y la línea "orfeo pyme calidad", además de los iconos de pestaña del navegador y de pantalla de inicio en iOS.
  El reparto entre uno y otro no es estético, lo imponen dos límites del propio activo. El nombre "Orpyca" está dibujado en negro degradado, así que **sobre fondo oscuro desaparece**: en el panel de acceso el nombre va escrito con la tipografía del sistema de diseño, junto al isotipo, sobre un verde de marca profundo donde el anillo recupera la silueta con la que fue diseñado. Y la línea "orfeo pyme calidad" es diminuta: a la altura de una barra de navegación no llegaría a 6 píxeles, así que **el logotipo completo solo aparece donde hay sitio** —la tarjeta de acceso— y en las franjas estrechas se usa el isotipo con el nombre en texto. Escribir el nombre en vez de incrustarlo en la imagen tiene además una ventaja práctica: se ve nítido en cualquier pantalla y sigue leyéndose cuando se amplía el navegador.
  La marca aparece ahora en el panel lateral, en la barra superior **en móvil** (donde el panel lateral está plegado y la aplicación no mostraba ninguna señal de marca), en la pantalla y el panel de acceso, en la barra y el pie de la página pública, en la pantalla de acceso denegado y en la de verificación de sesión. Al compartir un enlace del sistema en mensajería o redes, la vista previa muestra el logotipo.
  *Por qué*: un sistema que una institución instala bajo su nombre no puede presentarse con un icono de librería y una imagen de relleno.
  *Impacto*: solo interfaz; ningún contrato de API cambia. Se verificó que la línea de firma sobre el fondo oscuro cumple el contraste mínimo de accesibilidad (pasó de 4,31:1 a 10,6:1).

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

- `fix(tooling,*)`: **La comprobación automática de código pasa a estar en verde en los once servicios — y resultó que no estaba mirando tres de ellos**
  Continuación de la corrección de la puerta de enlace (más abajo), donde quedó anotado que la comprobación seguía en rojo. Al ponerse a arreglarla apareció algo más incómodo que los errores: **la propia herramienta no revisaba tres de los once servicios**. No fallaba en ellos; simplemente no los abría, y entre los tres escondían más de cien avisos. La lista de servicios ya no se escribe a mano: se deduce del proyecto, de modo que un servicio nuevo entra en la revisión sin que nadie tenga que acordarse.
  Y algo más serio: **la biblioteca de auditoría —la que deja constancia inmutable de cada acto, la que sostiene la trazabilidad legal— no se estaba comprobando en ningún servicio**. No estaba instalada en el entorno de revisión, así que la herramienta la daba por buena sin mirarla. Al instalarla y declararla correctamente, veintinueve avisos desaparecieron **y no apareció ni un problema nuevo**: las llamadas ya eran correctas, solo que nadie lo había verificado nunca. Se dejó además preparado para que, si esa instalación fallara algún día, la revisión se detenga en vez de continuar en falso.
  Entre los problemas de verdad que la herramienta destapó: la firma electrónica daba por supuesto un tipo concreto de clave criptográfica y habría fallado con un mensaje incomprensible ante cualquier otro; la comprobación de que un certificado corresponde a su solicitud no funcionaba con dos tipos de clave modernos; un evento sin número de radicado podía llegar a modificar datos sin saber a qué documento se refería; y crear una versión de tabla de valoración incompleta daba un error interno en vez de decir qué faltaba. Ninguno estaba dando problemas hoy, pero todos eran fallos esperando ocurrir.
  También apareció **una prueba que no probaba nada**: extraía un dato, explicaba en tres líneas de comentario qué había que comprobar sobre él, y no lo comprobaba. Escrita la comprobación que faltaba.
  *Por qué*: una comprobación que siempre está en rojo deja de leerse, y esta además daba por buenos tres servicios y la biblioteca de la que depende la trazabilidad legal.
  *Impacto*: los once servicios en verde y **2 516 pruebas** sin una sola rotura.

- `fix(api-gateway)`: **La puerta de enlace central pasaba a comprobarse sin verificación de tipos, y con ella todo el tráfico del sistema**
  Toda petición que entra al sistema atraviesa un mismo punto. Ese punto armaba la llamada al servicio de destino de una forma que el verificador automático de tipos no podía inspeccionar, así que emitía once avisos y **dejaba sin comprobar el único tramo por el que pasa el cien por cien del tráfico**.
  Reescrita la llamada de forma explícita. En el camino quedó fijado por escrito —y con pruebas— un detalle que antes era implícito: cuando no se indica un tiempo máximo de espera, hay que decir «usa el del cliente», que **no** es lo mismo que «sin límite». Confundirlos dejaría cada petición esperando indefinidamente, y el aviso de «el servicio no respondió a tiempo» nunca llegaría a mostrarse.
  *Por qué*: el archivo afectado es por donde pasa cada petición del sistema, y era el único tramo del recorrido que no se comprobaba de verdad.
  *Impacto*: sin cambios de comportamiento. Dos pruebas nuevas que fijan el tiempo de espera en los dos casos —el general y el del asistente conversacional, que necesita uno mucho mayor—, comprobadas al revés: sin el arreglo, fallan. Conviene no leer de más este arreglo: al quedar limpio este punto se hizo visible que los otros siete servicios acumulan sus propios avisos, anteriores e independientes, así que la comprobación de tipos del proyecto **todavía no** puede usarse como control de calidad.

- `fix(tooling)`: **Ningún contenedor vuelve a dejar archivos que su propietario no pueda editar**
  Varias herramientas del proyecto (el revisor de estilo, la regeneración de dependencias, las pruebas documentadas para quien contribuye) se ejecutaban dentro de contenedores que tenían acceso de escritura al código. Un contenedor trabaja con la identidad del administrador del sistema, así que **todo lo que escribía quedaba en el disco a nombre de esa identidad**, y quien había clonado el proyecto ya no podía modificarlo. Se habían acumulado unas nueve mil rutas en esa situación, entre ellas catorce archivos de código de la interfaz que llevaban meses con un aviso del revisor sin poder corregirse, precisamente porque el corrector automático no podía escribirlos.
  El fallo era invisible: los comandos funcionaban, el control de versiones registraba los archivos con toda normalidad, y el problema solo aparecía la siguiente vez que alguien intentaba editarlos. Se cerraron los tres orígenes: ahora las herramientas reciben el código **en modo solo lectura** y escriben sus archivos temporales dentro del contenedor; cuando un comando debe producir un archivo de verdad, lo entrega por la salida estándar y es el equipo de quien lo ejecuta el que lo escribe, con su propia identidad. La regla y su comprobación quedan escritas en la guía interna del proyecto.
  *Por qué*: corregir los archivos afectados sin corregir las herramientas que los generaban habría reproducido el problema en la siguiente ejecución.
  *Impacto*: sin cambios en el código de los servicios. Se comprobó con un control positivo: el revisor de estilo entrega exactamente los mismos resultados con el acceso de solo lectura que con el anterior, de modo que el cambio no altera lo que se analiza.

- `fix(frontend)`: **El formulario de alta de las pantallas de administración se reabría con lo escrito antes; en «Usuarios», con la casilla ROOT aún marcada**
  Siete pantallas de administración comparten el mismo esqueleto: una tabla, un botón «Nuevo…» y una ventana con el formulario de alta. Al unificar ese esqueleto, se perdió el paso que vaciaba el formulario al abrirlo. Consecuencia: si alguien pulsaba «Nuevo…», escribía algo y cancelaba, al volver a abrir la ventana reaparecía todo lo anterior, incluido el mensaje de error de la vez pasada.
  En la pantalla de **Usuarios** esto dejaba de ser una molestia: la casilla «Otorgar ROOT» —que concede control total sobre el sistema— también conservaba la marca. Alguien podía marcarla, arrepentirse, cancelar y, al dar de alta a la siguiente persona, concederle ese control sin darse cuenta de que la casilla seguía activada. Es precisamente la pantalla con más riesgo de conceder privilegios de más.
  Lo detectó el revisor automático de código, que señalaba siete funciones de limpieza que ya nadie invocaba. Es la segunda vez que ese revisor, una vez puesto en marcha, saca a la luz un fallo real que las demás comprobaciones no veían. Una de las siete pantallas ni siquiera aparecía señalada, así que también se revisó a mano: el aviso automático marca el síntoma, no el problema.
  *Por qué*: lo que parecía código sobrante era en realidad la limpieza del formulario; en Usuarios el residuo tiene consecuencias de seguridad.
  *Impacto*: corregido en el componente compartido y en las siete pantallas, con cinco pruebas nuevas que reproducen el caso de la casilla ROOT y que se comprobaron al revés — sin el arreglo, fallan.

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
