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

## Autenticación

| Ruta | Propósito |
|---|---|
| `(public)/login` | Inicia el flujo OAuth2 Authorization Code + PKCE contra Keycloak |
| `(public)/login/callback` | Recibe el código de autorización, lo intercambia por tokens (server-side) y establece la sesión |
| `(public)/logout` | Cierra sesión local y en Keycloak |
| `/403` | Página de acceso denegado cuando el usuario no tiene el permiso requerido |

## Pantallas operativas (`(app)/`)

| Ruta | Pantalla | Propósito | Rol típico | Endpoints principales (vía gateway) |
|---|---|---|---|---|
| `/dashboard` | Panel principal | Resumen de actividad del usuario/tenant: pendientes, alertas, atajos | Todos los usuarios autenticados | `GET /api/v1/documents`, `GET /api/v1/workflows` (resumen) |
| `/bandeja` | Bandeja de entrada/trámites | Ver, tramitar, devolver, anular o responder radicados asignados; solicitar/otorgar vistos buenos (individual o en cadena); ajustar nivel de seguridad | Operador de correspondencia, funcionario de dependencia | `GET/PATCH /api/v1/documents`, `/api/v1/workflows`, `/api/v1/documents/{id}/anulacion`, `/api/v1/documents/{id}/respuesta` |
| `/radicar` | Radicación de documentos | Registrar un radicado de entrada, salida o interno con sus anexos | Ventanilla única, operador de correspondencia (`PERM_RADI`, `PERM_RADI_SALIDA`) | `POST /api/v1/documents`, `POST /api/v1/storage/upload` |
| `/borradores` | Borradores | Preparar un radicado antes de asignarle número; aprobarlo o radicarlo definitivamente | Funcionario redactor | `GET/POST /api/v1/documents/borradores`, acción "radicar" |
| `/busqueda` | Búsqueda de documentos | Búsqueda full-text (FTS PostgreSQL) sobre radicados y expedientes, con filtros | Todos los usuarios con permiso de consulta | `GET /api/v1/search` |
| `/expedientes` | Listado de expedientes | Ver expedientes abiertos/cerrados/transferidos, crear uno nuevo | Archivista, funcionario con `USUA_PERM_EXPEDIENTE` | `GET/POST /api/v1/expedientes` |
| `/expedientes/[id]` | Detalle de expediente | Agregar/quitar radicados, ver anexos, cerrar el expediente, descargar el índice electrónico XML firmado, exportar ZIP, iniciar transferencia | Archivista | `GET/PATCH /api/v1/expedientes/{id}`, `GET /api/v1/expedientes/{id}/indice`, `GET /api/v1/expedientes/{id}/export` |
| `/archivo-fisico` | Archivo físico | Gestionar ubicaciones (direcciones recursivas), unidades de conservación, préstamos y su FUID | Custodio de archivo central | `GET/POST /api/v1/ubicaciones`, `/api/v1/unidades`, `/api/v1/prestamos` |
| `/transferencias` | Transferencias documentales | Enviar/recibir/rechazar transferencias entre archivo de gestión y central, generar FUID | Archivista, jefe de dependencia | `POST /api/v1/transferencias`, acciones enviar/recibir/rechazar |
| `/firmas` | Firmas electrónicas | Firmar (individual o en lote) radicados/expedientes pendientes, rechazar firma | Firmante autorizado (`PERM_FIRMA`) | `GET/POST /api/v1/signature`, `/api/v1/signature/cadena` |
| `/envios` | Envíos/correspondencia postal | Gestionar remesas hacia el operador postal externo y su confirmación de entrega | Operador de correspondencia | `POST /api/v1/documents` (postal), webhook entrante del operador |
| `/reportes` | Reportes | Generar y exportar reportes (CSV) de gestión documental | Jefe de dependencia, administrador | `GET /api/v1/reports` |
| `/asistente` | Asistente conversacional | Chat en lenguaje natural que consulta el sistema (RAG con citas) traduciendo la pregunta a llamadas MCP de solo lectura | Cualquier usuario autenticado | `POST /api/v1/assistant/chat` (vía `mcp-server`) |
| `/admin/plantillas` | Administración de plantillas | Definir plantillas de metadatos documentales/de expediente (Form Builder con SurveyJS) | Administrador (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/metadata/templates` |
| `/_kit` | Kit de componentes UI | Showcase interno de componentes del sistema de diseño Orpyca (solo entorno de desarrollo, no producción) | Desarrolladores | — |

## Notas de diseño

- **Sistema de diseño Orpyca**: todos los componentes reutilizables
  (`DataTable`, `Drawer`, `Modal`, `StatusChip`, `FormField`, `Button`, `Loader`,
  `Stepper`) viven en `frontend/src/lib/components/ui/` y usan tokens de diseño
  `--op-*` (colores/espaciados), nunca valores hex sueltos (ADR-020).
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
