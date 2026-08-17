# Integraciones externas (notas de operación)

Varias capacidades de OrpycaMCP están implementadas con un **proveedor enchufable** y un **stub funcional por defecto**, de modo que el sistema opera de extremo a extremo sin dependencias externas pesadas. Esta guía indica, para cada una, **dónde conectar el componente real** cuando se disponga del entorno (certificados, almacenamiento WORM, librerías, modelos), qué **contrato permanece estable** y los **pasos** de activación.

Principio común: el contrato (firmas de función / endpoints REST) **no cambia** al sustituir el stub por el proveedor real. Solo se implementa el adaptador y se ajusta la configuración.

---

## 1. Firma digital cualificada (PKI / XAdES) — `signature-service`

| | |
|---|---|
| Estado actual | Firma **electrónica** nativa (SHA-256 + identidad + sello). Detecta alteración; **no** es firma digital cualificada. |
| Punto de swap | `services/signature-service/app/services/signer.py` (funciones `firmar` / `verificar`). |
| Selector | `SIGNER_PROVIDER` (config; `nativa` por defecto). |
| Norma | Decreto 2364/2012 (electrónica) → certificada para trámites que exijan firma **digital**. |

**Para activar PKI:**
1. Añadir una librería de firma (p. ej. `pyhanko`/`endesive` + `cryptography`) a `services/signature-service/requirements.txt`.
2. Implementar un proveedor `xades` en `signer.py` con la misma interfaz: `firmar(payload, firmante) -> {payload_sha256, firma_blob, provider}` y `verificar(...)`, usando certificado **X.509**, sello de tiempo **TSA (RFC 3161)** y, si aplica, validación **CRL/OCSP**.
3. Provisionar el certificado y el endpoint TSA (variables de entorno/secretos), seleccionarlo con `SIGNER_PROVIDER=xades`.
4. La firma del índice al cierre (`archive → signature-service /sign-xml`) y `/verify` ya consumen este contrato: pasan a firma digital sin cambios en archive.

---

## 2. WORM / inmutabilidad (MinIO Object Lock) — `storage-service`

| | |
|---|---|
| Estado actual | Plan de preservación + eventos PREMIS + AIP/BagIt. El evento `WORM` se registra, pero **no** hay Object Lock real. |
| Punto de swap | `storage-service` (cliente MinIO + bucket de preservación; al persistir el AIP). |
| Norma | Acuerdo AGN 001/2024 Art. 4.3.2.6; Decreto 2609/2012 Art. 26. |

**Para activar WORM:**
1. Crear un **bucket de preservación** con *versioning* y **Object Lock** habilitado (modo *compliance*).
2. Al empaquetar el AIP (`POST /api/v1/preservacion/aip`), subir el objeto con `retention until` derivado de la **TRD** (E04, `archivo_central_years`) y, si aplica, *legal hold*.
3. Registrar el evento `WORM` (ya soportado) con la fecha de retención aplicada.
4. Requiere MinIO/S3 con Object Lock; el cálculo de retención ya existe (E04 `GET /trd/{id}/retention`).

---

## 3. Validación PDF/A (veraPDF) — `storage-service`

| | |
|---|---|
| Estado actual | E07 entrega PDF/A *best-effort* sin validar. La preservación no valida conformidad. |
| Punto de swap | `storage-service` (paso de ingesta/normalización de preservación). |
| Norma | Decreto 2609/2012 Art. 32 (estándares abiertos); ISO 19005 (PDF/A). |

**Para activar veraPDF:**
1. Disponer de **veraPDF** (CLI Java) o un microservicio validador accesible.
2. En la normalización a PDF/A-1b/2b, invocar veraPDF y registrar el resultado como evento `VALIDACION_PDFA` (`ok`/`fallo`) en preservación (ya soportado).
3. Rechazar/marcar los objetos no conformes antes de empaquetar el AIP.

---

## 4. Capa MCP — servidor MCP — `mcp-server`

| | |
|---|---|
| Estado actual | **Servidor MCP completo** (SDK oficial `mcp`) por **stdio** y **HTTP streamable**: expone **tools** (catálogo), **resources** (`orfeo://…`) y **prompts**, con **OAuth por sesión** (obtiene/refresca el token Keycloak vía auth-service). Refinamiento abierto: sincronización proactiva del refresh. |
| Implementación | `services/mcp-server/app/mcp_app.py` (tools) + `app/mcp_resources.py` (resources+prompts) + `app/mcp_stdio.py` (entrypoint stdio) + endpoint **`/mcp`** en la app FastAPI (`app/main.py`, `StreamableHTTPSessionManager`). El núcleo `catalog.py` + `dispatcher.py` se reutiliza intacto. |
| Norma/decisión | Constitución §1; ADR-019. |

