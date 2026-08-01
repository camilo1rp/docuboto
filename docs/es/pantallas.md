# Pantallas del sistema

El frontend de OrpycaMCP (`frontend/`, SvelteKit 2 + Svelte 4 SSR, E22) es la única
capa de presentación oficial del sistema. Ninguna pantalla accede a datos
directamente: cada vista tiene su propia subcarpeta `api/` que actúa como
**Backend-for-Frontend (BFF)** — recibe la acción del navegador, adjunta el JWT
(guardado en cookie httpOnly, nunca visible al JS del cliente) y reenvía la petición
al `api-gateway`. Ver el detalle de esta arquitectura en
[Arquitectura](architecture.md), sección "Flujo de autenticación".

La navegación y las acciones visibles en cada pantalla reflejan el RBAC real del
usuario (`PERM_RADI`, `USUA_PERM_EXPEDIENTE`, `USUA_PERM_ADMIN`, `PERM_RADI_SALIDA`,
`PERM_FIRMA`, `PERM_DLQ_ADMIN`, …), pero esa restricción es solo defensa en
profundidad en la UI — el backend siempre revalida el permiso y el nivel de
clearance (RF-SEG-08) por request.

## Pantallas públicas (`(public)/`)

| Ruta | Propósito |
|---|---|
| `(public)/` | **Landing pública** del producto: qué es OrpycaMCP, marco normativo (Ley 594/2000, Acuerdo AGN 060/2001), capacidades, comparativa con Orfeo y licencia AGPL v3. Si `locals.user` está poblado (sesión activa), su `+page.server.js` **redirige 302 a `/dashboard`** — un usuario autenticado nunca la ve |

## Autenticación

| Ruta | Propósito |
|---|---|
| `(public)/login` | Inicia el flujo OAuth2 Authorization Code + PKCE contra Keycloak. Composición **split**: en móvil solo la tarjeta de ingreso; desde 960px aparece el panel de marca. Si Keycloak devuelve `?error=`, muestra un mensaje mapeado desde una tabla cerrada en español (nunca el string crudo del IdP) con enlace «Reintentar» — evita el bucle de redirección |
| `(public)/login/callback` | Recibe el código de autorización, lo intercambia por tokens (server-side) y establece la sesión |
| `(public)/logout` | Cierra sesión local y en Keycloak |
| `/403` | Página de acceso denegado cuando el usuario no tiene el permiso requerido |

## Pantallas operativas (`(app)/`)

