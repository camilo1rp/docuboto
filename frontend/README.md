# Frontend — OrpycaMCP

> **Estado: PLANIFICADO (scaffold).** Esta carpeta fija el stack y la estructura del frontend; **aún no hay aplicación implementada**. Las dependencias **no** se instalan en el host: el build y el dev server corren siempre en Docker (regla *Docker-only* del proyecto). Decisión registrada en [ADR-011](../docs/es/adr/ADR-011-frontend-sveltekit-monorepo.md).

## Qué es

Capa de **presentación** de OrpycaMCP. **No es un microservicio de dominio**: no tiene base de datos, ni migraciones, ni publica eventos. Es un **cliente del `api-gateway`** (igual que `mcp-server` lo es). Por eso vive en `frontend/` y **no** bajo `services/`.

```
navegador → frontend (SvelteKit SSR) → api-gateway:8080 → microservicios
                         └── OAuth2 + PKCE contra Keycloak (cliente orpyca-frontend)
```

## Stack (alineado con sgdINTI)

Las herramientas replican el frontend de `sgdINTI`, que ya está probado en producción:

| Área | Herramienta |
|---|---|
| Framework | **SvelteKit 2** + **Svelte 4** |
| Render | **SSR** con `@sveltejs/adapter-node` (servidor Node sirve HTML) |
| Bundler / dev | **Vite 5** (HMR) |
| Estilos | **Bulma** + **Sass (SCSS)** |
| Iconos | FontAwesome Free |
| Editor de texto rico | **TipTap 3** (*opcional*; colaboración con **Yjs** + **Hocuspocus**) |
| Formularios dinámicos | **SurveyJS** (*opcional*; *form builder* — útil para PQRSD/ventanilla) |
| Drag & drop | `svelte-dnd-action` |
| Captcha (portal ciudadano) | `altcha` (*opcional*) |
| Markdown | `marked` |
| Tests | **Vitest** |
| Calidad | **ESLint** + **Prettier** |

> TipTap/Yjs/Hocuspocus, SurveyJS y altcha están en `optionalDependencies`: se habilitan cuando se aborden las funcionalidades que los necesitan (edición colaborativa, *form builder*, portal ciudadano). El núcleo arranca sin ellos.

## Estructura prevista (igual que sgdINTI)

```
frontend/
├── src/
│   ├── lib/
│   │   ├── components/   # componentes Svelte reutilizables
│   │   ├── services/     # clientes HTTP del api-gateway (fetch + JWT)
│   │   ├── stores/       # estado (sesión, tenant, usuario)
│   │   └── utils/        # helpers
│   ├── routes/
│   │   ├── (app)/        # rutas autenticadas (bandejas, radicación, expedientes)
│   │   ├── (public)/     # rutas públicas (login, consulta por código de verificación)
│   │   └── 403/          # acceso denegado
│   └── styles/           # SCSS + tema Bulma
├── static/               # favicon, assets estáticos
├── svelte.config.js      # adapter-node (SSR)
├── vite.config.js
├── Dockerfile            # build de producción (multi-stage)
└── Dockerfile.dev        # dev server Vite (:5173)
```

## Integración con la plataforma

- **Autenticación:** OAuth2 Authorization Code + **PKCE** contra Keycloak. El cliente público **`orpyca-frontend`** ya existe en `infra/keycloak/realm-export.json` (redirectUris `:3000` y `:5173`).
- **API:** todas las llamadas pasan por el **`api-gateway`** (nunca directo a un microservicio). El JWT lleva `tenant_slug` → multi-tenancy transparente para el front.
- **Multi-tenancy:** el front es agnóstico; el tenant se resuelve por el claim del JWT (o subdominio en despliegue).
- **MCP / IA:** las capacidades asistidas (E18/E21) se consumen vía el gateway como cualquier otro endpoint; el front no habla con la IA directamente.

## Build y ejecución (siempre en Docker)

```bash
# Desarrollo (HMR en :5173) — se añadirá un servicio "frontend" a docker-compose.yml
docker compose up -d --build frontend

# Producción: la imagen multi-stage compila y sirve con `node build` en :3000
docker build -f frontend/Dockerfile -t orpyca-frontend frontend/
```

> Pendiente al iniciar la implementación: declarar el servicio `frontend` en `docker-compose.yml` (`Dockerfile.dev`, volúmenes para HMR, `depends_on: api-gateway`, var `PUBLIC_API_URL`) y el bloque de producción en `docker-compose.prod.yml`.

## Convenciones

- Código y comentarios en inglés; términos de dominio en español (`radicado`, `expediente`).
- Licencia **AGPL-3.0-or-later** (igual que el backend; conviven en el monorepo).
- Accesibilidad y SCSS según las guías del agente `ux-svelte-expert`.
