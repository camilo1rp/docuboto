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
- **Paleta Orpyca** — la tabla siguiente refleja el **Sistema de Diseño OrpycaMCP v1.0** (2026-07), que reemplazó los valores provisionales con los que se escribió originalmente esta ADR (`#459940` / `#305336` / `#94BC2F` / `#212421` / `#515551` / `#D8DDD6` / `#F5F7F4`, y los estados `#D8A32E` / `#D9534F` / `#3E7FA6`). **Los nombres de token no cambiaron**, solo sus valores, por lo que ninguna pantalla tuvo que tocarse. La fuente única es `frontend/src/styles/_tokens.scss`:

  | Token | Valor | Uso |
  |---|---|---|
  | `--op-primary` | `#2A8C3A` | marca 500 — acción principal. **Solo usos NO textuales** (ver nota de contraste) |
  | `--op-primary-dark` | `#1C6B33` | marca 600 — hover/activo **y color AA-seguro para TEXTO** |
  | `--op-primary-light` | `#DCEED7` | fondos sutiles, selección |
  | `--op-on-primary` | `#FFFFFF` | texto sobre superficie primaria oscura |
  | `--op-accent` | `#8DBF3C` | marca lime 300 — acentos puntuales. **NUNCA como texto sobre claro** (2.18:1) |
  | `$op-brand-700` / `$op-brand-400` | `#173A22` / `#4FA84B` | extensión de marca para degradados profundos (hero, panel de login) |
  | `--op-text` | `#232624` | ink — texto primario |
  | `--op-text-secondary` | `#6E7370` | muted — texto secundario |
  | `--op-border` | `#E7EAE2` | hairline — bordes/divisores |
  | `--op-bg` | `#FBFCF8` | canvas — fondo de página |
  | `--op-surface` | `#FFFFFF` | tarjetas/superficies |
  | `--op-success` / `--op-warning` / `--op-error` / `--op-info` | `#2A8C3A` / `#C8881C` / `#C4452C` / `#2B6CB0` | estados — color de **borde/icono** (≥3:1) |
  | `--op-*-bg` / `--op-*-fg` | ver `_tokens.scss` | pares verificados **≥4.5:1 AA** para chips, toasts y semáforos |

- **Contraste: el primario está partido por rol.** El verde de marca v1.0 (`#2A8C3A`) mide **4.28:1** sobre blanco — cumple el umbral no-textual de 3:1 pero **falla el 4.5:1 que WCAG AA exige a texto normal**. Por eso `--op-primary` conserva el valor exacto de v1.0 y se reserva a bordes, iconos y anillos de foco, mientras que **todo uso de texto** —botones, tags, enlaces, `.text-primary`, skip-link, y las variables Sass `$primary`/`$link` que alimentan las clases autogeneradas de Bulma— consume `--op-primary-dark` (**6.56:1**) o el par `--op-*-fg` correspondiente. Los ratios están calculados con la fórmula de luminancia relativa WCAG (no estimados) y anotados junto a cada token en `_tokens.scss`.
- **Tipografía**: **Space Grotesk** (display, títulos, cifras destacadas) + **Public Sans** (UI y cuerpo), **self-hosted** vía `@fontsource` — sin CDN externo, tanto por soberanía como para no filtrar la navegación de los usuarios a un tercero. Escala v1.0: Display 46/700 · H1 28/600 · H2 22/600 · Body 15/400 · Small 13/500 · Mono 12/500.
- **Movimiento**: los tokens de duración y easing viven también en `_tokens.scss`, que declara además una **regla global `prefers-reduced-motion`** neutralizando toda transición y animación del sitio; ninguna pantalla necesita repetirla.
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

> **Implementado (2026-08, bloque 11): dictado en el navegador, y SOLO en el dispositivo.**
> `POST /api/v1/assistant/transcribe` sigue **sin construir**. Lo que hay es el dictado del
> navegador (`VoiceInput.svelte`), y se implementó con una restricción que esta ADR obliga a
> añadir: el botón **solo se ofrece si el navegador reconoce en el dispositivo**
> (`SpeechRecognition.availableOnDevice` + `processLocally`), comprobado y confirmado por la
> propia API. En Chrome y Edge la Web Speech API clásica **envía el audio a un servicio de
> Google**, así que ofrecerlo por el mero hecho de que la API exista habría contradicho de
> plano la decisión de soberanía de arriba — mandando la voz de un funcionario público a un
> tercero, en un sistema que gestiona radicados reservados. Donde no hay reconocimiento local,
> **no hay botón**: la degradación honesta aplicada a la soberanía y no solo al soporte.
>
> **Construido (2026-08-17): `POST /api/v1/assistant/transcribe` + `whisper-service`.**
> El camino soberano completo ya existe y funciona en cualquier navegador: el audio va del
> navegador al gateway, de ahí al mcp-server y de ahí a `whisper-service`, que corre
> `faster-whisper` en CPU dentro de la propia instalación. No hay ningún salto a un tercero.
>
> Tres decisiones que conviene no revertir sin leer esto:
>
> * **No hay proveedor `stub`, a diferencia de los embeddings de E21.** Un vectorizador por
>   *hashing* es recuperación léxica honesta; un «stub» de transcripción tendría que
>   **inventarse lo que la persona dijo**, y ese texto acaba en el compositor del asistente o en
>   el cuerpo de un documento oficial. El default es `disabled` y su comportamiento es negarse
>   con un 503 que dice qué falta.
> * **`whisper-service` arranca desactivado** (perfil `assistant` de Compose) y **no lo enruta
>   el gateway**: es interno, solo le habla el mcp-server — mismo criterio que storage-service
>   con D-02.
> * **`faster-whisper`, no `openai-whisper`**: el segundo arrastra torch (varios GB) para el
>   mismo trabajo. Este corre en CPU, que es lo que permite a una entidad sin GPU transcribir su
>   propia voz en vez de mandarla fuera.
>
> **Rendimiento medido, no estimado** (modelo `small`, `int8`, 4 CPU): ~9 s para ~5 s de audio,
> es decir, cerca del doble del tiempo real. Sirve para dictar una frase al compositor; no para
> transcribir una reunión. El modelo se cambia por entorno (`WHISPER_MODEL`) si una entidad
> quiere más calidad y puede pagar el tiempo, o menos y más rápido.
>
> **Lo que NO está verificado**: la calidad sobre voz humana real. La prueba de extremo a
> extremo se hizo con voz sintética (`espeak-ng`), que es notoriamente difícil para cualquier
> ASR: transcribió bien la parte final y se equivocó en los nombres propios. Eso dice que la
> tubería funciona, no que la transcripción sea buena — y en dictado de números de radicado y
> nombres propios es justo donde importa. Antes de ofrecerlo como función terminada a una
> entidad conviene probarlo con personas.
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