| Ruta | Pantalla | Propósito | Rol típico | Endpoints principales (vía gateway) |
|---|---|---|---|---|
| `/dashboard` | Panel personal | **Cuatro módulos personales** visibles sin scroll: 01 Radicados pendientes, 02 Actividad reciente, 03 Alertas (radicados abiertos por `due_date`, RF-RAD-04), 04 Favoritos. Las **métricas agregadas del tenant** quedan en una sección secundaria colapsable, cerrada por defecto y gateada por `SGD_PERM_ESTADISTICA` | Todos los usuarios autenticados (métricas: `SGD_PERM_ESTADISTICA`) | `GET /api/v1/documents`, `GET /api/v1/reports/radicados` (solo la sección de métricas) |
| `/bandeja` | Bandeja de entrada/trámites | Ver, tramitar, devolver, anular o responder radicados asignados; solicitar/otorgar vistos buenos (individual o en cadena); ajustar nivel de seguridad; ver/fijar la disposición final (TRD) del radicado individual, con confirmación explícita para la acción de eliminación; el drawer de detalle incluye una sección "Respuestas" con las Salidas vinculadas | Operador de correspondencia, funcionario de dependencia | `GET/PATCH /api/v1/documents`, `/api/v1/workflows`, `/api/v1/documents/{id}/anulacion`, `/api/v1/documents/{id}/respuesta`, `GET /api/v1/documents/{id}/respuestas`, `GET/PATCH /api/v1/documents/{id}/disposition` |
| `/radicar` | Radicación de documentos | Registrar un radicado de entrada, salida o interno con sus anexos; botón "Sugerir con antecedentes" (RAG con citas, F6) para el cuerpo de Salida/Interno y chips de tipo documental sugerido (nunca autoaplicados) junto al selector de clasificación | Ventanilla única, operador de correspondencia (`PERM_RADI`, `PERM_RADI_SALIDA`) | `POST /api/v1/documents`, `POST /api/v1/storage/upload`, `POST /api/v1/knowledge/rag`, `POST /api/v1/knowledge/antecedentes` |
| `/borradores` | Borradores | Preparar un radicado antes de asignarle número; aprobarlo o radicarlo definitivamente; botón "Sugerir con antecedentes" (RAG con citas, F6) en el editor del cuerpo | Funcionario redactor | `GET/POST /api/v1/documents/borradores`, acción "radicar", `POST /api/v1/knowledge/rag` |
| `/busqueda` | Búsqueda de documentos | Tres pestañas deep-linkeables (`?tab=`): **Exacta** (full-text FTS PostgreSQL con filtros), **Semántica** (documentos parecidos por significado, similitud vectorial) y **Antecedentes** (trámites relacionados, por texto o por un radicado pivote verificado) — F6. Los resultados de Semántica/Antecedentes se presentan siempre como "parecidos", nunca como coincidencia exacta; el recorte por permisos de acceso es invisible por diseño | Todos los usuarios con permiso de consulta | `GET /api/v1/search`, `POST /api/v1/knowledge/search`, `POST /api/v1/knowledge/antecedentes`, `GET /api/v1/documents/by-tracking/{tracking}` |
| `/expedientes` | Listado de expedientes | Ver expedientes abiertos/cerrados/transferidos, crear uno nuevo | Archivista, funcionario con `USUA_PERM_EXPEDIENTE` | `GET/POST /api/v1/expedientes` |
| `/expedientes/[id]` | Detalle de expediente | Agregar/quitar radicados, ver anexos, cerrar el expediente, descargar el índice electrónico XML firmado, exportar ZIP, iniciar transferencia; ver (solo lectura) la disposición final materializada al cierre o, si sigue abierto, una vista previa de la regla de la serie TRD asignada | Archivista | `GET/PATCH /api/v1/expedientes/{id}`, `GET /api/v1/expedientes/{id}/indice`, `GET /api/v1/expedientes/{id}/export`, `GET /api/v1/trd/{serie_id}` |
| `/archivo-fisico` | Archivo físico | Gestionar ubicaciones (direcciones recursivas), unidades de conservación, préstamos y su FUID | Custodio de archivo central | `GET/POST /api/v1/ubicaciones`, `/api/v1/unidades`, `/api/v1/prestamos` |
| `/transferencias` | Transferencias documentales | Enviar/recibir/rechazar transferencias entre archivo de gestión y central, generar FUID | Archivista, jefe de dependencia | `POST /api/v1/transferencias`, acciones enviar/recibir/rechazar |
| `/transferencias/[id]` | Detalle de transferencia (acta-certificado) | Ver el acta de entrega en apariencia de certificado (qué/entre quién/cuándo), el sello institucional XAdES, y firmar personalmente como elaborador/remitente/receptor — el rol de cada persona se deriva de su identidad, nunca se elige; verificar sello y firmas on-demand | Archivista, jefe de dependencia con `USUA_PERM_EXPEDIENTE` (firma personal: además `PERM_FIRMA` de facto, gate del backend) | `GET /api/v1/transferencias/{id}`, `.../acta`, `.../acta/verificacion`, `POST .../acta/firmar-personal`, `POST .../acta/firmar` |
| `/firmas` | Firmas electrónicas | Firmar (individual o en lote) radicados/expedientes pendientes, rechazar firma | Firmante autorizado (`PERM_FIRMA`) | `GET/POST /api/v1/signature`, `/api/v1/signature/cadena` |
| `/envios` | Envíos/correspondencia postal | Gestionar remesas hacia el operador postal externo y su confirmación de entrega | Operador de correspondencia | `POST /api/v1/documents` (postal), webhook entrante del operador |
| `/reportes` | Reportes | Generar y exportar reportes (CSV) de gestión documental; pestaña "Índice de clasificados" (Ley 1712 art. 20) — registro content-free de radicados reservados/clasificados con su fundamento jurídico, sin exponer asunto/contenido | Jefe de dependencia, administrador (índice de clasificados: `PERM_RECLASIFICAR`) | `GET /api/v1/reports/radicados`, `GET /api/v1/reports/indice-reservado[.csv]` |
| `AssistantDock` (no es una ruta) | Asistente conversacional | Chat en lenguaje natural que consulta el sistema (RAG con citas) traduciendo la pregunta a llamadas MCP de solo lectura. Es un panel flotante montado en `AppLayout.svelte`, disponible desde cualquier pantalla: **no existe una página `/asistente`**, solo el proxy BFF `(app)/asistente/api/message` | Cualquier usuario autenticado | `POST /api/v1/assistant/message` (vía `mcp-server`) |
| `/admin/preservacion` | Administración de preservación digital | Consultar y actualizar el plan de preservación (versionado), ver su historial; visibilidad operacional de índices electrónicos pendientes de sellado XAdES (con reintento manual) y de artefactos WORM (índice/acta/AIP) pendientes de renovación de retención de Conservación Total. **No** ofrece empaquetado manual de AIP ni protección WORM manual de un artefacto arbitrario: ambos requieren datos (`documentos[]`/`file_id`) que solo `archive-service` conoce en el contexto de un expediente, y la renovación de retención es, por diseño, disparo de sistema únicamente | Archivista, administrador (`USUA_PERM_EXPEDIENTE`; reintento de sellado además `PERM_FIRMA`) | `GET/PUT /api/v1/preservacion/plan`, `GET /api/v1/preservacion/plan/versions`, `GET /api/v1/expedientes/indices/pendientes-firma`, `GET /api/v1/expedientes/indices/pendientes-renovacion`, `POST /api/v1/expedientes/{id}/indice/firmar` |
| `/admin/plantillas` | Administración de plantillas | CRUD de plantillas de **cuerpo** de documentos de salida, con editor de texto enriquecido. **No** es un constructor de formularios: las plantillas de **metadatos** (JSON Schema por serie TRD/tipo documental) se administran en `/admin/metadatos` | Administrador (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/plantillas` |
| `/admin/trd` | Administración de TRD / CCD | Dos pestañas: series TRD/CCD en **árbol indentado** (jerarquía real vía `parent_id`, nunca lista plana) con alta/edición (código inmutable); tipos documentales (tercer nivel), con alta/edición/borrado y filtro por serie | Administrador de TRD (`USUA_PERM_TRD`, gate real del backend — más específico que `USUA_PERM_ADMIN`, que solo controla la visibilidad del panel `/admin`) | `GET/POST /api/v1/trd`, `PATCH /api/v1/trd/{id}`, `GET/POST/PATCH/DELETE /api/v1/tipos-documentales` |
| `/admin/metadatos` | Administración de metadatos | Tres pestañas: plantillas de metadatos de **expediente** (por serie TRD, archive-service) y de **documento** (por tipo documental, document-service) — ambas de solo alta (inmutables, JSON Schema editado como texto con validación de sintaxis antes de enviar; nueva versión = nuevo registro); elementos de metadato reutilizables (CRUD completo). Reparto con `/admin/trd`: TRD administra la clasificación, esta pantalla administra los campos de esos esquemas | Administrador (`USUA_PERM_ADMIN` del lado del cliente; los routers de plantilla/elemento de document-service **no gatean por permiso en el backend hoy** — hallazgo de dominio documentado en el código, no corregido en el front) | `GET/POST /api/v1/expediente-metadata/templates`, `GET/POST /api/v1/metadata/templates`, `GET/POST/PATCH/DELETE /api/v1/metadata/elements` |
| `/admin/catalogos` | Administración de catálogos | Maestro-detalle: lista de catálogos de referencia del tenant a la izquierda, items del seleccionado a la derecha (CRUD completo); en móvil degrada a dos niveles navegables con botón «Volver», nunca a columnas apretadas | Administrador (`USUA_PERM_ADMIN` del lado del cliente; `catalogos.py` tampoco gatea por permiso en el backend hoy) | `GET /api/v1/catalogos`, `GET/POST /api/v1/catalogos/{catalogo}`, `PATCH/DELETE /api/v1/catalogos/{catalogo}/{id}` |
| `/admin/parametros` | Administración de parámetros | Tres secciones: parámetros del tenant (clave→valor JSON libre), calendario de festivos (alta puntual + precarga Ley 51/1983 sin llamada externa), y calculadora de días hábiles que muestra el **desglose** de fines de semana/festivos descontados en el rango (el backend solo devuelve la fecha resultante; el desglose se reconstruye en el cliente a partir del mismo catálogo de festivos ya cargado) | Administrador (`USUA_PERM_ADMIN` del lado del cliente; `config.py` documenta explícitamente que el gate de permiso está pendiente) | `GET/PUT /api/v1/config/params[/{key}]`, `GET/POST/DELETE /api/v1/config/holidays`, `POST /api/v1/config/holidays/seed`, `GET /api/v1/business-days/calculate` |
| `/admin/grupos` | Administración de grupos | Listar/crear grupos; drawer de detalle con miembros, permisos por grupo y clearance (RF-SEG-08). **No** hay edición ni borrado de grupo (el backend solo tiene alta+lectura). El backend **tampoco expone lectura** de membresías/permisos/clearance ya asignados a un grupo (solo altas/bajas), advertido explícitamente en el drawer — las acciones se aplican de inmediato sin poder mostrar "el estado actual" | Administrador (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/auth/groups`, `GET /api/v1/auth/permissions`, `GET /api/v1/auth/security-levels`, `POST/DELETE /api/v1/auth/groups/{id}/members/{user_id}`, `PUT /api/v1/auth/groups/{id}/permissions/{permission_id}`, `PUT /api/v1/auth/groups/{id}/clearance` |
| `/admin/dependencias` | Administración de dependencias | Vista árbol del organigrama institucional (`GET /dependencias/tree`); crear, editar y activar/desactivar. Renombrar o desactivar dispara un modal de confirmación explícito (no un tooltip): las dependencias se correlacionan por **nombre**, no por id/código, en `workflow_rules.assign_to_dept` y en los `flow_steps` en vuelo — ambos pueden quedar huérfanos. **No** ofrece borrado duro (el backend lo tiene, bloqueado si hay hijos, pero el alcance de esta pantalla es crear/editar/desactivar) | Administrador (`USUA_PERM_ADMIN`) | `GET /api/v1/dependencias`, `GET /api/v1/dependencias/tree`, `POST /api/v1/dependencias`, `PATCH /api/v1/dependencias/{id}` |
| `/_kit` | Kit de componentes UI | Showcase interno de componentes del sistema de diseño Orpyca (solo entorno de desarrollo, no producción) | Desarrolladores | — |

## Navegación global

Presentes en **todas** las pantallas `(app)/`, provistas por `AppLayout` y `SidebarNav`:

- **Búsqueda global** (`role="search"` en la barra superior): navega a
  `/busqueda?q=…`, la misma vista de búsqueda full-text con sus filtros avanzados.
  Atajo de teclado <kbd>/</kbd>, inhibido cuando el foco ya está en un campo editable.
  En pantallas estrechas se despliega desde un icono. Como el término viaja en la URL,
  toda búsqueda es enlazable y compartible.
- **Favoritos**: cada entrada del menú lateral tiene un control fijar/desfijar
  (`aria-pressed`, nunca señalizado solo por color). Los ítems fijados encabezan el menú
  y alimentan el módulo 04 del `/dashboard`. Se persisten en `localStorage`
  **namespaced por tenant + usuario** (`lib/stores/favorites.js`, con guardia SSR).
  El store **no filtra por sí mismo**: recibe la lista ya filtrada por permisos desde
  `lib/utils/navItems.js`, de modo que un favorito cuyo permiso se retira desaparece
  solo. El catálogo `NAV_ITEMS` y su filtro RBAC son la **fuente única** compartida por
  `SidebarNav` y el `/dashboard`.

## Notas de diseño

- **Sistema de diseño Orpyca v1.0**: todos los componentes reutilizables
  (`DataTable`, `Drawer`, `Modal`, `StatusChip`, `FormField`, `Button`, `Loader`,
  `Stepper`, `Card`, `EmptyState`, `Toast`, `Stepper`, `UserPicker`) viven en
  `frontend/src/lib/components/ui/` y usan tokens de diseño `--op-*`
  (colores/espaciados/tipografía/radios/sombras/movimiento), nunca valores hex sueltos
  (ADR-020). La fuente única es `src/styles/_tokens.scss`.
- **Contraste y uso del verde de marca**: el primario v1.0 (`--op-primary`, `#2A8C3A`)
  mide **4.28:1** sobre blanco, por debajo del 4.5:1 que WCAG AA exige a texto normal.
  Por eso el token está **partido por rol**: `--op-primary` solo para elementos
  **no textuales** (bordes, iconos, anillos de foco, umbral ≥3:1) y `--op-primary-dark`
  (`#1C6B33`, **6.56:1**) para todo **texto** —incluidos `$primary`/`$link` de Bulma— o
  los pares `--op-*-fg` verificados. Los semánticos crudos (`--op-warning`,
  `--op-error`, …) son colores de **borde/icono**; su versión de texto es `--op-*-fg`.
  `--op-accent` (`#8DBF3C`, 2.18:1) **nunca** se usa como texto sobre fondo claro.
- **Tipografía**: Space Grotesk (display, títulos, cifras) + Public Sans (UI y cuerpo),
  **self-hosted** vía `@fontsource` — sin CDN externo, por soberanía y por no filtrar
  la navegación de los usuarios a un tercero.
- **Movimiento**: `_tokens.scss` declara una regla global `prefers-reduced-motion` que
  neutraliza toda transición y animación del sitio; ninguna pantalla necesita repetirla.
- **`DataTable` es la tabla del sistema**: expone ocho capacidades **opt-in por prop**
  (filtros por columna, ordenamiento con `aria-sort`, exportación CSV, columnas
  configurables, vistas guardadas, selección múltiple, virtualización a nivel de render
  y edición en celda) con el comportamiento simple como valor por defecto. La
  virtualización usa `content-visibility` en vez de retirar filas del DOM: es menos
  agresiva, pero no rompe la semántica nativa de `<table>` para lectores de pantalla.
  La usan `/busqueda`, `/expedientes`, `/firmas`, `/envios` y los cuatro paneles de
  `/reportes`; `/bandeja` y `/archivo-fisico` siguen con tabla propia (ver
  [Roadmap](roadmap.md)).
- **Edición de texto enriquecido** (respuestas, observaciones): usa TipTap 3, con el
  HTML saneado **server-side** antes de guardar y antes de renderizar — nunca se
  interpola HTML crudo del usuario.
- **Exportación CSV** desde el navegador neutraliza inyección de fórmulas (antepone
  `'` a celdas que empiezan con `= + - @`).
- No existen capturas de pantalla en esta documentación — las tablas anteriores
  describen la funcionalidad real implementada; para verla en ejecución, levantar la
  plataforma (ver [Primeros pasos](getting-started.md)) y navegar a
  `http://localhost:19300`.

## Ver también

- [Arquitectura](architecture.md) — cómo cada pantalla se conecta con los
  microservicios a través del gateway.
- [Referencia de API](api.md) — contrato completo de cada endpoint listado arriba.
