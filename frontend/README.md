# Frontend — OrpycaMCP

> **Estado: E22 ✅ núcleo implementado.** SvelteKit ~completo: login PKCE, 14+ vistas
> funcionales, Form Builder, asistente conversacional. Las dependencias **no** se
> instalan en el host: el build y el dev server corren siempre en Docker (regla
> _Docker-only_ del proyecto). Decisión registrada en
> [ADR-011](../docs/es/adr/ADR-011-frontend-sveltekit-monorepo.md) y
> [ADR-020](../docs/es/adr/ADR-020-sistema-diseno-orpyca-asistente.md).

## Qué es

Capa de **presentación** de OrpycaMCP. **No es un microservicio de dominio**: no tiene
base de datos, ni migraciones, ni publica eventos. Es un **cliente del `api-gateway`**
(igual que `mcp-server` lo es). Por eso vive en `frontend/` y **no** bajo `services/`.

```
navegador → frontend (SvelteKit SSR, :19300) → api-gateway (:19080) → microservicios
                         └── OAuth2 + PKCE contra Keycloak (cliente orpyca-frontend)
```

**Patrón BFF (Backend-for-Frontend)**: cada ruta autenticada bajo `src/routes/(app)/`
trae su propia subcarpeta `api/` con endpoints server-side (`+server.js`) que reciben
la petición del navegador, inyectan el `Authorization: Bearer` desde la cookie httpOnly
del servidor, y reenvían al `api-gateway` usando la URL **interna** de Docker
(`INTERNAL_API_URL`, nunca la pública). El navegador **nunca** ve el JWT ni llama
directo al gateway — ver regla D-05 en `frontend/CLAUDE.md`.

## Stack (alineado con sgdINTI)

| Área                       | Herramienta                                                            |
| -------------------------- | ---------------------------------------------------------------------- |
| Framework                  | **SvelteKit 2** + **Svelte 4**                                         |
| Render                     | **SSR** con `@sveltejs/adapter-node` (servidor Node sirve HTML)        |
| Bundler / dev              | **Vite 5** (HMR)                                                       |
| Estilos                    | **Bulma** + **Sass (SCSS)**, tokens de diseño Orpyca `--op-*`          |
| Iconos                     | FontAwesome Free                                                       |
| Editor de texto rico       | **TipTap 3** (saneado server-side, ver `frontend/CLAUDE.md`)           |
| Formularios dinámicos      | **SurveyJS** (_form builder_, plantillas administrables)               |
| Drag & drop                | `svelte-dnd-action`                                                    |
| Markdown                   | `marked`                                                               |
| Tests                      | **Vitest** (unitarios) + **Playwright** (`frontend/e2e/`)              |
| Calidad                    | **ESLint** + **Prettier**                                              |

## Estructura real

```
frontend/
├── src/
│   ├── lib/
│   │   ├── components/
│   │   │   ├── editor/    # TipTap + saneado HTML
│   │   │   ├── layout/    # navegación, sidebar (espejo RBAC)
│   │   │   └── ui/        # DataTable, Drawer, Modal, StatusChip, FormField, Button, Loader, Stepper
│   │   ├── server/        # utilidades server-only (sanitizeHtml, config.server.js)
│   │   ├── services/      # clientes HTTP hacia el api-gateway
│   │   ├── stores/        # estado (sesión, tenant, usuario)
│   │   ├── types/         # tipos/JSDoc compartidos
│   │   └── utils/         # helpers
│   ├── routes/
│   │   ├── (app)/         # rutas autenticadas — cada una con su propia api/ (BFF)
│   │   ├── (public)/      # login, login/callback, logout
│   │   ├── 403/           # acceso denegado
│   │   └── _kit/          # showcase de componentes UI (solo dev)
│   └── hooks.server.js    # sesión SSR, guard de autenticación
├── e2e/                   # pruebas Playwright
├── static/                # favicon, assets estáticos
├── svelte.config.js       # adapter-node (SSR)
├── vite.config.js
├── Dockerfile             # build de producción (multi-stage)
└── Dockerfile.dev         # dev server Vite
```

## Pantallas implementadas

Ver el detalle de propósito, rol y endpoints de cada vista en
[Pantallas del sistema](../docs/es/pantallas.md). Resumen: `/dashboard`, `/bandeja`,
`/radicar`, `/borradores`, `/busqueda`, `/expedientes` (+ detalle), `/archivo-fisico`,
`/transferencias`, `/firmas`, `/envios`, `/reportes`, `/asistente`,
`/admin/plantillas`, más el flujo de autenticación (`/login`, `/login/callback`,
`/logout`) y `/403`.

## Integración con la plataforma

- **Autenticación:** OAuth2 Authorization Code + **PKCE** contra Keycloak. El cliente
  público **`orpyca-frontend`** existe en `infra/keycloak/realm-export.json`
  (redirectUris `:19300` en Docker y `:5173` en dev local con Vite). El `access_token`
  es **httpOnly y server-only** — nunca se expone al JS del cliente (D-05).
- **API:** todas las llamadas pasan por el **`api-gateway`** (nunca directo a un
  microservicio) a través del patrón BFF descrito arriba.
- **Multi-tenancy:** el front es agnóstico; el tenant se resuelve por el claim
  `tenant_slug` del JWT.
- **MCP / IA:** el asistente conversacional (E18/E21) se consume vía `/api/v1/assistant`
  a través del gateway; el front no habla con el LLM ni con `mcp-server` directamente.
- **RBAC espejo:** la navegación y las acciones visibles reflejan los permisos reales
  (`PERM_RADI`, `USUA_PERM_EXPEDIENTE`, `USUA_PERM_ADMIN`, `PERM_RADI_SALIDA`, …), pero
  el backend siempre re-valida — la UI es defensa en profundidad, no el control real.

## Build y ejecución (siempre en Docker)

```bash
# Levantar junto al resto de la plataforma (puerto publicado 19300)
docker compose up -d --build frontend

# Solo rebuild del frontend tras un cambio
docker compose up -d --build frontend

# Ver logs
docker compose logs -f frontend
```

```bash
# Verificación (dentro del contenedor o con Node local si se prefiere)
cd frontend && npm run check   # svelte-check
npx vitest run                  # tests unitarios
```

## Convenciones

- Código y comentarios en inglés; términos de dominio en español (`radicado`, `expediente`).
- Licencia **AGPL-3.0-or-later** (igual que el backend; conviven en el monorepo).
- Reglas detalladas de seguridad, accesibilidad y convenciones de proxy en
  `frontend/CLAUDE.md` (regla D-05, allowlist de payload, saneado HTML, tokens `--op-*`).
