# ADR-006: Proveedor de IA configurable (multi-proveedor, global al despliegue) para la capa de conocimiento

**Estado**: Propuesto
**Fecha**: 2026-06-14
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

OrpycaMCP incorporará una **capa de conocimiento e inteligencia documental** (recuperación semántica, antecedentes entrada↔salida, sugerencias de TRD/dependencia y RAG anclado con citas) sobre el conocimiento histórico institucional. Esa capa necesita **inferencia de IA** en dos puntos:

1. **Embeddings** — para vectorizar el texto documental e indexarlo (búsqueda por similitud).
2. **Generación** — para el RAG: producir respuestas ancladas con citas a partir del contexto recuperado.

La cuestión a decidir es **dónde y con qué proveedor se ejecuta esa inferencia**, porque OrpycaMCP gestiona **documentos oficiales** de instituciones públicas, parte de ellos con **clasificación reservada**. Hay una tensión real:

- **Soberanía del dato** — el derecho de acceso a la información (Ley 1712/2014) y el régimen de datos personales (habeas data, Ley 1581/2012) presionan a que el contenido **no salga** de la institución.
- **Capacidad y operación** — los modelos en la nube suelen ser más capaces y no exigen que cada institución opere infraestructura de inferencia (GPU, modelos descargados).

Se consideraron como referencia conceptual (no de implementación) dos proyectos internos: un servicio de IA basado en **Ollama** con selección de modelos por rol (`MODEL_SECTION`/`MODEL_ANALYSIS`) y modo `local`/`cloud`; y un pipeline RAG (extracción → *embeddings* locales → *vector store* → LLM → API). De ellos se reutiliza el **patrón** (cliente HTTP del runtime de inferencia, selección de modelos por rol, extracción de respuesta tolerante a *thinking mode*), **no** su stack.

La decisión evolucionó durante el diseño: se partió de un planteamiento **local-only estricto** (sin nube) y, tras análisis, se concluyó que **la rigidez absoluta no era la elección correcta** para todas las instituciones. El resultado es una decisión de **proveedor configurable**, con la soberanía como **recomendación + responsabilidad del despliegue** en lugar de como restricción impuesta por el sistema.

## Decisión

**La inferencia de IA (embeddings y generación) se realiza a través de una interfaz genérica multi-proveedor, seleccionada por configuración global del despliegue.**

1. **Interfaz `LLMProvider`** con las implementaciones:
   - `ollama_local` — Ollama autohospedado (**default recomendado**, referencia).
   - `ollama_cloud` — Ollama gestionado en la nube.
   - `openai_compatible` — cualquier endpoint compatible con la API de OpenAI.
   - `anthropic` — API de Anthropic.
   - `disabled` — sin generación; la capa opera **solo en modo recuperación** (devuelve contexto rankeado y citado, sin LLM).
2. **Selección global del despliegue, no por tenant.** El proveedor y el mapeo de modelos por rol se fijan en la configuración del despliegue (`pydantic-settings`), no varían entre tenants. Lo que **sí** es opt-in por tenant es la **activación** de la capa de conocimiento (una institución puede operar el SGDEA completo sin ella).
3. **Modelos por rol** — `MODEL_EMBED` (embeddings), `MODEL_GENERATION` (RAG), `MODEL_SUGGEST` (opcional, rerank/razonamiento de sugerencias).
4. **La soberanía es responsabilidad del despliegue.** El default local (`ollama_local`) protege por defecto a las instituciones con contenido reservado: el contenido no sale. Configurar un proveedor en la nube/externo es una **decisión deliberada y auditada del administrador del despliegue**, que implica que el contenido documental autorizado —**sin distinción de clasificación**— se procesa en ese tercero. El sistema **no impone** una barrera dura que impida la salida; recomienda, advierte y audita.
5. **El control de acceso (ACL) es independiente del proveedor y se mantiene intacto.** El pre-filtrado por permisos y el aislamiento multi-tenant (ver [ADR-002](ADR-002-multi-tenancy-schema-isolation.md)) deciden **qué** contenido autorizado llega al modelo; el proveedor decide **dónde** se procesa. Habilitar la nube cambia *dónde* se procesa el contenido autorizado, **no** *quién* puede acceder a él: el modelo —local o en la nube— solo recibe el contenido que el usuario solicitante está autorizado a ver.

> **Reconciliación implementada (E21 Inc.3, RAG generativo, 2026-07-20) — ESTA DECISIÓN QUEDA SUPERADA para la GENERACIÓN por una barrera dura, consistente con el gate ya implementado para embeddings (Inc.1).** El punto 4 de arriba ("sin distinción de clasificación... el sistema no impone una barrera dura") describía el modelo suave original de este ADR. La decisión final del usuario (2026-07-20, `documentos/specDrive/00-capa-conocimiento.md` §"Invariante rector CC-03") es: **material `nivel_seguridad >= RESERVADA` NUNCA se envía a un proveedor de generación EXTERNO, aunque el usuario solicitante esté autorizado a verlo — fail-closed, SIN override configurable** (no existe un `ALLOW_CLASSIFIED_EXTERNAL`). Implementado como `is_generatable(nivel, provider)` (`knowledge-service/app/core/clearance.py`), gemelo estructural de `is_embeddable` (Inc.1) pero sin el matiz de override que aquella documenta. El router (`POST /rag`) usa este predicado para EXCLUIR del *contexto* enviado al LLM los chunks que no lo superan — no para bloquear la petición completa: el resto del contexto (nivel < RESERVADA, o cualquier nivel con `ollama_local`) se sigue procesando y, si el contexto elegible queda vacío, la respuesta degrada honestamente al Patrón A (recuperación pura) en vez de fallar o inventar. El **pre-filtro ACL** (punto 5, arriba) sigue siendo una capa aparte, previa e intacta: decide qué VE el usuario; la barrera de este párrafo decide qué SALE a un tercero. Los puntos 1-3 y 5 de esta sección, la "Auditoría de soberanía" y la infraestructura descritas más abajo **siguen vigentes sin cambios**; solo el punto 4 y la fila correspondiente de "Alternativas consideradas" quedan reemplazados para el caso de generación.

