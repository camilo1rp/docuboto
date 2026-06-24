# ADR-019: Capa MCP como fachada fina cliente del gateway

**Estado**: Aceptado
**Fecha**: 2026-06-22
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

La constitución (§1) define dos capas de consumo: la **API REST** versionada (fuente de verdad) y un **servidor MCP** que la expone como *tools* a agentes/LLM. E18 (fase final) añade esa segunda capa: un asistente debe poder radicar, buscar, consultar expedientes, leer el índice, listar la TRD o generar un FUID **sin saltarse ninguna garantía** (autenticación Keycloak, aislamiento multi-tenant, RBAC, auditoría).

La pregunta es **dónde y cómo** vive esa capa sin duplicar lógica ni romper el respeto de capas.

## Decisión

**El `mcp-server` es un microservicio nuevo, cliente HTTP del api-gateway, que expone un catálogo de *tools* mapeadas 1:1 a endpoints REST estables. No tiene lógica de negocio ni accede a BD/MinIO/Redis.**

- **Cliente del gateway, no de la BD**: cada tool invoca un endpoint `/api/v1/…` del api-gateway, propagando el token Keycloak del usuario y el `X-Tenant-Slug`. Así hereda gratis la autenticación, el RBAC, el aislamiento y la auditoría — no los reimplementa.
- **Catálogo de tools declarativo**: cada tool se define como `(nombre, descripción, método, ruta, permiso requerido, ¿escritura?)`. El dispatcher genérico traduce la invocación a una llamada HTTP al gateway. Añadir una tool es añadir una entrada, no código nuevo.
- **Scope por permisos y gate de escritura**: el catálogo visible se filtra por los `permissions[]`/`roles[]` del usuario; las tools de escritura/destructivas se marcan y requieren gate explícito. El MCP nunca ofrece lo que la API no autorizaría.
- **Sin lógica de dominio**: si falta un endpoint, se construye en su épica de dominio, no en el MCP. El MCP solo compone contratos existentes.
- **Transporte**: el binding del **SDK MCP oficial** (stdio + HTTP streamable, `initialize`/capabilities) envuelve este núcleo. El **catálogo + dispatcher + scope** es independiente del transporte y es lo que se entrega primero; el binding del protocolo es una capa fina encima.

## Consecuencias

**Positivas:**
- **Cero duplicación y cero deuda de seguridad**: al pasar por el gateway, el MCP no puede saltarse RBAC, tenant ni auditoría aunque quisiera.
- **Mantenible**: el catálogo declarativo desacopla "qué tools existen" de "cómo se llaman"; evoluciona con la API sin reescrituras.
- **Respeto de capas** (constitución): prohibido el acceso directo a datos desde el MCP; es un consumidor más de la API, como cualquier cliente externo.
- El núcleo (catálogo/dispatcher/scope) es **testable sin un cliente MCP real** (se prueba como cliente HTTP del gateway).

**Negativas / límites:**
- Un salto de red extra (MCP → gateway → servicio) por llamada; aceptable para una capa de asistencia, no de alto rendimiento.
- El binding del SDK MCP (stdio/HTTP-streamable, sesiones, refresh de token) es trabajo adicional sobre el núcleo; se entrega después del catálogo.
- El MCP queda acoplado a la **estabilidad** de la API REST: por eso E18 es la fase final (E01..E17 consolidadas primero).

## Alternativas consideradas

- **MCP accediendo directamente a la BD/servicios**: descartado; viola el respeto de capas, duplica RBAC/tenant/auditoría y acopla el MCP al esquema interno.
- **Tools como código imperativo por endpoint**: descartado frente al **catálogo declarativo**; este reduce el coste de añadir/auditar tools y centraliza el scope de permisos.
- **Integrar MCP dentro del api-gateway**: descartado; mezcla responsabilidades (proxy/seguridad vs. protocolo de agentes) y dificulta el transporte stdio. Servicio separado, puerto propio.

## Relacionados

- [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) — el MCP propaga el token; la autorización la resuelve la BD vía gateway.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — el `tenant_slug` del token se propaga como `X-Tenant-Slug`.
- [ADR-006](ADR-006-proveedor-ia-configurable.md) — el modelo/LLM es del cliente externo; el MCP solo provee tools (no aloja inferencia).
