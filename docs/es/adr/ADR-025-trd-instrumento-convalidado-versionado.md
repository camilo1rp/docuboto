# ADR-025 — La TRD es un instrumento convalidado, no una tabla de configuración: versionado append-only y fijación de la versión que gobierna cada expediente

**Estado**: Propuesto (Increment 1 especificado; 2 y 3 declarados como hoja de ruta, no deuda oculta). Diseño de `orfeo-architect` a petición de `archival-compliance-auditor` (hallazgo **Crítico**). Pendiente de implementación (`fastapi-developer`) y de re-auditoría por `archival-compliance-auditor` + `tenant-security-auditor`.

**Fecha**: 2026-07-31.

**Contexto normativo**: Ley 594/2000 arts. 24-26; **Acuerdo AGN 004/2019** (elaboración, aprobación por el Comité Institucional de Gestión y Desempeño, **convalidación** por el Consejo Departamental/Distrital de Archivos, publicación e inscripción en el **RUSD**); Acuerdo AGN 004/2013 (derogado en lo pertinente por 004/2019, citado en ADR-015); Acuerdo AGN 001/2024 art. 4.3.2.6 (retención del expediente electrónico).

---

## Contexto

ADR-015 modeló la TRD como **contenido** (jerarquía serie↔subserie, retención en dos fases, disposición AGN) y **difirió explícitamente** el versionado en caliente: *"el versionado entregado es estructural (filas versionadas); el switching/reclasificación masiva queda diferido"*. La implementación no llegó ni a lo estructural.

Estado verificado en código (2026-07-31):

| Afirmación | Realidad |
|---|---|
| Versionado estructural | `version` y `valid_from` existen (`migrations/tenant/006_trd_ccd.sql:17-22`) pero **son vestigiales**: no están en `TrdSerieCreate`/`TrdSerieUpdate` (`app/schemas/archive.py:188-222`) y ningún SQL las escribe. `version` vale `1` para siempre. |
| Una reclasificación crea fila nueva | Falso: `update_trd_serie` hace `UPDATE trd_series SET retention_years = ...` **en sitio** (`app/repositories/archive_repository.py:604-668`). El valor anterior deja de existir. |
| Coexistencia de versiones | **Estructuralmente imposible**: `code VARCHAR(20) NOT NULL UNIQUE` (`migrations/tenant/001_create_archive.sql:46`). No está "sin implementar": está bloqueado por el esquema. |
| Circuito de convalidación | **Inexistente**. Ni acto administrativo, ni fecha, ni instancia convalidante, ni RUSD, ni estado del instrumento. |
| La API no sobre-declara | Falso: `TrdSerieResponse` expone `version` y `valid_from` (`app/schemas/archive.py:239-240`). La API **afirma un versionado que no existe** (RF-FIR-15). |

`public.audit_log` (ADR-008/ADR-010) **sí** registra `archive.trd_serie_updated` con before/after. Eso da trazabilidad del cambio, y **no cierra el hallazgo**: la auditoría prueba *qué se cambió*, no permite *reconstruir el estado vigente* para el cálculo, porque todo el sistema resuelve la retención leyendo la fila **viva**.

### Por qué es Crítico: el punto exacto de la fuga

`expedientes.trd_serie_id` apunta a una fila **mutable**, y **nueve** puntos del código derivan consecuencias jurídicas leyendo esa fila **en el instante de la ejecución**:

1. `ArchiveService.compute_retention` (`app/services/archive_service.py:1938`) — único punto de cálculo del calendario.
2. `ArchiveService.close_expediente` (`:1123-1223`) — materializa el calendario en `expedientes.disposition` (JSONB).
3-4. `IndexService._resolve_worm_retention` / `_resolve_ct_retencion_anios` (`app/services/index_service.py:1096-1142`) — `retain_until` **WORM COMPLIANCE** del índice firmado.
5-6. `TransferenciaService._resolve_worm_retention_acta` / `_resolve_ct_retencion_anios_acta` (`app/services/transferencia_service.py:1724-1778`) — WORM del acta.
7-8. `AipWiringService._resolve_worm_retention` / `_resolve_aip_pdfa_profile` (`app/services/aip_wiring_service.py:105-133`) — WORM + perfil PDF/A del AIP.
9. `jobs/indice_reconciliation.py` — renovación de la ventana WORM para series CT.

Consecuencias, en orden de gravedad:

- **Alteración retroactiva del calendario de disposición.** Un `PATCH /api/v1/trd/{id}` de un administrador cambia hoy, en silencio, el destino de expedientes cerrados hace años. En 2032 el sistema no podrá demostrar bajo qué versión convalidada se decidió eliminar, ni que estuviera convalidada al cerrar. La eliminación queda **sin respaldo oponible**.
- **Efecto irreversible sobre WORM.** Los puntos 3-9 alimentan `retain_until` en modo COMPLIANCE (ADR-023): un `PATCH` que alargue la retención produce objetos **imborrables durante años**, y uno que la acorte produce renovaciones WORM cortas. Es la única consecuencia de esta fuga que **no se puede deshacer** ni con una corrección posterior.
- **La disposición la puede gobernar un instrumento inexistente.** `close_expediente` materializa el calendario sin comprobar **ningún** estado del instrumento: una serie recién tecleada, jamás aprobada ni convalidada, gobierna la eliminación de documentos públicos.

