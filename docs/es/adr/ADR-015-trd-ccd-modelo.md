# ADR-015: Modelo de TRD/CCD (clasificación, retención y disposición)

**Estado**: Aceptado
**Fecha**: 2026-06-21
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

La Fase 3 implementa el corazón archivístico del SGDEA: la **Tabla de Retención Documental (TRD)** y el **Cuadro de Clasificación Documental (CCD)**, exigidos por la Ley 594/2000 y el Acuerdo AGN 004/2013. La TRD gobierna cuánto vive un documento y cuál es su destino final; sin ella, el sistema es un gestor de archivos, no un SGDEA.

Estructura archivística colombiana (AGN):

- **CCD** — jerarquía de clasificación: **Fondo → Sección/Subsección → Serie → Subserie**. Las secciones se corresponden con las dependencias del organigrama (E14).
- **TRD** — por cada serie/subserie: **tiempos de retención** en dos fases (Archivo de Gestión y Archivo Central) y la **disposición final**.
- **Tipos documentales** — 3.er nivel, ya implementados (E03, `tipos_documentales`).
- **Disposición final (AGN)**: `CT` Conservación Total · `E` Eliminación · `S` Selección · `M` Microfilmación/Digitalización (medio tecnológico).

Estado actual: existe `trd_series` (code, name, `retention_years`, `total_retention`, `disposition` ∈ eliminate/conserve/digitize/select), `tipos_documentales` (3.er nivel) y `disposition JSONB` en el radicado (E03). Falta el modelo jerárquico serie↔subserie, la retención en dos fases y el cálculo de la disposición sobre el ciclo de vida del expediente.

## Decisión

**Modelar la TRD/CCD de forma nativa en archive-service (PostgreSQL, asyncpg), con jerarquía serie↔subserie, retención en dos fases y disposición con códigos AGN. Sin un servicio nuevo.**

1. **Jerarquía** — `trd_series` gana `parent_id UUID` (auto-referencia anulable): una fila con `parent_id = NULL` es **serie**; con `parent_id` es **subserie**. Los `tipos_documentales` cuelgan de cualquiera de los dos niveles (FK ya existente `trd_serie_id`). La Sección del CCD se representa con `dependencia` (E14, tenant-service) — no se duplica el organigrama.

2. **Retención en dos fases** — se añaden `archivo_gestion_years` y `archivo_central_years`. El total de retención = gestión + central (se conserva `total_retention` como columna derivada/compatibilidad). Los campos `retention_years`/`total_retention` previos se mantienen para no romper datos sembrados.

3. **Disposición con códigos AGN** — `disposition` pasa a aceptar `CT | E | S | M`. Se mantiene un mapa de compatibilidad con los valores antiguos (`conserve→CT`, `eliminate→E`, `select→S`, `digitize→M`) para los datos ya existentes.

4. **Cálculo de retención/disposición** — el destino de un expediente se **deriva de su serie** y de su fecha de cierre: `fin_gestion = cierre + archivo_gestion_years`, `fin_central = fin_gestion + archivo_central_years`, y en `fin_central` se aplica la disposición final. El cálculo es una función pura sobre la fila de la serie (sin estado adicional); el resultado se materializa en la disposición del expediente/radicado (E03) al cerrar.

5. **Versionado** — la TRD evoluciona por convalidación del Comité (Acuerdo AGN 004/2013). Se modela con `version INTEGER` + `valid_from DATE` por serie; una reclasificación crea una **fila nueva** (mismo `code`, `version+1`) y la anterior se marca `is_active=false`. El expediente ya clasificado conserva su clasificación histórica (no se reescribe). La **lógica de cambio de versión en caliente** (reclasificación masiva) se difiere; F3 entrega el modelo y el cálculo.

## Consecuencias

**Positivas:**
- Conformidad con la estructura AGN (CCD jerárquico + TRD en dos fases + disposición CT/E/S/M), base legal del ciclo de vida.
- Reutiliza lo ya construido (`trd_series`, `tipos_documentales`, `disposition` del radicado) sin reescritura; los datos sembrados (FondeCund) siguen válidos vía el mapa de compatibilidad.
- Cálculo de retención **determinista y auditable** (función pura sobre la serie), sin acoplar al organigrama (la Sección es la dependencia de E14).
- Sin servicio nuevo: la TRD vive junto a los expedientes (archive-service), su consumidor natural.

**Negativas / límites:**
- Jerarquía de dos niveles (serie/subserie) por auto-referencia; un CCD con más niveles requeriría generalizar a árbol — fuera de alcance AGN estándar.
- El versionado entregado es estructural (filas versionadas); el **switching/reclasificación masiva** queda diferido.
- Mantener dos juegos de campos de retención (antiguos + dos fases) durante la transición añade columnas; se documenta cuál es la fuente de verdad (las dos fases).

## Alternativas consideradas

- **CCD como árbol genérico (closure table / ltree)**: potente pero sobredimensionado para la jerarquía AGN de profundidad fija; descartado por complejidad.
- **TRD en servicio propio**: descartado; la TRD se consulta en cada cierre/transferencia de expediente — vivir junto al expediente evita llamadas entre servicios (coherente con la autonomía de servicios).
- **Disposición solo como texto libre**: descartado; los códigos AGN (CT/E/S/M) son normativos y dirigen la automatización del ciclo de vida.

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — la TRD vive por `tenant_{slug}`.
- [ADR-007](ADR-007-metadatos-jsonb.md) / E03 — la disposición calculada se materializa en `disposition` del radicado/expediente.
- E14 — la Sección del CCD se corresponde con la dependencia del organigrama (no se duplica).
