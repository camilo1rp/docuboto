# ADR-047: La instalación es un proceso y cuatro contenedores

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-001](ADR-001-microservices-architecture.md) (microservicios; **se enmienda**), [ADR-019](ADR-019-capa-mcp.md) (capa MCP; **se retira**), [ADR-033](ADR-033-entrada-de-produccion-nginx-en-contenedor.md) (entrada de producción; **se conserva con menos destino**), [ADR-048](ADR-048-identidad-emitida-por-docuboto.md), [ADR-049](ADR-049-eventos-en-postgresql-sin-redis.md), [ADR-050](ADR-050-bytes-en-disco-y-archivo-sellado.md), [ADR-051](ADR-051-ppi-reducido-vive-en-la-app.md)

---

## Contexto

El repositorio declara doce servicios con puertos propios y un gateway que enruta por prefijo, pero **ese no es el despliegue que se envía**. En los dos manifiestos el perfil por defecto arranca `app` + `worker`, y los diez servicios de dominio quedan detrás de `profiles: ["split"]`. `services/docuboto/docuboto/main.py` importa los diez conjuntos de routers y los monta en un solo `FastAPI`. Con eso, la frontera de microservicio ya era una opción de despliegue y no la topología.

Cifras del censo (2026-11-07, `feat/radicar-ingest-entry` @ `680d47a0`):

- 149.011 líneas de Python de producción en 12 paquetes; 188.165 de pruebas.
- ~10.700 líneas de duplicación estructural: `core/config.py` ×12, `core/database.py` ×9, `core/internal_auth.py` ×8, `core/clearance.py` ×7, `routers/health.py` ×12, `migrations/runner.py` ×10 (8 de ellos exactamente 186 líneas que solo difieren en una constante).
- Tres clientes del mismo `storage-service` (`document` 557, `archive` 1.366, `signature` 94 líneas) y dos proveedores OAI-PMH con 24 de 24 funciones compartidas.

El objetivo de producto es una alcaldía pequeña (categorías 4–6): 20–60 funcionarios, 10–15 concurrentes, 3.000–12.000 radicados al año, sin administrador de sistemas propio. La complejidad operativa actual —nueve contenedores en el perfil por defecto, límites que suman 13,5 CPU y 20,75 GB, y diecisiete pasos manuales para levantar una entidad— no cabe en ese cliente.

Dos hechos verificados sostienen la decisión: **no hay ninguna instalación con datos** (por eso no hay compatibilidad que preservar), y **el hardware que la documentación promete (4 núcleos / 16 GB) ya está por debajo de lo que el propio manifiesto declara** (13,5 CPU / 20,75 GB solo en el perfil por defecto).

## Decisión

### 1. Un solo proceso de aplicación

Los paquetes de dominio (`document`, `archive`, `signature`, `auth`, `tenant`, `workflow`, `notification`, `storage`, `ppi`) dejan de ser servicios con puerto propio y pasan a ser **subpaquetes de un único proceso**. Se conservan los nombres de directorio para que el diff siga siendo navegable y los ADR sigan mapeando a código, pero desaparecen:

| Pieza | Destino |
|---|---|
| `api-gateway` (2.215 líneas) | Se retira. El enrutado por prefijo (`routers/proxy.py`, 554 líneas) lo sustituye el router del propio `FastAPI`; el límite de tasa y las cabeceras de seguridad pasan a `edge` (ADR-033) y a un middleware propio. |
| `worker` (segunda imagen del mismo árbol) | Se retira. Sus bucles (`format_migration_loop`, `renovacion_lta`, cola `trabajo_fuera`) pasan a un grupo de tareas `asyncio` con semáforo acotado dentro del proceso. |
| `X-Internal-Token` (`core/internal_auth.py`, 8 copias, 500 líneas) | Se retira. Sin frontera de red entre llamadas de dominio, la autorización interna es una llamada a función; lo que queda es la comprobación de permiso en la BD. |
| Identidad firmada con nonce (`shared/identidad.py`, 680 líneas; `IdentidadFirmadaMiddleware`) | Se retira. Ya no está montada en ningún servicio y un guardián (`shared/tests/test_la_identidad_propagada_va_firmada.py:109`) **falla el build** si alguien la vuelve a montar. |
| `core/conservacion.py` (3 copias, 311 líneas) | Una sola función en el paquete de dominio compartido. |
| `core/config.py` ×12 | Uno: `pydantic-settings`, un `Settings` por familia con prefijo. |
| `routers/health.py` ×12, `routers/metrics.py` ×7 | Uno cada uno. |

La identidad del llamante se resuelve una vez por request en un middleware único (`UsuarioActualMiddleware`, que ya existe en `shared/orpycamcp_common/usuario.py`) contra `auth_users`, y viaja en `request.state`. Ningún dominio vuelve a resolverla.

### 2. Cuatro contenedores, no nueve

