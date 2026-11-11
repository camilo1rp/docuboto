# ADR-055: Lo que la Ley 1712 obliga a publicar se publica

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-015](ADR-015-trd-ccd-modelo.md) (TRD/CCD), [ADR-022](ADR-022-indice-electronico-v2-metadatos.md) (índice electrónico), [ADR-025](ADR-025-trd-instrumento-convalidado-versionado.md) (TRD convalidada), [ADR-040](ADR-040-cuadro-de-clasificacion-derivado-y-fases-de-la-trd.md) (CCD derivado), [ADR-038](ADR-038-antirrobots-altcha-y-ficha-provisional.md) (antirrobots), [ADR-054](ADR-054-la-firma-dice-la-verdad.md)

---

## Contexto

La Ley 1712 de 2014 obliga a publicar los instrumentos archivísticos: el art. 9 y el art. 13 (esquema de publicación y cuadro de clasificación) y el art. 12 (información mínima obligatoria). El Decreto 1080 de 2015 art. 2.8.2.5.8 obliga a publicar el PINAR.

El sistema implementa los instrumentos y no los publica. Verificado:

- **TRD/CCD**: `archive/routers/trd.py` gatea todas sus rutas con `require_permission("USUA_PERM_TRD")`. Los routers públicos de `archive` son dos: `public_indice.py` (índice de clasificados y reservados, art. 20) y `public_eliminacion.py` (publicidad previa de la eliminación). **No hay ruta pública del cuadro de clasificación.** El código documenta la obligación de publicar como *justificación* para abrir la lectura a usuarios autenticados, que no es la misma cosa.
- **PINAR**: `tenant/routers/pinar.py:78` gatea el router completo con `USUA_PERM_ADMIN`. `docs/es/api.md:378-380` dice que la publicación *«es un acto hacia fuera —una pantalla pública—»*, y esa pantalla no existe.
- **Folios**: `GET /api/v1/expedientes/{id}/foliado` **calcula** los folios por posición sobre los ítems vinculados. No hay ningún endpoint que escriba el número de folio: el campo `folios` de un vínculo es una cantidad que declara quien lo crea. Es una vista, no un acto de foliación. El propio `docs/es/api.md:1248` aclara que *«el índice electrónico normativo es E15»*.
- **Metadatos mínimos**: las plantillas son opt-in por tipo documental y por serie. Un tipo sin plantilla activa guarda `metadata` sin validar. La obligación de metadatos mínimos (Acuerdo AGN 001/2024) no está garantizada hasta que cada serie declare la suya — y nada obliga a declararla.
- **Tabla de control de acceso (TCA)**: la política vive dentro del índice firmado como `<listaControlAcceso>` con `modelo="no-read-up"`. No existe como instrumento administrable. La única aparición de «tabla de control de acceso» en el código es el nombre de una serie sembrada en `archive/migrations/seeds/fondecund_trd.sql:122`.

Hay además cuatro citas normativas marcadas como no verificadas en los propios ADR: Decreto 103 de 2015 arts. 29 y 40 (ADR-039, con `[confirmar campos]`), Ley 1448 de 2011 art. 144 (ADR-043, `[confirmar]`), la correspondencia de artículos del Acuerdo AGN 002 de 2004 con el 001 de 2024 (ADR-026), y el Acuerdo AGN 004 de 2013 que ADR-015 cita aunque está derogado.

## Decisión

### 1. Publicación de los instrumentos, como superficie propia

Se añade un prefijo público de instrumentos, hermano de `/api/v1/public/archive/`:

| Ruta | Contenido | Norma |
|---|---|---|
| `GET /api/v1/public/archive/instrumentos/{tenant}` | Índice de instrumentos publicados de la entidad | Ley 1712 art. 9 |
| `GET /api/v1/public/archive/instrumentos/{tenant}/ccd` (`.xml`, `.csv`) | Cuadro de clasificación derivado del organigrama (ADR-040) | Ley 1712 art. 13 |
| `GET /api/v1/public/archive/instrumentos/{tenant}/trd/{code}` | Una TRD/TVD convalidada, versión vigente y sus series | Ley 1712 art. 9, Acuerdo AGN 001/2024 |
| `GET /api/v1/public/archive/instrumentos/{tenant}/pinar` | PINAR vigente y su estado | Decreto 1080/2015 art. 2.8.2.5.8 |

Reglas:

- **Solo lo convalidado y vigente.** Una TRD en `borrador`, `aprobada` o `devuelta` no se publica: publicar un borrador es publicar información que no rige. La condición es `estado='convalidada'` y la versión que gobierna.
- **Sin contenido documental.** Publica estructura (series, subseries, códigos, retención, disposición, productor), nunca radicados.
- **Marcado `_PUBLIC`**, que es el chokepoint que ya usan `oai.py` y `public_indice.py` para impedir que una ruta pública herede el `search_path` de una entidad equivocada.
- **Sin ALTCHA y sin sesión**: es publicación, no formulario.
- **`GET /api/v1/public/archive/instrumentos/{tenant}/ccd` es el que responde al art. 13**, y es el que un ciudadano puede enlazar.

### 2. La TCA se vuelve un instrumento administrable

Se modela como una tabla, no como un elemento del índice:

`tca (serie_id, categoria_acceso, fundamento_legal, plazo, autoridad, version, convalidada_en)`

- Se administra en `/admin/trd` junto a la serie.
- El índice electrónico **sigue** llevando `<listaControlAcceso>`, pero generado desde esta tabla, no escrito a mano.
- Se publica en `GET …/instrumentos/{tenant}/tca` — una TCA que no se publica no sirve al ciudadano que quiere saber quién puede ver qué.