Lo que **no** está roto y conviene no tocar: la reclasificación de un expediente (`update_metadata`, `:833-970`) ya exige `open` + actor identificable + asiento propio `archive.expediente_reclasificado`. El vector retroactivo es **exclusivamente** el `UPDATE` de `trd_series`.

---

## Decisión

**La TRD se modela como instrumento convalidado: filas de versión append-only, con estado y acto administrativo, y cada expediente queda amarrado a la versión concreta que lo gobierna. La retención nunca se recalcula hacia atrás.**

Se divide en tres incrementos. **Este ADR especifica el Increment 1**, que es el que cierra la fuga retroactiva; 2 y 3 quedan declarados.

### D1 — Append-only: una versión es una fila, y las filas no vigentes no se tocan

`UNIQUE (code)` → `UNIQUE (code, version)`. `PATCH` deja de ser un `UPDATE` de contenido: una modificación sustantiva **crea una versión nueva** (`version + 1`, estado `borrador`) y, al convalidarla, **cierra** la anterior (`valid_to`, `estado='derogada'`, `is_active=false`) en una sola transacción. Índice parcial único → **como máximo una versión vigente por código** (y como máximo una en trámite, para evitar bifurcaciones).

La inmutabilidad no es procedimental: un **trigger** rechaza el `UPDATE` de los campos sustantivos (`code`, `name`, `parent_id`, retención, `disposition`, `valid_from`, `version`) sobre cualquier fila que no esté en `borrador`, y el `DELETE` de cualquier fila que no esté en `borrador`. Mismo criterio y mismo precedente que `transferencia_acta` (migración 023): *imposibilitado por el motor*, no solo por convención de código.

Excepción explícita y acotada: `pdfa_profile` **sí** es editable en sitio en cualquier estado. No es contenido convalidado de la TRD (no es retención ni disposición); es política de preservación técnica introducida por E10 (migración 035). Bloquearlo rompería el perfil PDF/A por serie sin ganancia jurídica.

### D2 — Estado del instrumento y acto administrativo