## Implementación

### Configuración (global del despliegue)

```python
# app/core/config.py (pydantic-settings) — NO varía por tenant
class Settings(BaseSettings):
    AI_PROVIDER: str = "ollama_local"   # ollama_local | ollama_cloud | openai_compatible | anthropic | disabled
    AI_BASE_URL: str = "http://ollama:11434"
    AI_API_KEY: str = ""                 # requerido para nube/externo
    MODEL_EMBED: str = "paraphrase-multilingual-MiniLM-L12-v2"
    MODEL_GENERATION: str = "llama3.1"
    MODEL_SUGGEST: str = ""              # opcional; vacío ⇒ usa MODEL_GENERATION
```

### Interfaz del proveedor

```python
# Protocolo común; una implementación por proveedor, cliente httpx async.
class LLMProvider(Protocol):
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...
    async def generate(self, prompt: str, model: str) -> str: ...

# El modelo NO corre dentro del proceso del servicio (excepto la opción de
# embeddings con sentence-transformers en proceso). knowledge-service es un
# cliente HTTP del proveedor configurado.
```

La respuesta de generación se extrae de forma **tolerante a *thinking mode***: algunos modelos devuelven el contenido en `response`/`thinking`/`output`/`text`/`content` según su configuración; una utilidad recorre esos campos en orden y devuelve el primer contenido útil.

### Contenedor de inferencia opcional

El contenedor `ollama` (puerto `11434`, volumen persistente de modelos) se define en `docker-compose.yml` bajo un **perfil**; se levanta cuando `AI_PROVIDER=ollama_local` (con `depends_on` condicional). Con un proveedor externo, el contenedor **puede no levantarse** y el servicio habla con `AI_BASE_URL` + `AI_API_KEY`.

### Salud y degradación

`/health` valida que el proveedor configurado responde y que los modelos por rol están disponibles. Si falta el modelo de **generación**, la capa **degrada a modo recuperación** en vez de fallar. Con `AI_PROVIDER=disabled`, el endpoint de RAG responde `409`.

### Auditoría de soberanía

Cada generación registra **proveedor y modelo en columnas separadas** (`proveedor`, `modelo`) en la traza de sugerencias, con índice `(proveedor, modelo)`. Esto da trazabilidad de **a dónde fue el contenido** y permite a una institución auditar si, y con qué, se usó un proveedor externo.

## Consecuencias

**Positivas:**
- **Soberano por defecto** — sin configuración adicional, la inferencia es local y el contenido no sale de la institución.
- **Flexible** — un despliegue puede optar por modelos más capaces en la nube cuando su política lo permite, sin cambiar código.
- **Proveedor intercambiable** — cambiar de modelo o proveedor no toca el dato maestro ni la lógica de dominio (la capa de conocimiento es un derivado reconstruible).
- **Trazable** — proveedor + modelo de cada generación quedan auditados, lo que respalda el cumplimiento.
- **El control de acceso no se relaja** — el ACL y el aislamiento multi-tenant son independientes del proveedor.

**Negativas:**
- **Con un proveedor en la nube, el contenido autorizado (incluida la clasificación reservada) sale a un tercero.** No hay barrera dura que lo impida: es una **responsabilidad del despliegue**, mitigada con el default local, la advertencia y la auditoría.
- **Operación del caso local** — usar `ollama_local` exige que la institución opere Ollama (descarga de modelos, hardware); la generación en CPU puede tener latencia alta (mitigable cayendo a modo recuperación).
- **Superficie de configuración** — el administrador debe entender el trade-off de soberanía al elegir proveedor; una mala configuración puede exfiltrar contenido sin que el sistema lo bloquee.

## Alternativas consideradas

- **Local-only estricto (sin nube)**: descartado por demasiado rígido. Impedía a instituciones que sí pueden/quieren usar modelos en la nube acceder a mayor capacidad; convertía la soberanía en una restricción del sistema en lugar de una decisión informada del despliegue.
- **Selección de proveedor por tenant**: descartado a favor de **configuración global del despliegue** (más simple de operar y auditar; el proveedor de inferencia es una decisión de infraestructura, no de cada institución inquilina).
- **Un único proveedor (solo Ollama, local + nube)**: descartado a favor de una **interfaz genérica multi-proveedor**, para no atar el proyecto a una sola tecnología (admite endpoints OpenAI-compatible, Anthropic, etc.).
- **Barrera dura "lo reservado nunca sale a la nube"**: descartado a favor de **sin distinción + responsabilidad del despliegue**. Una barrera por clasificación se evaluó como compleja y frágil; en su lugar, el default local protege por defecto y la salida a terceros queda como decisión consciente y auditada.

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — aislamiento multi-tenant por schema; base del pre-filtrado ACL, independiente del proveedor de IA.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — asyncpg + SQL crudo; aplica a las tablas de la capa de conocimiento (incluida la traza con `proveedor`/`modelo`).