| Contenedor | Contenido |
|---|---|
| `app` | El proceso único. FastAPI + bucles de fondo + consumidores de eventos. |
| `postgres` | Esquema `tenant_{slug}`, `public`, cola de trabajo y eventos (ADR-049). |
| `edge` | nginx: TLS, límite de tasa, estáticos, proxy a `app` y `frontend`. |
| `frontend` | SvelteKit SSR con un único proxy BFF (ADR-056). |

Fuera del manifiesto: `keycloak` (ADR-048), `redis` (ADR-049), `minio`/RustFS local y `worker` (ADR-050), `mcp-server` (ADR-051).

Objetivo medible: **cuatro contenedores, menos de 2 GB en reposo, `docker compose up -d` hasta sistema operativo en menos de cinco minutos.**

### 3. Una instalación por alcaldía, con esquema por entidad

Se despliega una instalación por entidad y se **conserva** el esquema `tenant_{slug}` con `search_path` por conexión (ADR-002). Con una sola entidad eso cuesta una sentencia por conexión y nada más; colapsar a `public` tocaría todos los repositorios para no ganar nada visible y cerraría para siempre la opción de alojar varias entidades. Es la opcionalidad más barata del rediseño.

Lo que sí se retira del multi-tenant es lo que solo tenía sentido con varias entidades vivas a la vez: el registro de entidades con estados de ciclo de vida, el veto de la entidad suspendida en el borde, el bucket por entidad y el aprovisionamiento de clientes de Keycloak. `public.tenants` queda como una fila con la configuración de la entidad.

### 4. Se conserva la separación por capas dentro del proceso

`routers → services → repositories → SQL`, con `asyncpg` y SQL a mano (ADR-003), sin cambios. La fusión no relaja ninguna regla: sigue habiendo una sola capa que habla con la base de datos, y las pruebas de arquitectura siguen leyendo el árbol.

## Alternativas consideradas

- **Quedarse con los diez servicios y el gateway.** Descartado: ya no es lo que se despliega; mantener dos topologías vivas obliga a probar ambas y a mantener la identidad firmada, los tokens internos y nueve imágenes que nadie arranca.
- **Consolidar en dos contenedores (`app` + `worker`).** Descartado: el `worker` existe para llevar `openjdk-21-jre-headless` y `ghostscript` al runtime por veraPDF, y su carga es casi nula con `preservation_enabled=False` y `FORMAT_MIGRATION_ENABLED=False` por defecto. Un segundo contenedor para una cola que cabe en un grupo de tareas no se paga.
- **Reescribir desde cero.** Descartado: se perdería el activo caro de reconstruir. El dominio de archivo (TRD/TVD convalidada y versionada, transferencias con acta congelada y fixity, eliminación con separación de funciones y lápida, índice electrónico v4 con XSD validado en runtime) está construido y probado; el rediseño toca la plomería, no ese núcleo.

## Consecuencias

- **Menos superficie operativa.** Cuatro contenedores en lugar de nueve, una imagen de aplicación en lugar de diez, un `docker compose` en lugar de un perfil.
- **El despliegue deja de tener dos formas.** No hay perfil `split` que probar ni divergencia entre lo que se prueba y lo que se envía.
- **El escalado horizontal desaparece como opción inmediata.** Con un proceso no se puede replicar solo el dominio que va lento. Al volumen del cliente objetivo (≈12.000 radicados al año) no aplica; si algún día aplica, se parte de una base sin tokens internos que inventar.
- **Un fallo de un dominio afecta a todos.** Se mitiga con los bucles supervisados que ya existen (`spawn_supervised`) y con un proceso que reinicia `restart: always`.
- **Hay que retirar 484 decoradores de ruta de su `main.py` de servicio.** Es trabajo mecánico y el guardián de gates (`shared/tests/test_toda_ruta_lleva_gate.py`) dice cuáles quedan sin permiso.

## Orden de ejecución

Este ADR es el cimiento; el resto se ejecuta en este orden porque cada paso desbloquea el siguiente:

1. **Esquema legible y lanzamientos** (ADR-053). Sin datos que migrar, se puede regenerar el esquema antes de tocar código.
2. **PPI a lo construido y firma correcta** (ADR-051, ADR-054). Es lo único que hoy no funciona en la topología que se envía.
3. **Fusión en un proceso** (este ADR).
4. **Identidad propia** (ADR-048), que quita un contenedor y le da a PPI el emisor OIDC que necesita.
5. **Eventos en PostgreSQL** (ADR-049), que quita otro.
6. **Bytes en disco y archivo sellado** (ADR-050), que quita el almacén local.
7. **Un solo proxy BFF** (ADR-056).
8. **Sustitución por librerías** (ADR-052), que es trabajo de detalle y puede hacerse por piezas.
9. **Brechas de conformidad** (ADR-055).

## Residuos declarados

No hay instalaciones previas con datos: nada obliga a conservar una tabla, una variable o un servicio por compatibilidad. Las piezas que se retiran no necesitan nota de migración.
