# ADR-017: Modelo de archivo físico (ubicación ≠ unidad de conservación)

**Estado**: Aceptado
**Fecha**: 2026-06-21
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

Aunque OrpycaMCP es mayoritariamente digital, el soporte papel se sigue produciendo y hay un acervo físico que custodiar y localizar. E17 modela la **topología física** del almacenamiento, las **unidades de conservación** y la **signatura topográfica** que las localiza, vinculando la jerarquía intelectual (expediente) con la física. Es exigencia normativa: Acuerdo AGN 001/2024 (rotulado de unidades Art. 4.3.1.8, expediente híbrido Art. 4.3.3.1) y Acuerdo 042/2002 (FUID).

El Orfeo legado modela la ubicación como **campos planos** (depósito, estante, caja en columnas del expediente). Eso impide reorganizar el depósito sin tocar cada expediente, no soporta profundidad variable y mezcla "dónde está" (dirección fija) con "qué lo contiene" (caja, que se mueve).

## Decisión

**Separar la Ubicación (dirección física, jerarquía recursiva) de la Unidad de conservación (contenedor móvil), patrón ArchivesSpace; en archive-service, PostgreSQL por tenant.**

1. **`ubicacion`** — jerarquía **recursiva** (`parent_id` auto-referencia) de niveles tipados y configurables (sede→edificio→piso→depósito→módulo→estante→entrepaño→gaveta, y `custodia_externa`). `codigo` único por tenant; la **ruta** completa es derivable recorriendo los padres. `tipo_archivo` (gestión/central/histórico). Sin límite de profundidad.

2. **`unidad_conservacion`** — contenedor **móvil** tipado (caja|carpeta|legajo|libro|tomo|az|folder), anidable (`parent_id`), colocado en una `ubicacion` (`ubicacion_id`). Lleva la **signatura topográfica**.

3. **Signatura topográfica** — cadena estructurada legible (p. ej. `DEP01-E05-B03-C0124`), **única por tenant**, derivada de los códigos de la ruta de ubicación + el código de la unidad. Es el **puente intelectual↔físico**; se **recalcula al trasladar** la unidad (y el traslado queda en el historial).

4. **Vínculo N:M `expediente_unidad`** — una unidad contiene varios expedientes y un expediente (híbrido) puede ocupar varias unidades, con rango `folio_inicio/folio_fin`. Habilita "¿dónde está el expediente X?" y "¿qué contiene la caja Y?".

5. **FUID** (RF-ARF-10) — archive-service es la **fuente única canónica** del Formato Único de Inventario Documental, derivado del inventario físico; lo consume E12 (transferencias). (Exportación FUID, préstamos, historial, rótulo, capacidad y custodia externa son incrementos siguientes de E17.)

## Consecuencias

**Positivas:**
- Reorganizar el depósito = mover unidades y recalcular signaturas, **sin tocar los expedientes**.
- Profundidad variable y niveles configurables por tenant (no hay esquema rígido de columnas).
- Separación limpia "dirección" vs "contenedor": una caja puede cambiar de estante conservando su identidad y su contenido.
- Soporta el **expediente híbrido** (mismo expediente, parte física + parte electrónica) por la referencia cruzada vía signatura.
- Modelo nativo en archive-service (junto al expediente), sin servicio nuevo.

**Negativas / límites:**
- La jerarquía recursiva exige recorrer padres para derivar la ruta/signatura (coste O(profundidad) por consulta); se mitiga con índice por `parent_id` y, si hiciera falta, materializando la ruta.
- La unicidad y el recálculo de la signatura al trasladar añaden lógica de servicio (no es un simple campo).
- Capacidad/ocupación, préstamos, historial, rótulo PDF/QR y FUID export son piezas adicionales (incrementos posteriores).

## Alternativas consideradas

- **Campos planos (Orfeo legado)**: descartado; no reorganizable, profundidad fija, mezcla dirección y contenedor.
- **Ubicación y unidad fusionadas en una sola tabla**: descartado; impide mover una caja sin perder/duplicar la dirección y rompe el N:M expediente↔unidad.
- **Árbol genérico (ltree/closure table) para la ubicación**: viable, pero `parent_id` + índice es suficiente para la profundidad real del archivo; se reserva ltree como optimización futura si el volumen lo exige (coherente con [ADR-014](ADR-014-busqueda-postgresql-fts.md): empezar simple en PostgreSQL).

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — todo el modelo físico vive por `tenant_{slug}`.
- [ADR-015](ADR-015-trd-ccd-modelo.md) — la unidad física aloja expedientes ya clasificados por TRD.
- E12 (transferencias) consume el **FUID** que esta épica produce.