Se añaden `estado`, `valid_to` y los campos del acto: `acto_administrativo`, `fecha_aprobacion_comite`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`, `fecha_publicacion`. **El circuito del Acuerdo 004/2019 se modela como estado, no como texto libre.**

### D3 — El expediente fija la versión: `trd_serie_id` **es** el amarre (recomendación al punto 6)

Se pidió una recomendación, no un menú. Es esta:

> **`expedientes.trd_serie_id` se conserva como el amarre, y pasa a apuntar a una fila-versión inmutable. Se congela al cerrar y no se reescribe jamás. Se añade `trd_serie_code` como denormalización de consulta, y el snapshot ya existente (`expedientes.disposition`) se enriquece con la procedencia de la versión. Se descarta `(trd_serie_code, trd_serie_version)` como clave de enlace.**

El argumento decisivo es de **radio de impacto**, y es verificable: los nueve puntos de derivación listados arriba resuelven **todos** por el mismo seam, `get_trd_serie_by_id(exp.trd_serie_id)`. Si esa columna pasa a apuntar a una fila-versión inmutable, los nueve quedan correctos **sin tocarlos**. Cualquier diseño que introduzca una columna nueva de amarre (`trd_serie_version_id`) **bifurca el seam** y obliga a editar nueve sitios — incluidos los tres que alimentan WORM COMPLIANCE, donde un error es irreversible. Un amarre nuevo compra precisión nominal y paga con nueve oportunidades de equivocarse.

Por qué se descartan las otras dos como amarre primario:

- **`(code, version)` compuesta**: exige FK compuesta contra `UNIQUE(code, version)`, dos columnas en cada join, y no aporta nada que el UUID —que **ya está en la tabla y ya está poblado**— no dé. `code` y `version` sí viajan **dentro del snapshot** por legibilidad humana del expediente; como clave de join, no.
- **Solo snapshot de reglas al cerrar**: insuficiente por sí solo. Un JSONB no se puede unir, no se puede indexar cómodamente por serie, y no impide que la fila que lo originó sea borrada o alterada. El snapshot es **prueba**, no **integridad referencial**.

Las tres piezas tienen papeles distintos y complementarios:

| Pieza | Papel | Garantía |
|---|---|---|
| `trd_serie_id` → fila-versión, con **FK real** (hoy no existe ninguna) | **Identidad**: qué versión gobernó | El motor impide borrar la versión mientras un expediente la invoque |
| `trd_serie_code` (denormalizado) | **Clasificación**: a qué serie pertenece, con independencia de la versión | Consultas "todos los expedientes de la serie 100-25" y supervivencia si la fila fuera irresoluble |
| `expedientes.disposition` (JSONB, enriquecido) | **Prueba**: cuál era el calendario y bajo qué acto | Legible, exportable, oponible sin reconstruir la BD |

**Flotar mientras abierto, congelar al cerrar.** Un expediente `open` no tiene calendario (la retención arranca en el cierre): su `trd_serie_id` puede apuntar a una versión ya derogada sin daño. Al **cerrar**, `close_expediente` re-resuelve por `trd_serie_code` la versión **vigente en la fecha de cierre**, escribe ese `id` en `trd_serie_id` (**la última escritura de esa columna en la vida del expediente**) y materializa el snapshot. Un trigger sobre `expedientes` rechaza cualquier cambio de `trd_serie_id` cuando el estado previo ya era `closed`/`transferred`.

Esto también resuelve, sin batch ni job, el "switching en caliente" que ADR-015 dejó diferido: **no hay reclasificación masiva** al convalidar una versión nueva; los expedientes abiertos se re-resuelven solos por código en el momento del cierre.

### D4 — Retroactividad: **congelar**, y marcar lo que no se puede congelar honestamente

Recomendación: **congelación**. El calendario de un expediente lo gobierna la versión convalidada vigente **en su fecha de cierre**, y una convalidación posterior **no lo altera**. Es la lectura correcta del Acuerdo 004/2019 (la actualización rige hacia adelante) y es, exactamente, la fuga que este ADR cierra: recalcular hacia atrás *es* el defecto.

- **Recalcular** — descartado. Es el estado actual del sistema. Hace irreconstruible el fundamento de cualquier eliminación y, vía WORM COMPLIANCE, produce efectos irreversibles.
- **Marcar todo para revisión** — descartado como política general. Convertiría cada actualización de TRD en una cola de trabajo manual proporcional al archivo histórico; en la práctica se ignora, y una marca que todos ignoran es peor que no tenerla porque simula control.
- **Congelar** — recomendado. Lo que se pierde: una entidad que acorte la retención por norma nueva **no** verá el efecto sobre expedientes ya cerrados; necesitará un acto explícito. Eso es correcto, no un defecto: cambiar el destino de expedientes ya cerrados es un acto administrativo, y debe costar un acto administrativo. Se modela en el Increment 3 como **re-baselining explícito**, por expediente, con actor, motivo y asiento — nunca como efecto colateral de un `PATCH`.

Caso residual honesto: los expedientes ya cerrados **sin** snapshot (`disposition IS NULL`: cerrados sin serie, o cerrados antes de la migración 007) no se pueden congelar, porque no hay nada que congelar. Esos, y solo esos, se marcan `trd_revision_requerida = true` con motivo. El sistema **no inventa** el calendario que "habría" tenido.

### D5 — Gate de disposición: `borrador` no gobierna nada; `migrada` gobierna pero se declara

- **Cerrar** un expediente (que solo *proyecta* un calendario) exige serie en estado `convalidada` **o** `migrada`. `borrador`, `aprobada` y `derogada` → `409 trd_version_no_vigente`.
- **Ejecutar** la disposición final (eliminación efectiva — **hoy no existe ningún job que elimine**; el único job es la renovación WORM de series CT) exigirá `convalidada` **estricto**, sin excepción para `migrada`. Como esa ruta aún no existe, la regla se escribe ahora y se hace exigible cuando se implemente (Increment 2).

`aprobada` no habilita el cierre a propósito: la aprobación del Comité es condición necesaria y **no suficiente** (Acuerdo 004/2019); sin convalidación del Consejo, el instrumento no es oponible.

### D6 — Backfill: `migrada`, y el motor impide que `migrada` se disfrace de convalidada

Se pidió criterio, no comodidad. **`version=1, estado='convalidada'` es inaceptable**: sería el sistema **afirmando un hecho jurídico que nadie realizó** — exactamente lo que este proyecto no hace (RF-FIR-15). Y no es una sutileza: esa afirmación falsa sería precisamente la que se invoque para justificar una eliminación en 2032.

Las filas existentes quedan en **`estado='migrada'`**: *"esta serie existía antes del modelo de convalidación; el sistema no sabe si fue convalidada ni bajo qué acto"*. Es una afirmación verdadera y completa.

Para que `migrada` no derive hacia una convalidación de facto, se refuerza en tres capas:

1. **CHECK estructural**: `estado='migrada'` **exige** `acto_administrativo`, `fecha_convalidacion`, `instancia_convalidante` y `rusd_radicado` **todos NULL**. Es imposible rellenar los campos del acto y quedarse en `migrada` — o se declara el acto y se pasa a `convalidada`, o no hay acto. La honestidad la sostiene el motor.
2. **El snapshot lo propaga**: todo expediente que se cierre bajo una serie `migrada` lleva en su `disposition` → `serie_estado: "migrada"`, `acto_administrativo: null`. La declaración viaja con la prueba, hasta el AIP.
3. **Marca de revisión**: esos expedientes quedan `trd_revision_requerida = true`, motivo `cerrado_bajo_serie_migrada`. La entidad ve exactamente qué le falta ratificar, y no queda bloqueada operando mientras tanto.

**Ratificación** (Increment 2): `POST /api/v1/trd/series/{code}/versiones/{version}/convalidar` con el acto real → `migrada → convalidada` **sin crear versión nueva** (el contenido no cambia; lo que se registra es el acto que ya existía en papel) y limpia las marcas de los expedientes cerrados bajo esa versión. El camino honesto existe y es barato; lo que no existe es el atajo de que el sistema lo dé por hecho.

---

## Modelo de datos — migración propuesta (**no aplicada**)

`services/archive-service/migrations/tenant/037_trd_versionado_convalidacion.sql`. Idempotente y **acotada a `current_schema()`** en todos los guards (lección de las migraciones 016/017/019/035, reparadas por la 036: un guard que filtra por nombre de objeto sin schema **omite silenciosamente** el objeto en el tenant #2 y siguientes).

```sql
-- (cabecera de licencia AGPL estándar del proyecto — omitida aquí por brevedad)

