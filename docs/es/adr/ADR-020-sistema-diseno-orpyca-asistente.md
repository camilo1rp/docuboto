# ADR-020: Sistema de diseño Orpyca y asistente conversacional del frontend

**Estado**: Propuesto
**Fecha**: 2026-06-23
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

[ADR-011](ADR-011-frontend-sveltekit-monorepo.md) decidió **dónde** vive el frontend (carpeta `frontend/` del monorepo) y **con qué stack** (SvelteKit SSR + Bulma/Sass, OAuth2 PKCE vía `orpycamcp-frontend`), pero dejó abierto **cómo se ve y cómo se opera** la interfaz. La épica E22 (fase final de presentación) debe resolver tres decisiones de producto/diseño que condicionan toda la UI y que conviene fijar antes de codificar:

1. **Identidad visual y consistencia.** Las implementaciones legadas de Orfeo (ICETEX, BogotáLimpia, 9342, …) sufren de pantallas heterogéneas, flujos partidos en **múltiples pestañas** y formularios densos que penalizan a quien trabaja jornadas largas en ventanilla. Hace falta una **plantilla única**, minimalista y con una **paleta de marca** consistente, no un tema por módulo.

2. **Personalización por tenant y por rol.** OrpycaMCP es multi-tenant ([ADR-002](ADR-002-multi-tenancy-schema-isolation.md)). Cada institución necesita su marca (logo/colores) sin recompilar, y cada rol (operador de ventanilla, gestor, archivista, administrador) debe ver **solo** su superficie de trabajo.

3. **Asistente conversacional (texto y voz).** El requisito de producto es operar el sistema "conversando": radicar, buscar, consultar expedientes, adjuntar anexos y automatizar tareas por lenguaje natural, **sin** que esa comodidad abra un agujero de seguridad ni filtre datos a terceros (instituciones públicas, Ley 1581/2012 de datos personales).

La pregunta de esta ADR: **qué sistema de diseño adopta el frontend y cómo se integra el asistente sin romper las garantías de la plataforma** (Keycloak, multi-tenant, RBAC, auditoría, soberanía del dato).

## Decisión

**El frontend adopta un sistema de diseño único "Orpyca" basado en tokens (Sass + CSS custom properties para branding en runtime por tenant), una plantilla de aplicación única con UI condicionada por rol, y un asistente conversacional (texto + voz) que NO ejecuta acciones por sí mismo: traduce lenguaje natural a *tools* de la capa MCP existente ([ADR-019](ADR-019-capa-mcp.md)), que pasan por el gateway y heredan toda la seguridad. La inferencia (LLM y STT) corre con proveedores locales *pluggable*, desactivados por defecto mediante perfiles de Docker Compose.**

### 1. Sistema de diseño por tokens (paleta Orpyca)

- **Doble expresión de tokens**: la paleta se define como **variables Sass** (alimentan a Bulma en build) **y** como **CSS custom properties `--op-*`** en `:root`. Las variables Sass dan el tema base; las custom properties permiten **rebrandear por tenant en runtime** (inyectando un `<style>` con overrides de `--op-*` desde los claims/config del tenant) sin recompilar.
- **Paleta Orpyca** (archivo origen `~/Downloads/orpyca_paleta.mb`, NO el verde de sgdINTI):

  | Token | Valor | Uso |
  |---|---|---|
  | `--op-primary` | `#459940` | acción principal, marca |
  | `--op-primary-dark` | `#305336` | hover/activo, texto sobre claro |
  | `--op-primary-light` | `#DCEED7` | fondos sutiles, selección |
  | `--op-accent` | `#94BC2F` | acentos puntuales |
  | `--op-text` | `#212421` | texto primario |
  | `--op-text-secondary` | `#515551` | texto secundario |
  | `--op-border` | `#D8DDD6` | bordes/divisores |
  | `--op-bg` | `#F5F7F4` | fondo de página |
  | `--op-surface` | `#FFFFFF` | tarjetas/superficies |
  | `--op-success` / `--op-warning` / `--op-error` / `--op-info` | `#459940` / `#D8A32E` / `#D9534F` / `#3E7FA6` | estados |

- **Regla de proporción 60/30/10** (neutro/primario/acento) para evitar saturación de verde en pantallas de uso prolongado.
- **Plantilla única**: `AppLayout` (sidebar + topbar + breadcrumb + área de contenido), reutilizando la estructura validada en `~/Documents/sgdINTI/frontend` (AppLayout/SidebarNav/BreadcrumbBar, `api.service.js`, stores `auth/permissions/roles/ui`), repintada con la paleta Orpyca.

### 2. Vistas consolidadas y UI por rol

- **Anti multi-pestaña**: cada caso de uso del legado que se repartía en varias pestañas se rediseña como **una vista con pasos guiados** o paneles contextuales (detalle de radicado con acciones de operador en sitio, flujo/historial inline). La matriz de cobertura capacidad→superficie UI vive en `E22-frontend/spec.md §4.1`.
- **UI por rol**: la navegación y las acciones se filtran por `roles[]`/`permissions[]` del JWT. Cuatro roles base: **operador de ventanilla, gestor/funcionario, archivista, administrador de tenant**. La UI **nunca** ofrece lo que la API no autorizaría — es espejo del RBAC del backend ([ADR-013](ADR-013-autorizacion-resuelta-en-bd.md)), no una segunda fuente de verdad.
- **Tenant resuelto del claim del JWT**: una sola URL; el subdominio por tenant queda como evolución futura sin coste de rediseño.

### 3. Asistente conversacional como traductor a *tools* MCP

