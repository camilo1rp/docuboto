# Integraciones externas (notas de operación)

Varias capacidades de OrpycaMCP están implementadas con un **proveedor enchufable** y un **stub funcional por defecto**, de modo que el sistema opera de extremo a extremo sin dependencias externas pesadas. Esta guía indica, para cada una, **dónde conectar el componente real** cuando se disponga del entorno (certificados, almacenamiento WORM, librerías, modelos), qué **contrato permanece estable** y los **pasos** de activación.

Principio común: el contrato (firmas de función / endpoints REST) **no cambia** al sustituir el stub por el proveedor real. Solo se implementa el adaptador y se ajusta la configuración.

---

## 1. Firma digital cualificada (PKI / XAdES) — `signature-service`

| | |
|---|---|
| Estado actual | Firma **electrónica** nativa (SHA-256 + identidad + sello). Detecta alteración; **no** es firma digital cualificada. |
| Punto de swap | `services/signature-service/signature/services/signer.py` (funciones `firmar` / `verificar`). |
| Selector | `SIGNER_PROVIDER` (config; `nativa` por defecto). |
| Norma | Decreto 2364/2012 (electrónica) → certificada para trámites que exijan firma **digital**. |

**Para activar PKI:**
1. Añadir una librería de firma (p. ej. `pyhanko`/`endesive` + `cryptography`) a `services/signature-service/requirements.txt`.
2. Implementar un proveedor `xades` en `signer.py` con la misma interfaz: `firmar(payload, firmante) -> {payload_sha256, firma_blob, provider}` y `verificar(...)`, usando certificado **X.509**, sello de tiempo **TSA (RFC 3161)** y, si aplica, validación **CRL/OCSP**.
3. Provisionar el certificado y el endpoint TSA (variables de entorno/secretos), seleccionarlo con `SIGNER_PROVIDER=xades`.
4. La firma del índice al cierre (`archive → signature-service /sign-xml`) y `/verify` ya consumen este contrato: pasan a firma digital sin cambios en archive.

---

## 2. WORM / inmutabilidad (S3 Object Lock) — `storage-service`

| | |
|---|---|
| Estado actual | Plan de preservación + eventos PREMIS + AIP/BagIt. El evento `WORM` se registra, pero **no** hay Object Lock real. |
| Punto de swap | `storage-service` (cliente S3 + bucket de preservación; al persistir el AIP). |
| Norma | Acuerdo AGN 001/2024 Art. 4.3.2.6; Decreto 2609/2012 Art. 26. |

**Para activar WORM:**
1. Crear un **bucket de preservación** con *versioning* y **Object Lock** habilitado (modo *compliance*).
2. Al empaquetar el AIP (`POST /api/v1/preservacion/aip`), subir el objeto con `retention until` derivado de la **TRD** (E04, `archivo_central_years`) y, si aplica, *legal hold*.
3. Registrar el evento `WORM` (ya soportado) con la fecha de retención aplicada.
4. Requiere un almacén S3 con Object Lock **verificado** (batería de conformidad, ADR-027); el cálculo de retención ya existe (E04 `GET /trd/{id}/retention`).

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

## 4. Agentes externos — **no disponible**

| | |
|---|---|
| Estado actual | **No hay servidor MCP.** `mcp-server` se retiró con [ADR-051](adr/ADR-051-ppi-reducido-vive-en-la-app.md), que marca [ADR-019](adr/ADR-019-capa-mcp.md) como retirado. Docuboto no es un servidor de herramientas para agentes (ADR-051 §5). |
| Vía para un agente | La API HTTP con una cuenta de servicio de alcance acotado, con el mismo RBAC, clearance y auditoría que cualquier llamante; o el proveedor de inteligencia por PPI (§5). |

Que un cliente MCP externo (Claude Desktop, un IDE) opere el SGDEA **no está disponible**, y no figura como pendiente. Si algún día hace falta, se decide en su propio ADR.

---

## 5. Capa de conocimiento — pasarela FTS / PPI externo — `knowledge-service`

| | |
|---|---|
| Estado actual | knowledge-service **ya no aloja embeddings** ([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)). Pasarela FTS hacia document-service (`/search`, `/antecedentes`), delegada al proveedor por PPI cuando la entidad está en `ppi`. Sin ruta de generación, sin pgvector, sin `kb_suggestions`. |
| PPI | Contrato **v1.4.0**, congelado en `contracts/ppi/v1.4.0/` (índice en `contracts/ppi/README.md`). Docuboto envía `PPI-Version: 1.4.0`. El consumidor de ingesta arranca en el `lifespan` de la app solo con `PPI_PROVIDER_URL` fijado; `docker-compose.intelligence.yml` solo añade `app` a la red `ppi`. La entidad empieza en `intelligence_provider = 'local'`. |
| Inteligencia | Cuando hay PPI configurado, la inteligencia viene de ese proveedor externo; el respaldo local es FTS de document-service (no modelos en Docuboto). |
| Gate | `asistente_habilitado` + `PERM_CONOCIMIENTO` ([ADR-037](adr/ADR-037-asistente-activable-por-entidad.md)). |
| Decisión | [ADR-046](adr/ADR-046-conocimiento-sin-modelos.md) (retira la capa local de modelos); [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md) (PPI); ADR-006 queda histórico para el diseño pluggable original. |

**Qué quedó fuera de Docuboto (ADR-046):** no hay `embeddings.py`, `EMBEDDING_PROVIDER`, índice pgvector propio ni LLM local. Una búsqueda semántica es cosa de un proveedor externo por PPI, no de un modelo enchufado dentro de knowledge-service.

---

## 6. Operador postal real (4-72 / Servientrega) — `document-service`

| | |
|---|---|
| Estado actual | Proveedor **stub** (guía determinista, sin API real). |
| Punto de swap | `services/document-service/document/services/postal_provider.py` (`generar_guia`). |

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
| WORM (Object Lock) | storage-service | cliente S3 | RustFS con Object Lock (ADR-027) |
| PDF/A | storage-service | normalización | veraPDF |
| Clientes MCP externos | — | **no disponible** (ADR-051 §5) | — |
| Conocimiento FTS / PPI | knowledge-service | pasarela a document-service (+ PPI externo si está configurado) | PPI externo (opcional); sin modelos locales |
| Operador postal | document-service | `postal_provider.py` | credenciales API |
| Migración desde Orfeo | — | **fuera de alcance** | traductor externo hacia `POST /api/v1/import` |

Todas las integraciones conservan su contrato; activar cada una es implementar el adaptador y proveer la dependencia/credencial — **sin cambios en la lógica de dominio**.

La migración es la excepción y por eso figura sin servicio: no es un adaptador que se enchufe aquí, sino trabajo que se hace fuera contra un contrato de entrada estable (§7).