**Transporte stdio (cliente local tipo escritorio):** el cliente lanza el proceso y habla por stdio; la sesión se configura por entorno. Para el **token** hay tres modos (precedencia de arriba abajo):

```bash
GATEWAY_URL=http://localhost:19080 \
MCP_TENANT_SLUG=icetex \
# (a) token estático (sin refresco):   MCP_BEARER_TOKEN=<jwt>
# (b) refresh por sesión:              MCP_REFRESH_TOKEN=<refresh-token>
# (c) ROPC (usuario/contraseña):       MCP_AUTH_USERNAME=ana MCP_AUTH_PASSWORD=...
python -m app.mcp_stdio
```

Con (b)/(c) el mcp-server **obtiene y refresca** el access token Keycloak vía `auth-service` (`/api/v1/auth/token` y `/api/v1/auth/refresh`, públicos en el gateway) y lo cachea hasta su expiración.

**Transporte HTTP streamable (clientes remotos):** **`POST https://<host>/api/v1/mcp/stream`** a través del api-gateway, que valida el JWT e inyecta `X-Tenant-Slug`. (`POST http://mcp-server:8009/mcp` sigue existiendo para acceso dentro de la red Docker.) El contexto de sesión llega por dos cabeceras y nada más: `Authorization: Bearer <jwt>` y `X-Tenant-Slug`.

> **`X-User-Permissions` y `X-User-Roles` ya no se leen, y `MCP_USER_PERMISSIONS`/`MCP_USER_ROLES` se han retirado.** La primera no la producía ningún componente —el gateway la descarta por forjable—, así que llegaba siempre vacía y toda tool con permiso quedaba invisible por este transporte. Las variables de entorno eran peores: sí se podían poner, de modo que quien las configuraba creía estar concediendo algo, y lo único que conseguía era ver ofrecidas tools que el backend le negaría al usarlas. **Los permisos se resuelven contra `GET /api/v1/auth/me` con el token del llamante**, una sola resolución para los tres transportes.

En ambos casos el servidor propaga `Authorization` + `X-Tenant-Slug` al gateway (que resuelve autenticación/RBAC/tenant) y filtra las tools visibles por permiso **y nivel CRUD** (ROOT ve todas). Las operaciones de escritura **no dependen de la aprobación del cliente**: invocarlas devuelve una **propuesta firmada** que no ejecuta nada, y ejecutarla exige presentar ese token (`confirmar_accion`, o `POST /api/v1/mcp/proposals/execute`). La aprobación del cliente MCP y las `annotations` son fricción añadida, no la garantía.

**El marco de alcance** se ofrece por dos vías equivalentes: la tool **`mi_alcance`** y el resource **`orfeo://alcance`**. Devuelven lo que la persona puede ejecutar, lo que puede consultar, y lo que **no** puede con su motivo y su **vía alternativa** — que es lo que evita que un límite se descubra fallando.

**Resources** (`orfeo://normativa`, `orfeo://alcance`, plantillas `orfeo://radicado/{id}`, `orfeo://expediente/{id}`, `orfeo://expediente/{id}/indice`) y **prompts** (`radicar_pqrsd`, `resumen_expediente`, `buscar_antecedentes`) están disponibles en ambos transportes.

La capa MCP queda así **completa** (tools + resources + prompts + ambos transportes + OAuth por sesión).

---

## 5. Capa de conocimiento — embeddings/LLM reales — `knowledge-service`

| | |
|---|---|
| Estado actual | Embedding **stub** determinista (hash→vector dim 64). pgvector + recuperación con pre-filtrado ACL ya funcionan. |
| Punto de swap | `services/knowledge-service/app/services/embeddings.py` (`embed`). |
| Selector | `EMBEDDING_PROVIDER` / `EMBEDDING_DIM` (config). |
| Decisión | ADR-006 (proveedor de IA configurable, local por defecto). |