-- Migración de tenant (ADR-025): la TRD pasa de tabla de configuración a
-- instrumento convalidado. Cierra la fuga retroactiva: hoy `UPDATE trd_series`
-- altera el calendario de disposición de expedientes cerrados hace años y el
-- `retain_until` WORM COMPLIANCE (irreversible) de índices, actas y AIPs.

-- ---------------------------------------------------------------------------
-- 1. trd_series: coexistencia de versiones
-- ---------------------------------------------------------------------------

-- UNIQUE(code) creada inline en 001 => nombre autogenerado `trd_series_code_key`.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t     ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = current_schema()
           AND t.relname = 'trd_series'
           AND c.conname = 'trd_series_code_key'
    ) THEN
        ALTER TABLE trd_series DROP CONSTRAINT trd_series_code_key;
    END IF;
END $$;

-- `ADD COLUMN IF NOT EXISTS` es nativamente schema-scoped (no necesita guard).
-- DEFAULT 'migrada' rellena las filas EXISTENTES con la única verdad
-- verificable (D6); el DEFAULT para filas NUEVAS pasa a 'borrador' justo
-- después. El orden importa.
ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS estado                  VARCHAR(20) NOT NULL DEFAULT 'migrada',
    ADD COLUMN IF NOT EXISTS valid_to                DATE,
    ADD COLUMN IF NOT EXISTS acto_administrativo     TEXT,
    ADD COLUMN IF NOT EXISTS fecha_aprobacion_comite DATE,
    ADD COLUMN IF NOT EXISTS fecha_convalidacion     DATE,
    ADD COLUMN IF NOT EXISTS instancia_convalidante  TEXT,
    ADD COLUMN IF NOT EXISTS rusd_radicado           TEXT,
    ADD COLUMN IF NOT EXISTS fecha_publicacion       DATE,
    ADD COLUMN IF NOT EXISTS supersede_a             UUID REFERENCES trd_series(id),
    ADD COLUMN IF NOT EXISTS created_by              UUID;

ALTER TABLE trd_series ALTER COLUMN estado SET DEFAULT 'borrador';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_estado_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_estado_check
            CHECK (estado IN ('borrador','aprobada','convalidada','migrada','derogada'));
    END IF;

    -- Convalidada EXIGE el acto (Ac. AGN 004/2019).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_convalidada_acto_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_convalidada_acto_check
            CHECK (estado <> 'convalidada' OR (
                acto_administrativo    IS NOT NULL AND
                fecha_convalidacion    IS NOT NULL AND
                instancia_convalidante IS NOT NULL));
    END IF;

    -- D6: `migrada` NO PUEDE llevar datos de acto. El motor impide que una
    -- serie migrada se disfrace de convalidada rellenando campos.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_migrada_sin_acto_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_migrada_sin_acto_check
            CHECK (estado <> 'migrada' OR (
                acto_administrativo    IS NULL AND
                fecha_convalidacion    IS NULL AND
                instancia_convalidante IS NULL AND
                rusd_radicado          IS NULL AND
                fecha_aprobacion_comite IS NULL));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_vigencia_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_vigencia_check
            CHECK ((valid_to IS NULL OR valid_to >= valid_from)
               AND (estado <> 'derogada' OR valid_to IS NOT NULL)
               AND version >= 1);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_code_version_key') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_code_version_key UNIQUE (code, version);
    END IF;
END $$;

-- Una sola versión VIGENTE por código (los nombres de índice son per-schema:
-- `IF NOT EXISTS` resuelve contra current_schema()).
CREATE UNIQUE INDEX IF NOT EXISTS ux_trd_series_vigente
    ON trd_series (code)
 WHERE estado IN ('convalidada','migrada') AND valid_to IS NULL;

-- Una sola versión EN TRÁMITE por código (evita bifurcar el instrumento).
CREATE UNIQUE INDEX IF NOT EXISTS ux_trd_series_en_tramite
    ON trd_series (code)
 WHERE estado IN ('borrador','aprobada');

CREATE INDEX IF NOT EXISTS ix_trd_series_code_version ON trd_series (code, version DESC);

-- ---------------------------------------------------------------------------
-- 2. Inmutabilidad ESTRUCTURAL de las versiones no-borrador (precedente: 023)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        -- `pdfa_profile` y `is_active` quedan FUERA: no son contenido
        -- convalidado de la TRD (pdfa_profile es politica de preservacion,
        -- E10/migracion 035; is_active lo mueve la derogacion).
        IF NEW.code                  IS DISTINCT FROM OLD.code
        OR NEW.name                  IS DISTINCT FROM OLD.name
        OR NEW.description           IS DISTINCT FROM OLD.description
        OR NEW.parent_id             IS DISTINCT FROM OLD.parent_id
        OR NEW.retention_years       IS DISTINCT FROM OLD.retention_years
        OR NEW.total_retention       IS DISTINCT FROM OLD.total_retention
        OR NEW.archivo_gestion_years IS DISTINCT FROM OLD.archivo_gestion_years
        OR NEW.archivo_central_years IS DISTINCT FROM OLD.archivo_central_years
        OR NEW.disposition           IS DISTINCT FROM OLD.disposition
        OR NEW.version               IS DISTINCT FROM OLD.version
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from THEN
            RAISE EXCEPTION 'trd_series: contenido inmutable en estado % (codigo %, version %) — una modificacion sustantiva crea una version nueva (ADR-025)',
                OLD.estado, OLD.code, OLD.version;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_append_only ON trd_series;