### 3. Foliado: se escribe, no se calcula

- Se añade `POST /api/v1/expedientes/{id}/foliado` que **asigna** folios con una secuencia por expediente, en una transacción, escribiendo `folio_desde`/`folio_hasta` en cada vínculo y un asiento de auditoría por acto.
- `GET /{id}/foliado` deja de inferir por posición: lee lo escrito. Un expediente sin foliar responde `409 expediente_sin_foliar`, no una lista inventada.
- La re-foliación (corregir un folio mal puesto) es un acto propio, auditado, que exige `USUA_PERM_EXPEDIENTE` a nivel 3 y no toca el índice firmado sin reconstruirlo.

### 4. Metadatos mínimos: obligatorios por serie, no por plantilla

- Se invierte la condición: una serie TRD **debe** declarar su plantilla de metadatos antes de poder convalidarse. El convalidation circuit ya existe y ya bloquea; se le añade esta condición.
- Un tipo documental sin plantilla activa deja de aceptar radicados (`422 metadatos_no_declarados`) en vez de guardar sin validar.
- Los radicados importados o históricos se admiten con una marca `metadatos_heredados` visible en la ficha, para que la deuda sea explícita en lugar de invisible.

### 5. Las citas normativas se verifican o se retiran

Cada cita marcada se resuelve con el texto de la norma a la mano, y se corrige el ADR que la sostiene:

| Cita | Acción |
|---|---|
| Decreto 103/2015 arts. 29 y 40 (hoy DUR 1081/2015) — ADR-039 | Verificar artículo y campos; corregir `[confirmar campos]` |
| Ley 1448/2011 art. 144 — ADR-043 | Verificar; si el artículo no sostiene el hold, elegir el que sí |
| Acuerdo AGN 002/2004 ↔ 001/2024 — ADR-026 | Verificar correspondencia; si no es verificable, citar solo el 001/2024 |
| Acuerdo AGN 004/2013 — ADR-015 | Marcar como derogado en el texto, como ya hizo ADR-025 |

Regla: **una cita con `[confirmar]` no puede sostener una función de cumplimiento.** O se verifica, o la función se describe como decisión de producto y no como obligación.

### 6. El roadmap se sustituye por una lista de conformidad con pruebas

`docs/es/roadmap.md` tiene 36 marcas «Pendiente» dentro de casillas marcadas como hechas, y afirma que las rutas batch dan 404 cuando `api-gateway/app/routers/proxy.py:128-129` las registra. Una lista cuya casilla contradice su texto no informa.

Se sustituye por `docs/es/conformidad.md`: una fila por obligación normativa, con la norma, el archivo que la implementa y **el nombre de la prueba que la demuestra**. Sin prueba, la fila dice «no demostrado», aunque el código exista. Una prueba de arquitectura comprueba que cada fila que dice «demostrado» nombra un test que existe.

### 7. Las rutas de conocimiento que se retiran con ADR-051 tienen destino

`/knowledge/search` y `/knowledge/antecedentes` se convierten en rutas de `document` (`GET /api/v1/search` ya existe y es la implementación real; se añade `/api/v1/search/antecedentes`). El frontend cambia de prefijo en el proxy único (ADR-056). Buscar y ver antecedentes **no desaparecen**; lo que desaparece es la capa que solo los reempaquetaba.

## Alternativas consideradas

- **Publicar los instrumentos desde el frontend, con datos servidos por el API autenticado y una cuenta de servicio.** Descartado: haría que la publicación dependiera de una credencial de servicio y de la salud de un login, y sería imposible de enlazar desde un portal de transparencia externo. La publicación es anónima por definición.
- **Publicar también las TRD en borrador para mostrar el trabajo en curso.** Descartado: publicar un instrumento que no rige induce a error sobre cuáles son las reglas de la entidad.
- **Dejar el foliado como vista.** Descartado: el foliado es un acto archivístico, y una vista que finge folios numerados es peor que no tener foliado, porque produce un número que no consta en ningún sitio.
- **Metadatos mínimos por plantilla global.** Descartado: cada serie tiene campos propios (Acuerdo AGN 001/2024), y una plantilla global produciría campos irrelevantes y ausentes a la vez.
- **Mantener el roadmap y corrigiéndolo.** Descartado: el problema no es una entrada desactualizada, es el formato. Una casilla no puede probar que algo existe; una prueba sí.

## Consecuencias

- **Se cierran cuatro brechas que un ciudadano puede observar hoy.** La publicación del CCD es la más visible: hoy el sistema no puede responder a «¿cuál es el cuadro de clasificación de esta alcaldía?».
- **Cuatro superficies públicas nuevas** que hay que limitar por tasa y por tamaño de respuesta.
- **La TCA obliga a poblar una tabla por serie.** Es trabajo de la entidad, no del sistema, y el convalidation circuit lo hace visible al convalidar.
- **El foliado cambia el modelo de datos** (`folio_desde`/`folio_hasta` en el vínculo) y toca el índice electrónico. Es el cambio más invasivo de este ADR y va después de la fusión.
- **Metadatos obligatorios pueden bloquear una entidad nueva** hasta que declare sus plantillas. Es intencional y tiene que estar en el runbook de alta.
- **El roadmap desaparece como documento.** Se archiva como histórico y `conformidad.md` toma su lugar; el guardián que compara casillas abiertas ES/EN se retira con él.
- **Cuatro ADR se corrigen en su texto**, no solo en este. Es trabajo de documentación con impacto en la credibilidad del resto.