**Para activar embeddings/RAG reales:**
1. Conectar un modelo de embeddings (p. ej. `sentence-transformers` local o una API) implementando `embed(text) -> list[float]`; ajustar `EMBEDDING_DIM` y la columna `vector(N)` (migración) a la dimensión del modelo.
2. Para RAG anclado con citas y sugerencias TRD/dependencia (advisorias), conectar un LLM **pluggable** (ADR-006); mantener el **pre-filtrado ACL** (`acl @>`) antes de recuperar.
3. Es una capa **advisoria/derivada y opt-in por tenant**: no es fuente de verdad.

---

## 6. Operador postal real (4-72 / Servientrega) — `document-service`

| | |
|---|---|
| Estado actual | Proveedor **stub** (guía determinista, sin API real). |
| Punto de swap | `services/document-service/app/services/postal_provider.py` (`generar_guia`). |

**Para activar el operador real:** implementar `generar_guia(operador, radicado_id)` (y, si aplica, la consulta de estado) contra la API del operador con sus credenciales; el estado de entrega ya se actualiza vía `POST /api/v1/envios/{id}/estado` (callback/sondeo).

---

## 7. Migración desde Orfeo — **fuera del alcance de este repositorio**

**Decisión de alcance: este código no incorpora, ni incorporará, herramientas de
migración de datos desde instalaciones de Orfeo anteriores.** Ese trabajo se hace
externamente, por fuera del repositorio.

### Por qué

Cada entidad configuró su Orfeo a su manera: formatos de radicado de 14 o 15
dígitos, metadatos en tablas dinámicas, esquemas de contraseñas propios, motores
de base de datos distintos. Un extractor genérico no existe; lo que existe es un
trabajo de mapeo por instalación. Meterlo aquí significaría mantener en el núcleo
un conjunto de casos particulares que no le sirven a nadie más, y que envejecen
con cada cliente.

### Qué SÍ ofrece este repositorio, y es suficiente

La vía de entrada es el **importador interoperable**, que es genérico y está
documentado:

- `POST /api/v1/import` en `document-service` — recibe un paquete ZIP con
  `manifiesto.json`, `esquema/radicado.schema.json` (JSON Schema draft 2020-12
  **publicado dentro del propio paquete**), los radicados en JSON y sus anexos.
- Validación **todo o nada**: si un esquema o un SHA-256 no cuadra, no queda
  estado parcial en la base.
- **Identidad preservada**: el número de radicado original no se regenera
  (Acuerdo AGN 060/2001, Ley 594/2000 art. 19). Una colisión se omite, nunca se
  sobreescribe.
- Idempotente por número, lo que permite cargas incrementales repetidas.

Es decir: el contrato de entrada es estable y público. **Lo que se construye
fuera es el traductor** desde el sistema de origen hacia ese formato.

### Consecuencias prácticas

- No se aceptan en este repositorio contribuciones de ETL específico de Orfeo,
  adaptadores por cliente ni scripts de extracción de bases legadas.
- El material de análisis de instalaciones reales vive en el repositorio privado
  `documentos/planesMigracion/`, y es **referencia de reglas de negocio**, no
  código de migración.
- Si al migrar aparece una limitación del **importador genérico** —por ejemplo el
  formato del número de radicado admitido, o la reconciliación de la secuencia de
  numeración tras una carga— eso sí es trabajo de este repositorio, porque afecta
  al contrato de entrada y beneficia a cualquier origen, no solo a Orfeo.

## Resumen

| Capacidad | Servicio | Swap | Necesita |
|---|---|---|---|
| Firma digital PKI | signature-service | `signer.py` | cert X.509 + TSA + lib |
| WORM (Object Lock) | storage-service | cliente MinIO | MinIO con Object Lock |
| PDF/A | storage-service | normalización | veraPDF |
| Protocolo MCP (tools+resources+prompts, stdio+HTTP, OAuth) ✅ | mcp-server | hecho (`mcp_app.py`+`mcp_resources.py`+`oauth.py`+`/mcp`) | — |
| Embeddings/RAG | knowledge-service | `embeddings.py` | modelo/LLM |
| Operador postal | document-service | `postal_provider.py` | credenciales API |
| Migración desde Orfeo | — | **fuera de alcance** | traductor externo hacia `POST /api/v1/import` |

Todas las integraciones conservan su contrato; activar cada una es implementar el adaptador y proveer la dependencia/credencial — **sin cambios en la lógica de dominio**.

La migración es la excepción y por eso figura sin servicio: no es un adaptador que se enchufe aquí, sino trabajo que se hace fuera contra un contrato de entrada estable (§7).
