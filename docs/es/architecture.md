# Arquitectura / Architecture

## Visión general / Overview

OrpycaMCP usa una arquitectura de microservicios donde cada servicio es responsable de un bounded context del dominio documental.

```
                    ┌─────────────────┐
   Cliente/Postman  │   api-gateway   │  :8080
   ──────────────► │  (proxy + auth)  │
                    └────────┬────────┘
                             │ JWT validado
              ┌──────────────┼──────────────┐
              │              │              │
         ┌────┴────┐   ┌─────┴────┐  ┌─────┴──────┐
         │  auth   │   │  tenant  │  │  document  │
         │ :8001   │   │  :8002   │  │   :8003    │
         └─────────┘   └──────────┘  └─────┬──────┘
                                           │
              ┌────────────────────────────┼────────┐
              │                            │        │
         ┌────┴────┐   ┌──────────┐  ┌────┴────┐
         │ archive │   │ storage  │  │workflow │
         │  :8004  │   │  :8005   │  │  :8006  │
         └─────────┘   └──────────┘  └─────────┘

 Infraestructura:
 ┌──────────┐  ┌───────┐  ┌──────────┐  ┌───────┐
 │PostgreSQL│  │ MinIO │  │Keycloak  │  │ Redis │
 │  :5432   │  │ :9000 │  │  :8180   │  │ :6379 │
 └──────────┘  └───────┘  └──────────┘  └───────┘
```

## Flujo de autenticación

```
1. Usuario → POST /auth/token (credenciales)
2. auth-service → Keycloak (valida)
3. Keycloak → JWT con claims: {tenant_slug, user_id, roles[]}
4. Cliente → requests con header: Authorization: Bearer <JWT>
5. api-gateway → valida JWT en cada request
6. api-gateway → reenvía a servicio destino con claims
7. Servicio → establece search_path = tenant_{slug}
```

## Comunicación entre servicios

**Sincrónica (HTTP):** para operaciones de usuario que necesitan respuesta inmediata.

**Asincrónica (Redis Streams):** para eventos de dominio.

```
orpycamcp.document.events  → workflow-service (asigna flujo)
                          → notification-service (alerta)
orpycamcp.workflow.events  → notification-service (notifica)
```

Stream pattern: `orpycamcp.{servicio}.events`

## Multi-tenancy: Schema isolation

```sql
-- Una sola base de datos, múltiples schemas
public.tenants         -- registro de instituciones
tenant_demo.*          -- datos de institución "demo"  
tenant_icetex.*        -- datos de ICETEX
tenant_mi_entidad.*    -- datos de cualquier institución nueva
```

En cada request, el middleware cambia el `search_path` según el claim `tenant_slug` del JWT.

<!-- TODO: translate to English -->