- **No ejecuta, traduce**: el asistente convierte lenguaje natural en invocaciones de las *tools* del `mcp-server` ([ADR-019](ADR-019-capa-mcp.md)), que ya están mapeadas 1:1 a endpoints REST, filtradas por permisos y con gate de escritura. El asistente **no** accede a BD ni a servicios; toda acción viaja `frontend → gateway → mcp-server/servicio`, propagando el token Keycloak y `X-Tenant-Slug`, y queda **auditada** como cualquier otra llamada.
- **Contratos** (consumidos siempre por el gateway):
  - `POST /api/v1/assistant/message` — entrada NL → respuesta + *tool calls* resueltas; subida de anexos al chat reutiliza E07.
  - `POST /api/v1/assistant/transcribe` — audio → texto (STT) para los comandos de voz.
- **Inferencia local y *pluggable*** (soberanía del dato, alineado con [ADR-006](ADR-006-proveedor-ia-configurable.md)):
  - **LLM local** (Ollama / llama.cpp) por defecto; proveedor configurable, sin API externa obligatoria.
  - **STT en servidor con Whisper** (proveedor *pluggable*, `stub` por defecto); el audio no sale a terceros.
- **Opt-in por perfiles de Compose**: los servicios pesados de IA (Ollama, Whisper) y de edición en línea (OnlyOffice, ver E07 §9) se declaran con `profiles: ["assistant"]` / `["editor"]` y **arrancan desactivados**. Una instalación mínima corre sin GPU ni contenedores de inferencia; quien quiera el asistente activa el perfil.

### 4. Entrega gradual (5 fases de E22)

(1) plantilla base + tokens + login PKCE; (2) vistas consolidadas anti-multipestaña + editor TipTap (borradores de Salida); (3) asistente + voz; (4) personalización tenant + roles; (5) pruebas. **Cada fase se cierra antes de abrir la siguiente.** Esta ADR fija las decisiones transversales; el detalle de tareas vive en `E22-frontend/{spec,plan,tasks}.md`.

## Consecuencias

**Positivas:**
- **Una sola identidad visual** y un único `AppLayout` → coherencia, menor carga cognitiva en jornadas largas, mantenimiento barato.
- **Rebranding por tenant sin recompilar** gracias a las custom properties `--op-*` en runtime.
- **El asistente no amplía la superficie de ataque**: al delegar en *tools* MCP que pasan por el gateway, hereda autenticación, RBAC, multi-tenant y auditoría; no puede saltárselos aunque el LLM "alucine" una acción no permitida (la API la rechaza).
- **Soberanía del dato**: LLM y STT locales; nada del contenido documental ni de la voz sale a un proveedor externo por defecto.
- **Coste de operación opcional**: sin el perfil `assistant`, no hay contenedores de inferencia; el sistema corre en hardware modesto.

**Negativas / límites:**
- Mantener tokens en dos formatos (Sass + custom properties) exige disciplina para no divergir; se mitiga con un único archivo fuente de tokens y linql del tema.
- El asistente queda acoplado a la **calidad del catálogo de tools** y del LLM local; un modelo pequeño puede fallar en intención compleja (mitigable: confirmación explícita antes de toda *tool* de escritura).
- La inferencia local añade latencia y consumo (RAM/CPU/GPU) cuando el perfil está activo; es coste asumido a cambio de privacidad y aceptable para una capa de asistencia, no de alto rendimiento.
- SSR + branding por tenant en runtime obliga a inyectar el tema en cada render del tenant; coste menor frente a recompilar por institución.

## Alternativas consideradas

- **Tema por módulo / CSS ad hoc por pantalla** (statu quo del legado): descartado — produce la heterogeneidad que esta ADR busca eliminar.
- **Solo variables Sass (sin custom properties)**: descartado — obligaría a recompilar el frontend por cada tenant; las custom properties habilitan el rebranding en runtime.
- **Asistente que ejecuta acciones contra los servicios directamente**: descartado — duplicaría RBAC/tenant/auditoría y rompería el respeto de capas; el camino correcto es reutilizar la capa MCP ([ADR-019](ADR-019-capa-mcp.md)).
- **LLM/STT en la nube (OpenAI, etc.)**: descartado como opción por defecto — instituciones públicas con datos personales; choca con la soberanía del dato. Sigue siendo posible como proveedor *pluggable* para quien lo decida y asuma.
- **Inferencia siempre encendida**: descartado — encarece el despliegue mínimo; los perfiles de Compose la dejan opt-in.
- **Otro framework de UI / design system de terceros (Material, etc.)**: descartado — se mantiene Svelte/Bulma de [ADR-011](ADR-011-frontend-sveltekit-monorepo.md) y se construye un sistema de tokens propio ligero en vez de adoptar una librería pesada.

## Relacionados

- [ADR-011](ADR-011-frontend-sveltekit-monorepo.md) — ubicación y stack del frontend; esta ADR define su diseño y asistente.
- [ADR-019](ADR-019-capa-mcp.md) — capa MCP; el asistente es un cliente NL de esas *tools*, no una vía paralela.
- [ADR-006](ADR-006-proveedor-ia-configurable.md) — proveedor de IA configurable; el LLM/STT local sigue ese patrón *pluggable*.
- [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) — la UI por rol espeja el RBAC resuelto en la BD; no es fuente de verdad.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — el branding y el scope por tenant se resuelven con el claim del JWT.
- `documentos/specDrive/epics/E22-frontend/{spec,plan,tasks}.md` — detalle de requisitos (RF-UI-01…21), plan técnico y tareas (gitignored).