CREATE TRIGGER trg_trd_series_append_only
    BEFORE UPDATE OR DELETE ON trd_series
    FOR EACH ROW EXECUTE FUNCTION reject_trd_version_mutation();

CREATE OR REPLACE FUNCTION reject_trd_series_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'trd_series es append-only: TRUNCATE denegado (ADR-025)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_no_truncate ON trd_series;
CREATE TRIGGER trg_trd_series_no_truncate
    BEFORE TRUNCATE ON trd_series
    FOR EACH STATEMENT EXECUTE FUNCTION reject_trd_series_truncate();

-- ---------------------------------------------------------------------------
-- 3. expedientes: amarre a la version + marca de revision
-- ---------------------------------------------------------------------------
ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS trd_serie_code         VARCHAR(20),
    ADD COLUMN IF NOT EXISTS trd_revision_requerida BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS trd_revision_motivo    TEXT;

CREATE INDEX IF NOT EXISTS ix_expedientes_trd_serie_code ON expedientes (trd_serie_code);
CREATE INDEX IF NOT EXISTS ix_expedientes_trd_revision
    ON expedientes (trd_revision_requerida) WHERE trd_revision_requerida;

-- Backfill del codigo desde la fila actual (que sera la version 1).
UPDATE expedientes e
   SET trd_serie_code = s.code
  FROM trd_series s
 WHERE s.id = e.trd_serie_id
   AND e.trd_serie_code IS NULL;

-- FK REAL a la fila-version (hoy `trd_serie_id` es un UUID suelto, sin FK:
-- nada impide que la version que gobierna un expediente desaparezca).
-- NOT VALID: no se puede fallar la provision por huerfanos historicos; las
-- escrituras NUEVAS si quedan verificadas desde ya. La validacion completa
-- (`VALIDATE CONSTRAINT`) va en el Increment 2, tras depurar huerfanos.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'expedientes'
          AND c.conname = 'expedientes_trd_serie_id_fkey') THEN
        ALTER TABLE expedientes
            ADD CONSTRAINT expedientes_trd_serie_id_fkey
            FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id) NOT VALID;
    END IF;
END $$;

-- Marcas de revision (D4/D6). NO se inventa ningun calendario.
UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'cierre_sin_snapshot_de_retencion'
 WHERE status IN ('closed','transferred')
   AND disposition IS NULL
   AND NOT trd_revision_requerida;

UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'serie_no_resoluble'
 WHERE trd_serie_id IS NOT NULL
   AND trd_serie_code IS NULL
   AND NOT trd_revision_requerida;

UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'cerrado_bajo_serie_migrada'
 WHERE status IN ('closed','transferred')
   AND trd_serie_code IS NOT NULL
   AND disposition IS NOT NULL
   AND NOT trd_revision_requerida;

-- Congelacion del amarre: tras el cierre, `trd_serie_id` no se reescribe nunca.
-- (Al CERRAR, OLD.status aun es 'open' -> la escritura de congelacion pasa.)
CREATE OR REPLACE FUNCTION reject_trd_pin_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('closed','transferred')
       AND NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id THEN
        RAISE EXCEPTION 'expedientes: la version TRD que gobierna un expediente % es inmutable tras el cierre (ADR-025)', OLD.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expedientes_trd_pin_immutable ON expedientes;
CREATE TRIGGER trg_expedientes_trd_pin_immutable
    BEFORE UPDATE ON expedientes
    FOR EACH ROW EXECUTE FUNCTION reject_trd_pin_mutation();

-- Rollback (referencia manual, no ejecutado por el runner):
--   DROP TRIGGER IF EXISTS trg_expedientes_trd_pin_immutable ON expedientes;
--   DROP TRIGGER IF EXISTS trg_trd_series_append_only ON trd_series;
--   DROP TRIGGER IF EXISTS trg_trd_series_no_truncate ON trd_series;
--   DROP INDEX IF EXISTS ux_trd_series_vigente, ux_trd_series_en_tramite;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_code_version_key; ...
--   (el rollback NO restaura UNIQUE(code) si ya existen varias versiones)
```

**Validación exigida antes de integrar** (contenedor Postgres 15 desechable, CLAUDE.md): crear dos schemas de tenant y aplicar la migración en ambos **en orden**, comprobando que el tenant #2 obtiene *todos* los objetos (el modo de fallo histórico del proyecto), que re-aplicarla es no-op, y **por mutación**: intentar `UPDATE trd_series SET retention_years=99` sobre una fila `migrada` debe fallar; sobre una `borrador`, pasar.

---

## Máquina de estados del instrumento

```
                     (POST /trd)                    aprobar              convalidar
                          │                            │                     │
                          ▼                            ▼                     ▼
   [ nueva serie ] ──► borrador ──────────────────► aprobada ──────────► convalidada
                          │  ▲                                                │
              descartar   │  │ (POST /trd/{code}/versiones: clona la vigente) │
              (DELETE ok) ▼  │                                                │
                        (nada)└────────────────────────────────────────────── ┘
                                        al convalidar la version N+1,
                                        la version N pasa a ────────────► derogada
                                                                    (valid_to = fecha,
                                                                     is_active = false)

   [ filas preexistentes ] ──► migrada ──ratificar (acto real)──► convalidada
                                  │
                                  └──────── derogar ───────────► derogada
