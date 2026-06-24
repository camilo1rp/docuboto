# ADR-011: Frontend como app de presentación SvelteKit en el monorepo

**Estado**: Propuesto
**Fecha**: 2026-06-16
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

OrpycaMCP expone hoy su funcionalidad como API REST a través del `api-gateway`, y planifica una capa MCP (E18) para agentes. Falta decidir **dónde y cómo** vivirá la interfaz web para usuarios humanos (operadores de ventanilla, gestores, administradores y, más adelante, el portal ciudadano de E13).

Las preguntas a resolver:

1. ¿El frontend es **un microservicio más** (bajo `services/`), un **repositorio aparte**, o una **app dentro del monorepo** fuera de `services/`?
2. ¿Qué **stack de herramientas** se adopta?

Restricciones y hechos relevantes del proyecto:

- La carpeta `services/` está reservada a **microservicios de dominio FastAPI** con un patrón fijo (app factory, pool asyncpg, `migrations/runner.py`, eventos Redis, schema `tenant_{slug}`). Un frontend **no tiene** dominio, BD, ni migraciones.
- El realm de Keycloak (`infra/keycloak/realm-export.json`) ya define un cliente público **`orpycamcp-frontend`** con `redirectUris` a `:3000` y `:5173` (puertos de Node/Vite) → el diseño ya anticipa un cliente web con OAuth2 + PKCE.
- El proyecto se publica bajo **AGPL-3.0**.
- Regla **Docker-only**: nada se instala en el host; build y dev server corren en contenedores.
- Existe un frontend **ya probado en producción en `sgdINTI`** (mismo autor/ecosistema) cuyo stack puede reutilizarse para no reinventar decisiones.

## Decisión

**El frontend es una aplicación de presentación que vive en una carpeta de primer nivel del monorepo (`frontend/`), fuera de `services/`, y consume exclusivamente el `api-gateway`. Su stack se alinea con el frontend de `sgdINTI`: SvelteKit (SSR con adapter-node), Vite, Bulma + Sass, y el resto de utilidades ya validadas allí.**

### Ubicación: `frontend/` en el monorepo (opción A)

- **No** bajo `services/`: el frontend no es un microservicio de dominio (sin BD, sin migraciones, sin eventos). Es capa de presentación, análoga en rol a `mcp-server` (cliente del gateway, no dueño de dato).
- **No** un repositorio aparte: un solo repo simplifica el versionado de contratos front↔API y el **cumplimiento AGPL** (una licencia, un punto de publicación). Un repo separado solo se justificaría con un equipo de frontend con cadencia de release independiente, que hoy no es el caso.

### Stack alineado con sgdINTI

| Área | Herramienta |
|---|---|
| Framework | SvelteKit 2 + Svelte 4 |
| Render | **SSR** con `@sveltejs/adapter-node` |
| Bundler / dev server | Vite 5 |
| Estilos | Bulma + Sass (SCSS) |
| Editor de texto rico | TipTap 3 (+ Yjs/Hocuspocus para colaboración) — *opcional* |
| Formularios dinámicos | SurveyJS — *opcional* (form builder para PQRSD/ventanilla) |
| Captcha portal ciudadano | altcha — *opcional* |
| Tests / calidad | Vitest, ESLint, Prettier |

Las piezas marcadas *opcional* van en `optionalDependencies`: se activan al abordar la funcionalidad que las requiere (edición colaborativa, form builder, portal). El núcleo arranca sin ellas.

### Integración con la plataforma

```
navegador → frontend (SvelteKit SSR) → api-gateway:8080 → microservicios
                         └── OAuth2 Authorization Code + PKCE → Keycloak (orpycamcp-frontend)
```

- **Autenticación**: el cliente público `orpycamcp-frontend` ya existente; el JWT porta `tenant_slug`.
- **API**: el front llama **siempre** al gateway, nunca a un microservicio directamente.
- **Multi-tenancy**: transparente para el front (resuelta por el claim del JWT / subdominio).
- **MCP / IA (E18/E21)**: se consumen como endpoints normales vía el gateway; el front no habla con la IA directamente.

### Despliegue (Docker-only)

- `frontend/Dockerfile` — build de producción multi-stage (`npm run build` → `node build`, puerto 3000).
- `frontend/Dockerfile.dev` — Vite dev server con HMR (puerto 5173).
- Al implementar: declarar el servicio `frontend` en `docker-compose.yml` (dev) y `docker-compose.prod.yml` (prod), con `depends_on: api-gateway` y `PUBLIC_API_URL`.

> Esta ADR crea únicamente el **scaffold de planificación** en `frontend/` (manifiesto de herramientas, configs base, Dockerfiles, estructura `src/` y README). La aplicación se implementa en su fase correspondiente.

## Consecuencias

**Positivas:**
- Separación de responsabilidades clara: `services/` = dominio; `frontend/` = presentación.
- Reutiliza un stack **ya probado** (sgdINTI) → menos riesgo y decisiones nuevas.
- Cumplimiento AGPL y versionado de contratos simples (monorepo único).
- Encaja sin cambios en Keycloak y en el patrón "todo pasa por el gateway".

**Negativas:**
- El monorepo mezcla dos toolchains (Python/FastAPI y Node/SvelteKit); la CI debe manejar ambos (jobs separados).
- SSR introduce un proceso Node en runtime (no solo estáticos); es coste asumido para SEO/tiempos de carga y por paridad con sgdINTI.
- Acoplar el ciclo de release del front al del backend (mitigable si en el futuro se separa en su propio repo; esta decisión no lo impide).

## Alternativas consideradas

- **Microservicio bajo `services/`**: descartada — rompe la convención (un "servicio" sin BD ni migraciones) y confunde el modelo mental.
- **Repositorio aparte**: descartada por ahora — añade fricción de versionado de contratos y duplica CI/cumplimiento AGPL sin un equipo de frontend independiente que lo justifique. Reevaluable más adelante sin coste de rediseño.
- **SPA estática (adapter-static) servida por Nginx**: válida y más simple de operar, pero se prefiere **SSR** por paridad con sgdINTI, SEO del portal ciudadano y primer render más rápido.
- **Otro framework (React/Vue/Angular)**: descartado — Svelte/SvelteKit es el stack del ecosistema del autor, con agentes de soporte (`ux-svelte-expert`, `svelte-python`) y un frontend de referencia funcionando.

## Relacionados

- [ADR-001](ADR-001-microservices-architecture.md) — arquitectura de microservicios; el frontend es presentación, no dominio.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — multi-tenancy; el front la consume vía claims del JWT.
- E18 (capa MCP) — otra capa de exposición cliente del gateway; mismo principio de no tocar BD/dato maestro.
- `frontend/README.md` — scaffold, stack detallado y estructura prevista.