```

| Estado | Puede gobernar un **cierre** | Puede gobernar la **eliminación efectiva** | Contenido mutable |
|---|---|---|---|
| `borrador` | No (`409`) | No | **Sí** (es el único estado editable) |
| `aprobada` | No (`409`) | No | No |
| `convalidada` | Sí | Sí | No (salvo `pdfa_profile`) |
| `migrada` | Sí, **declarándolo** en el snapshot | **No** | No (salvo `pdfa_profile`) |
| `derogada` | No (`409`) | Solo la ya fijada en snapshots previos | No |

`is_active` queda como columna **derivada/legado**: la fuente de verdad es `estado`. Se mantiene sincronizada (`derogada ⇒ is_active=false`) para no romper `list_trd_series(is_active=...)` ni el frontend.

---

## Contratos de API afectados

| Endpoint | Cambio | Ruptura |
|---|---|---|
| `POST /api/v1/trd` | Crea **versión 1 en `borrador`**. `estado` **no** es settable por el cliente. | **Sí**: hoy una serie recién creada es utilizable de inmediato. Tests y seeds afectados (ver Impacto). |
| `PATCH /api/v1/trd/{serie_id}` | Solo sobre `borrador`. Sobre cualquier otro estado: `409 trd_version_inmutable` — salvo `pdfa_profile`, que sigue aceptándose siempre. `is_active` deja de aceptarse (la baja es `derogar`). | **Sí**, deliberada: es el endpoint que causa el hallazgo. |
| `POST /api/v1/trd/{code}/versiones` | **Nuevo**. Clona la versión vigente como `borrador` con `version+1`. Body: los campos a cambiar. | Aditivo |
| `POST /api/v1/trd/{code}/versiones/{version}/aprobar` · `/convalidar` · `/derogar` | **Nuevos** (Increment 2). `convalidar` exige acto, fecha e instancia; cierra la versión anterior **en la misma transacción**. | Aditivo |
| `GET /api/v1/trd` | Devuelve **solo vigentes** por defecto (`?historico=true` para todas las versiones). Preserva la UX actual: una fila por código. | No (comportamiento equivalente al actual) |
| `GET /api/v1/trd/{code}/versiones` | **Nuevo**: historia completa del instrumento. | Aditivo |
| `GET /api/v1/trd/{serie_id}/retention` | Sin cambio de firma; pasa a ser **preciso** (calcula sobre una versión concreta). | No |
| `GET /api/v1/expedientes/{id}` | `disposition` gana claves de procedencia; nuevos `trd_serie_code`, `trd_revision_requerida`. | Aditivo |
| `POST /api/v1/expedientes/{id}/close` | `409 trd_version_no_vigente` si la serie no es `convalidada`/`migrada`. | **Sí** (nuevo modo de fallo) |
| `TrdSerieResponse` | `version`/`valid_from` pasan a ser **verdad**; se añaden `valid_to`, `estado` y campos del acto. | Aditivo (cierra la sobre-declaración actual) |

**Snapshot enriquecido** (`RetentionSchedule` → `expedientes.disposition`), aditivo sobre las claves que ya consume el frontend (`disposition_label`, `fin_archivo_central`, …):

```jsonc
{
  "serie_id": "…", "code": "100-25", "closed_at": "2026-07-31",
  "archivo_gestion_years": 2, "archivo_central_years": 8,
  "fin_archivo_gestion": "2028-07-31", "fin_archivo_central": "2036-07-31",
  "disposition_code": "E", "disposition_label": "Eliminación",
  // ADR-025 — procedencia del instrumento que gobernó esta decisión:
  "serie_version": 3,
  "serie_estado": "convalidada",          // o "migrada" — se declara, no se oculta
  "acto_administrativo": "Resolución 0123 de 2026",
  "fecha_convalidacion": "2026-03-14",
  "instancia_convalidante": "Consejo Departamental de Archivos de Cundinamarca",
  "rusd_radicado": "RUSD-2026-00987",
  "snapshot_at": "2026-07-31T10:30:00Z"
}
```

---

## Plan de backfill

Ejecutado **dentro** de la propia migración 037 (arriba), sin scripts fuera de banda. Resumen y estado resultante:

| Población | Estado tras el backfill | Justificación |
|---|---|---|
| Toda fila de `trd_series` | `version=1` (ya lo era), `estado='migrada'`, campos del acto **NULL** | D6 — no se afirma una convalidación que nadie hizo |
| Expediente con serie resoluble | `trd_serie_code` poblado; `trd_serie_id` intacto (pasa a ser el amarre a la v1) | Amarre gratis: la fila actual *es* la versión 1 |
| Expediente cerrado **con** snapshot | Snapshot **intacto**; marca `cerrado_bajo_serie_migrada` | Congelación (D4); la marca se limpia al ratificar |
| Expediente cerrado **sin** snapshot | Marca `cierre_sin_snapshot_de_retencion` | No hay nada que congelar; no se inventa |
| Expediente con `trd_serie_id` huérfano | Marca `serie_no_resoluble`; FK `NOT VALID` no bloquea | La provisión no puede fallar por datos históricos |

**Ninguna operación existente queda bloqueada por el backfill**: `migrada` habilita el cierre. Lo que cambia es que, a partir de aquí, el sistema **declara** bajo qué clase de instrumento actuó.

Seeds (`seeds/fondecund_trd.sql`, ~cientos de INSERT) deben pasar a escribir `estado='migrada'` explícitamente: con el nuevo DEFAULT (`borrador`) las series sembradas no podrían gobernar cierres. Es el mismo criterio de honestidad — datos sembrados no están convalidados.

---

## Orden de incrementos

**Increment 1 — cerrar la fuga retroactiva** (lo mínimo viable; alcance de este ADR):
migración 037 · repositorio append-only (`create_version` en vez de `UPDATE`) · `PATCH` restringido a `borrador` · `POST /trd/{code}/versiones` · congelación + snapshot enriquecido en `close_expediente` · gate de cierre (`convalidada`/`migrada`) · backfill y marcas · asientos `archive.trd_version_creada` / `archive.trd_version_derogada` (mismo patrón atómico mutación↔asiento de `create_trd_serie`) · tests unit + **integración con Postgres real** (la atomicidad y los triggers no se prueban con mocks).

**Increment 2 — circuito de convalidación**:
`aprobar` / `convalidar` / `derogar` con acto administrativo · ratificación `migrada → convalidada` + limpieza de marcas · `GET /trd/revision-pendiente` (informe) · gate **estricto `convalidada`** para la eliminación efectiva, exigible cuando exista esa ruta · `VALIDATE CONSTRAINT` de la FK tras depurar huérfanos · RUSD y publicación.

**Increment 3 — propagación y UI**:
UI de administración TRD versionada (hoy `admin/trd` edita en sitio) · versión + acto en el índice electrónico E15 (**perfil v5**: el XML está firmado XAdES, cambiar su estructura exige perfil nuevo — precedente ADR-022) y en FUID/acta · **re-baselining explícito** por expediente (el único camino legítimo para alterar un calendario ya congelado) · propagación al `disposition` de radicados en document-service.

---

## Impacto en lo existente (para quien implemente)

**No requieren cambio** — resuelven por `get_trd_serie_by_id(exp.trd_serie_id)` y quedan correctos *por construcción* al ser esa columna un puntero a versión inmutable: `compute_retention`; `IndexService._resolve_worm_retention` / `_resolve_ct_retencion_anios`; `TransferenciaService._resolve_worm_retention_acta` / `_resolve_ct_retencion_anios_acta`; `AipWiringService._resolve_worm_retention` / `_resolve_aip_pdfa_profile`; `jobs/indice_reconciliation.py`. **Esta es la propiedad que justifica D3 — conviene verificarla, no asumirla, en la re-auditoría.**

**Requieren cambio o revisión explícita**:

1. `close_expediente` (`archive_service.py:1123-1223`) — re-resolución por código, congelación del pin, gate de estado y snapshot enriquecido. Es el núcleo del Increment 1. Su transacción ya es atómica con el asiento: **no la abra más**; la re-resolución es lectura y va antes, como `compute_retention` hoy.
2. `update_trd_serie` (`archive_repository.py:604-668` + `archive_service.py:1994`) — deja de ser `UPDATE`. **Fichero en edición concurrente por otro agente**: coordinar antes de tocar `archive_service.py` / `routers/trd.py`.
3. **`tipos_documentales.trd_serie_id`** (UNIQUE `(trd_serie_id, code)`) y **`expediente_metadata_templates.trd_serie_id`** (UNIQUE `(trd_serie_id, version)`) apuntan a la **fila**, no al código: una versión nueva los dejaría huérfanos. Decisión: al convalidar la versión N+1, **re-apuntar** (no clonar) esos hijos a la nueva fila **dentro de la misma transacción**, con asiento. No son portadores de retención y su valor histórico es bajo; la alternativa (resolver por código) exige cambiar sus constraints y se descarta para el Increment 1.
4. **Índice electrónico E15** — `index_builder.py` emite `<expediente><serie>` = **UUID** y `<contexto><serie>/<subserie>` = **nombres**; no serializa retención ni disposición. Con versionado, ese UUID pasa a identificar una versión (más preciso, sin cambio de esquema XML). **No añadir `version` al XML en el Increment 1**: el índice se firma XAdES y su perfil es "conforme completo" v4 (ADR-022) — tocar la estructura exige perfil v5 e invalida la comparación con índices ya firmados.
5. **FUID / acta de transferencia** — `_fuid_to_xml` emite `serie_code`, estable entre versiones: **sin impacto**. El acta es un FUID congelado + SHA-256 + XAdES; no se toca.
6. **AIP / PREMIS** — no serializa metadatos TRD; solo recibe `retencion_anios` y `pdfa_profile`. Sin impacto directo, **pero**: `retain_until` es COMPLIANCE e irreversible. Cualquier error en la re-resolución de versión al cerrar se materializa en objetos imborrables. Es el riesgo número uno de este incremento y exige test de integración específico.
7. **Batch** (`batch_service.py:114-118`) — el import hace `INSERT` directo con `trd_serie_id`; con la FK `NOT VALID` un id inexistente ahora **falla**. Debe mapear a una versión válida o marcar `trd_revision_requerida`.
8. **Frontend** (`frontend/src/routes/(app)/admin/trd/`) — el editor hace PATCH en sitio: pasará a recibir `409` sobre series `migrada`. Increment 1 debe, como mínimo, degradar con un mensaje honesto ("esta versión está cerrada; cree una versión nueva"); la UI de versiones es Increment 3. `DisposicionExpedienteDrawer` es aditivo-compatible (lee claves que no cambian) y puede mostrar `serie_estado`/`acto_administrativo` sin cambios de contrato.
9. **Tests y seeds** — todo test que cree una serie y cierre un expediente a continuación romperá con el nuevo DEFAULT `borrador`. Fixture: crear la serie y llevarla a `migrada`/`convalidada` explícitamente. Es una ruptura **buscada**: hace visible en la suite que cerrar bajo un instrumento no convalidado es un acto que debe declararse.
10. **document-service** — `documents.disposition` es JSONB **manual**, sin FK ni cálculo desde `trd_series`. Fuera de alcance; se anota como deuda coherente para el Increment 3.
11. **knowledge-service** — cero referencias a TRD. Sin impacto.

---

## Consecuencias

**Positivas**: el calendario de disposición de un expediente cerrado deja de ser alterable retroactivamente, que es el hallazgo; la eliminación en 2032 podrá exhibir la versión, el acto y la instancia que la fundamentaron; el `retain_until` WORM COMPLIANCE deja de depender de una fila editable; la API deja de afirmar un versionado inexistente; el "switching en caliente" que ADR-015 difirió se resuelve **sin** reclasificación masiva (re-resolución por código en el cierre); el motor —no la convención— impide tanto mutar una versión cerrada como que una serie `migrada` se disfrace de convalidada.

**Negativas / límites**: el `PATCH` de TRD cambia de semántica y rompe flujos de administración y tests existentes (ruptura buscada, pero real); toda serie preexistente queda `migrada` y todo expediente cerrado queda marcado para revisión — un volumen de marcas proporcional al archivo histórico, que solo la ratificación limpia; el circuito completo del Acuerdo 004/2019 (aprobación, convalidación, publicación, RUSD) **no** está en el Increment 1, de modo que entre el 1 y el 2 el sistema sabe declarar honestamente que no hay convalidación pero **no** ofrece todavía la ruta para registrarla; la FK queda `NOT VALID` hasta el Increment 2 (verifica escrituras nuevas, no las históricas); nada de esto acerca al proyecto a la acreditación ONAC ni a un repositorio WORM acreditado — sigue sin estarlo.

**Lo que este ADR NO afirma**: que las TRD existentes estén convalidadas (precisamente lo contrario); que el sistema pueda determinar por sí mismo si lo estuvieron; que la ratificación pueda ser automática. La convalidación es un hecho externo y solo puede entrar declarada por un actor identificable con el acto en la mano.

---

## Alternativas consideradas

- **Dejarlo en la auditoría** (`audit_log` ya registra el before/after del `PATCH`): descartado. La auditoría prueba *qué se cambió*; no permite *calcular con el estado vigente entonces*, porque los nueve puntos de derivación leen la fila viva. Reconstruir un calendario reproduciendo asientos hacia atrás no es un fundamento oponible.
- **Tabla histórica aparte** (`trd_series_historia`, disparada por trigger): descartada. Convierte la versión vigente en "la de verdad" y la historia en archivo muerto; el amarre del expediente seguiría apuntando a una fila mutable. Es tamper-evidence, no versionado.
- **Amarre por `(trd_serie_code, trd_serie_version)`**: descartado como clave de enlace (ver D3). Bifurca el seam de resolución y obliga a editar los nueve puntos, tres de ellos con efecto WORM irreversible. `code` y `version` sí viajan dentro del snapshot.
- **Solo snapshot JSONB, sin versionar la tabla**: descartado. Congela la prueba pero deja la fuente mutable, no da integridad referencial ni permite consultar por serie, y no impide que una serie no convalidada gobierne un cierre.
- **Backfill a `estado='convalidada'`**: descartado por el criterio central del proyecto — el sistema no afirma lo que no verificó (RF-FIR-15). Sería, además, la afirmación falsa que se invocaría para justificar una eliminación futura.
- **Bloquear de entrada todo cierre bajo serie no convalidada**: descartado para el Increment 1. Dejaría a todos los tenants sin poder cerrar expedientes desde el día del despliegue, sin ruta para arreglarlo (los endpoints de convalidación son Increment 2). `migrada` + declaración en el snapshot + marca de revisión da la misma honestidad sin parar la operación.

---

## Relacionados

- [ADR-015](ADR-015-trd-ccd-modelo.md) — modelo TRD/CCD; este ADR **cierra su versionado diferido** y corrige su §5 (que describía un comportamiento que el código nunca tuvo).
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) / [ADR-012](ADR-012-migraciones-por-tenant.md) — la migración corre por tenant; guards por `current_schema()`.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — asyncpg + SQL crudo; sin ORM ni Alembic.
- [ADR-008](ADR-008-auditoria-inmutable.md) / [ADR-010](ADR-010-libreria-compartida.md) — asientos atómicos con la mutación.
- [ADR-022](ADR-022-indice-electronico-v2-metadatos.md) — perfil del índice firmado: por qué no se toca el XML en el Increment 1.
- [ADR-023](ADR-023-worm-object-lock-preservacion.md) — WORM/Object-Lock: la retención derivada de la TRD es **irreversible** en modo COMPLIANCE.
