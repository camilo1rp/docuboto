# ADR-026 — La TVD es el mismo instrumento con otro contenido: discriminador sobre `trd_series`, no tabla propia

**Estado**: **Implementado (Increment 1)** — diseño de `orfeo-architect` a petición del usuario, tras el hallazgo de `archival-compliance-auditor` ("todo el modelo es exclusivamente TRD; una entidad con fondo acumulado no tiene dónde poner sus TVD"). La migración 039 está aplicada (`services/archive-service/migrations/tenant/039_tvd_instrumento_valoracion.sql`), el circuito de registro/convalidación de TVD (`/api/v1/tvd`) y el gemelo `GET /api/v1/{trd,tvd}/{code}/versiones` están implementados y probados (unit + integración, dos schemas de tenant, CHECK/vistas/trigger por mutación). El **Increment 2 está implementado** (migración 041): `expedientes.origen` + fechas extremas con CHECK bidireccional, trigger de la Capa 4 relajado a `origen↔tipo_instrumento` (ni `corriente→TVD` ni `fondo_acumulado→TRD`, probado por mutación en ambos sentidos), `compute_retention(serie_id, base_date, *, base_date_origen)` con el parámetro **keyword-only y sin default** —imposible invocarlo sin declarar la procedencia de la fecha—, snapshot con `base_date`/`base_date_origen` siempre, y los **seis resolutores WORM fail-closed** para fondo acumulado (hueco que este ADR no había previsto: al habilitarse el cierre, habrían calculado `retain_until` desde `closed_at`, que es el daño de D4 por otra puerta). 721 tests unit + 168 de integración, incluido el de fechas históricas que D4 exige. **Increment 3 — parte no-WORM implementada (2026-08-07)**: `origen` + fechas extremas en el import masivo (con la regla de forma D4 extraída a `validar_forma_fondo_acumulado`, compartida por la ruta unitaria y la masiva) y UI del selector TRD↔TVD condicionado a `origen` en `/expedientes` (catálogos separados, aviso de calendario ya vencido, enlace a `/admin/tvd` cuando no hay agrupaciones). **Increment 3 — clamp y cableado WORM implementados (2026-10, T12, AREA-06-04)**: los seis resolutores ya no devuelven `None` para `origen='fondo_acumulado'`. `retention_math.base_worm` devuelve como `fecha_inicio` la `fecha_extrema_final` del fondo con el clamp de D4.4, `retain_until = max(hoy + margen, fecha_extrema_final + total)` (el margen es `WORM_FONDO_ACUMULADO_MARGEN_DIAS`, 400 por defecto: **debe superar** el piso de storage, 365, o un día de bisiesto dejaría la fecha por debajo y storage la rechazaría); los tres de renovación (ventana desde hoy) simplemente dejan de excluir al fondo. Probado contra Postgres real con fechas históricas (un fondo de 1985 con 5 años de retención acaba en `hoy + margen`, no en 1990 ni en `closed_at + 5`). **Desviación declarada de D4.4**: el texto original dice «si `retain_until <= now()` no se fija retención Object-Lock»; se prefirió el clamp porque es lo que el plan de remediación fijó y mantiene el índice, el acta y el AIP del fondo bajo el mismo régimen que los demás —**el costo es un COMPLIANCE irreversible de ~400 días sobre documentación cuyo calendario ya venció**, decisión a confirmar por el usuario/`archival-compliance-auditor`—. **Pendiente**: el gate de publicación previa del inventario (§D5, brecha declarada, bloqueada a la espera de artículo y plazo).

**Fecha**: 2026-08-01

**Contexto normativo**: Ley 594/2000 arts. 24-26 (obligatoriedad de los instrumentos de retención y valoración); **Acuerdo AGN 001/2024** (Acuerdo Único de la Función Archivística), que **compila** el derogado **Acuerdo AGN 004/2019** — y que regula **TRD y TVD con el mismo circuito**: elaboración, aprobación por el Comité Institucional de Gestión y Desempeño, **convalidación** por el Consejo Departamental/Distrital de Archivos, publicación e inscripción en el **RUSD**. El régimen de **organización de fondos acumulados** (históricamente Acuerdo AGN 002/2004) queda igualmente recogido en la compilación; **la correspondencia exacta de artículos dentro del 001/2024 debe confirmarla `archival-compliance-auditor` antes de citarla en mensajes de error o en la UI** — este ADR no la afirma.

---

## Contexto

[ADR-025](ADR-025-trd-instrumento-convalidado-versionado.md) convirtió la TRD en **instrumento convalidado**: versiones append-only, máquina de estados `borrador → aprobada → convalidada` (+ `devolver`, `derogar`, ratificación `migrada → convalidada`, registro RUSD posterior), trigger de inmutabilidad a nivel de motor, congelación de la versión en el expediente al cerrar, y gate D5 de disposición final. Dos incrementos y dos auditorías (seguridad + conformidad, migraciones 037 y 038).

Toda esa maquinaria está atada a **un solo instrumento**. La TVD no existe: ni tabla, ni estado, ni endpoint, ni informe. Una entidad con **fondo acumulado** —documentación ya producida, acumulada sin criterio archivístico, típicamente de dependencias suprimidas o de entidades liquidadas— no tiene dónde registrar el instrumento que la valora, y por tanto **no puede cerrar ni disponer nada de ese fondo** dentro del sistema: el gate D5 de ADR-025 exige un instrumento convalidado que no tiene forma de existir.

### Qué comparten y en qué se diferencian, exactamente

Esta es la tabla que decide el ADR. No es retórica: cada fila es una pieza concreta del código actual.

| Pieza | TRD | TVD | ¿Idéntica? |
|---|---|---|---|
| Circuito Comité → Consejo → RUSD → publicación | sí | sí | **Idéntico** (Ac. AGN 001/2024, que compila el 004/2019) |
| Estados `borrador/aprobada/convalidada/derogada` | sí | sí | **Idéntico** |
| Append-only + acto administrativo inmutable | sí | sí | **Idéntico** |
| Gate D5 de disposición final | sí | sí | **Idéntico** (ver D3, abajo) |
| Jerarquía `parent_id` | serie → subserie | sección → subsección → serie del fondo | **Mismo mecanismo**, otra semántica |
| Disposición final (CT/E/S/M) | sí | sí | **Idéntico** |
| Fase de **archivo de gestión** | sí | **no existe** (ya está en central/histórico) | **Distinto** |
| Fecha base del calendario | fecha de **cierre** del expediente | **fecha extrema final** de la unidad | **Distinto y peligroso** (§D4) |
| Fechas extremas del fondo | no aplica | obligatorias | **Distinto** |
| Fondo/productora | implícita (la entidad) | explícita, a menudo **extinta** | **Distinto** |
| Justificación de la valoración | no se exige por fila | **es el objeto del instrumento** | **Distinto** |
| Población preexistente | cientos de filas `migrada` | **ninguna** | **Distinto** (a favor de TVD) |

Lo que se comparte es el **circuito**; lo que difiere es el **contenido valorado**. Cualquier decisión que no separe esas dos capas discute la pregunta equivocada.

---

## Decisión

**TVD se modela como discriminador `tipo_instrumento ∈ {'TRD','TVD'}` sobre `trd_series`.** No hay tabla propia. El aislamiento entre instrumentos —que es la objeción legítima al discriminador— **lo sostiene el motor** en cuatro capas (D2), no la disciplina de quien escriba la próxima consulta.

Se pidió una recomendación, no un menú. Es esta, y estos son los dos argumentos que la deciden.

### D1 — Por qué discriminador: la razón es el amarre, y es la misma razón de ADR-025 D3

`expedientes.trd_serie_id` es el **seam único** por el que **nueve** puntos derivan consecuencias jurídicas (ADR-025 D3), **siete de ellos alimentando `retain_until` en MinIO modo COMPLIANCE**, que es **irreversible**. ADR-025 introdujo además una **FK real** de esa columna a `trd_series(id)`.

Ahora aplíquese la pregunta: **un expediente de fondo acumulado, ¿a qué apunta?**

- **Con discriminador**: a una fila de `trd_series` cuyo `tipo_instrumento='TVD'`. La FK sigue siendo una FK. Los nueve puntos siguen resolviendo `get_trd_serie_by_id(exp.trd_serie_id)`, obtienen una fila con `total_retention`, `disposition` y `pdfa_profile`, y **quedan correctos sin tocarse**. Radio de impacto sobre las rutas WORM: **cero**.
- **Con tabla propia**: la FK **no puede** apuntar a dos tablas. Solo hay dos salidas, y ambas son peores que el problema que resuelven:
  1. **Columna nueva** (`tvd_agrupacion_id`): bifurca el seam y obliga a editar los nueve puntos —incluidos los siete irreversibles— para preguntar "¿cuál de las dos columnas está poblada?". Es *exactamente* el diseño que ADR-025 D3 descartó, con el mismo argumento, hace un incremento.
  2. **Puntero polimórfico** (`trd_serie_id` + `tipo`): **destruye la FK** que ADR-025 acaba de introducir. Volvería a ser posible borrar la versión que gobierna un expediente cerrado — el defecto que ese ADR cerró.

La objeción del usuario ("no son el mismo objeto") es archivísticamente correcta y **no** es la que gobierna aquí. En este servicio, la métrica que decide no es la pureza del modelado sino el **riesgo asimétrico de la irreversibilidad WORM**: "editar N sitios" no es trabajo, es N oportunidades de producir objetos imborrables durante décadas. Un modelo más limpio que exija tocar siete escrituras COMPLIANCE es un modelo peor.

### D2 — Las cuatro capas que sostiene el motor (respuesta a "un filtro olvidado mezcla instrumentos")

La objeción es real y este proyecto ya la ha sufrido. No se responde con "acuérdense de filtrar". Se responde así:

**Capa 1 — Identidad: el espacio de códigos es ÚNICO y COMPARTIDO.** Los índices de ADR-025 (`ux_trd_series_vigente(code)`, `ux_trd_series_en_tramite(code)`, `UNIQUE(code, version)`) **no se tocan**. Consecuencia: **un `code` identifica a lo sumo un instrumento, de un solo tipo**. Toda resolución por código —incluida `get_trd_serie_vigente_by_code` dentro de `close_expediente`, que es la que escribe el pin que alimenta WORM— sigue siendo correcta **sin filtro de tipo alguno**, porque la colisión es *estructuralmente imposible*: intentar registrar una TVD con un código ya usado por una TRD viola el índice único.

Esto es lo que desactiva la objeción en el único sitio donde equivocarse es irreversible. El precio es que la entidad no puede reutilizar el mismo código en TRD y TVD. Eso no es una limitación: las agrupaciones de un fondo acumulado proceden de cuadros de clasificación **anteriores o ajenos** (dependencia suprimida, entidad liquidada) y darles identificadores distintos de la TRD vigente es la práctica correcta. La API debe detectarlo antes de reventar y devolver `409 instrumento_code_en_uso` diciendo **de qué tipo** es el ocupante.

**Capa 2 — Enumeración: dos vistas, y los repositorios leen la vista, nunca la tabla.**

```sql
v_trd_series        AS SELECT * FROM trd_series WHERE tipo_instrumento = 'TRD' WITH CHECK OPTION
v_tvd_agrupaciones  AS SELECT * FROM trd_series WHERE tipo_instrumento = 'TVD' WITH CHECK OPTION
```

`list_trd_series` (hoy `SELECT * FROM trd_series WHERE 1=1`) pasa a `FROM v_trd_series`. Un filtro no se puede *olvidar* cuando no es un filtro sino el nombre del objeto consultado: la consulta o dice `v_trd_series` o no compila contra nada útil. Es la misma consulta, con el aislamiento movido del `WHERE` (opcional, olvidable) al `FROM` (obligatorio, visible en la primera línea).

`WITH CHECK OPTION` cierra el reverso: un `INSERT` a través de `v_tvd_agrupaciones` que se dejara `tipo_instrumento` caería al `DEFAULT 'TRD'` y sería **rechazado** en vez de crear una TRD disfrazada.

**Capa 3 — Forma: CHECK por tipo, en ambas direcciones.** Ni "la mitad de las columnas en NULL por convención", ni un tipo pudiendo vestirse del otro:

- `TVD` **exige** `fondo_nombre`, `fecha_extrema_inicial`, `fecha_extrema_final` (con `final >= inicial`) y `justificacion_valoracion`, y **exige** `COALESCE(archivo_gestion_years,0) = 0` — la TVD no tiene fase de gestión, y eso deja de ser un comentario para ser una restricción.
- `TRD` **exige** que esas cuatro columnas sean `NULL`.

Una fila mal formada no existe. Las columnas NULL de una TRD no son "columnas huérfanas": son una **negación declarada y verificada**.

**Capa 4 — Amarre: un trigger impide que un expediente se clasifique bajo el instrumento equivocado** (§D3). Es la capa que impide el error archivístico de fondo: clasificar producción corriente bajo una TVD.

Añádase, fuera del motor pero igual de visible: **rutas de API separadas** (`/api/v1/trd` vs `/api/v1/tvd`, §"Superficie de API") con `tipo_instrumento` **fijado por el router**, nunca aceptado del cliente; y **nombres de asiento de auditoría distintos** (`archive.tvd_*`). Nótese que en la auditoría la decisión es la **contraria** a la del modelo de datos, y a propósito: `audit_log` es una narración para humanos y peritos, y ahí quien filtra `archive.trd_version_convalidada` **no debe** tener que acordarse de mirar además un campo del payload.

**Coste que se acepta y no se disimula**: la tabla se seguirá llamando `trd_series` y contendrá TVD. Renombrarla exigiría tocar los nueve puntos, los triggers, la FK y las migraciones 037/038 — el mismo coste que D1 se niega a pagar. Se mitiga con los nombres de las vistas, que sí describen lo que contienen, y con este ADR.

### D3 — Amarre del expediente: **la misma columna**, con un trigger que impide cruzarlos

> `expedientes.trd_serie_id` amarra tanto TRD como TVD. **Ambos amarres conviven en el tenant; nunca en el mismo expediente.** Se añade `expedientes.origen ∈ {'corriente','fondo_acumulado'}` (default `'corriente'`) y un **trigger** que exige que el `tipo_instrumento` de la fila apuntada **corresponda** al `origen` del expediente.

- `origen='corriente'` → solo puede apuntar a `TRD`.
- `origen='fondo_acumulado'` → solo puede apuntar a `TVD`.

Un CHECK no basta (es una condición entre tablas), así que es un trigger `BEFORE INSERT OR UPDATE` sobre `expedientes`. En el **Increment 1**, cuando `origen` todavía no existe, ese mismo trigger se instala en su forma **estricta**: *rechaza cualquier pin a una fila `TVD`*. Así el Increment 1 puede registrar y convalidar TVD sin que exista todavía ninguna ruta —ni siquiera accidental, ni vía `batch`— por la que una TVD llegue a gobernar un cálculo de retención. **El Increment 1 tiene riesgo WORM cero, por construcción del motor y no por omisión.**

**Congelación**: idéntica, sin código nuevo. El expediente de fondo acumulado se registra y se **cierra** (un fondo acumulado ya está producido; no tiene archivo de gestión que recorrer), y `close_expediente` hace lo de siempre: re-resuelve por código la versión vigente en ese instante, escribe el pin —última escritura de esa columna en su vida— y materializa el snapshot. `trg_expedientes_trd_pin_immutable` lo congela. Reutilizar `close_expediente` íntegro, en vez de escribir una ruta de ingesta paralela, es deliberado: es donde viven el gate D5, el snapshot y la congelación, y una segunda ruta sería una segunda copia que diverge.

### D4 — El punto peligroso: **la fecha base del calendario**, y es irreversible

Este es el hallazgo que hay que leer dos veces antes de implementar el Increment 2.

`compute_retention(serie_id, closed_at)` calcula el calendario desde `closed_at`. Para una TRD eso es correcto: el expediente se cierra cuando deja de producirse. **Para un fondo acumulado es catastróficamente incorrecto.** Un expediente cuyos documentos son de 1985, registrado hoy, se cerraría con `closed_at = 2026-08-01`; su `fin_archivo_central` caería en 2046 y ese valor alimentaría `retain_until` **COMPLIANCE**. Resultado: documentación cuya retención se agotó hace décadas quedaría **imborrable durante veinte años más**, sin ninguna forma de deshacerlo. Es exactamente la clase de daño que ADR-025 se escribió para evitar, entrando por una puerta nueva.

Decisión:

1. `compute_retention` gana un parámetro **explícito** de fecha base. Para `origen='fondo_acumulado'` la base es la **`fecha_extrema_final` del expediente** (columna nueva, `NOT NULL` bajo CHECK cuando `origen='fondo_acumulado'`), **nunca** `closed_at`.
2. La fase de archivo de gestión es **0** para TVD (ya garantizado por el CHECK de la Capa 3).
3. El snapshot congelado declara **cuál fue la base y por qué** (`base_date`, `base_date_origen: "fecha_extrema_final"`). Igual que `serie_estado`, la declaración viaja con la prueba.
4. **Calendario ya vencido**: es el caso *normal* en fondo acumulado, no un error. Si el calculado `fin_archivo_central` es anterior a hoy, el expediente queda **listo para disposición final desde su registro** — y eso debe verse tal cual, no redondearse hacia adelante. Corolario operativo: si `retain_until <= now()`, **no se fija retención Object-Lock**; no hay periodo futuro que proteger y MinIO rechazaría la fecha. Es una decisión de la ruta AIP/WORM (Increment 3) que aquí se declara para que no se improvise.

Este punto **exige** test de integración contra Postgres real con fechas históricas antes de habilitar cualquier ruta WORM para fondo acumulado.

### D5 — Gate de disposición final: **aplica igual y no cambia una línea**

`assert_disposicion_final_autorizada` (`app/core/trd_disposition.py`) ya recibe **solo** `serie_estado` y `acto_administrativo` **del snapshot congelado**, no la fila viva. Es, sin haberlo pretendido, **agnóstico al instrumento**. Con el discriminador **no requiere ningún cambio**: una TVD `convalidada` autoriza igual que una TRD `convalidada`; una TVD `migrada` no puede existir (§"Backfill"), así que la excepción D6 sobra sin necesidad de excluirla.

(Adviértase el contraste: con tabla propia este gate habría necesitado un segundo *caller* con su propia resolución de snapshot — otra copia que diverge.)

**Qué sí cambia en la eliminación de fondo acumulado**, y es de conformidad, no de código:

- El **fundamento** de la eliminación no es la retención cumplida sino la **valoración**. Por eso `justificacion_valoracion` es obligatoria en TVD y **debe viajar al snapshot** (`tipo_instrumento`, `justificacion_valoracion`): es lo que se citaría en el acta de eliminación en 2032, y si no está congelado no es oponible.
- La eliminación documental exige, además del acto convalidado, la **publicación previa del inventario** de lo que se va a eliminar durante un plazo de ley. **Este ADR no fija ese plazo ni lo implementa**: `archival-compliance-auditor` debe determinar el artículo y el término exactos dentro del Ac. AGN 001/2024 antes de que se escriba ese gate. Queda registrado como **brecha declarada**, no como resuelta — no hay hoy ningún job de eliminación efectiva (ADR-025 D5), así que no hay nada incumpliéndolo todavía, y sí habría si se implementara sin ese requisito.

---

## Modelo de datos — migración 039 (**propuesta, NO aplicada**)

`services/archive-service/migrations/tenant/039_tvd_instrumento_valoracion.sql`. Idempotente y con **todos los guards acotados a `current_schema()`** (lección de las migraciones 016/017/019/035, reparadas por la 036: un guard que filtra por nombre de objeto sin schema **omite silenciosamente** el objeto en el tenant #2 y siguientes).

```sql
-- (cabecera de licencia AGPL estándar del proyecto — omitida aquí por brevedad)

-- Migración de tenant (ADR-026): la TVD entra como DISCRIMINADOR sobre
-- `trd_series`, no como tabla propia (ADR-026 D1: `expedientes.trd_serie_id`
-- es el seam único de nueve puntos de derivación, siete de ellos alimentando
-- `retain_until` WORM COMPLIANCE — irreversible).
--
-- INVARIANTE CENTRAL: el espacio de `code` es ÚNICO Y COMPARTIDO entre TRD y
-- TVD. Los índices de la 037 (`ux_trd_series_vigente`, `ux_trd_series_en_
-- tramite`, `UNIQUE(code,version)`) NO se tocan, y por eso toda resolución
-- por código sigue siendo correcta SIN filtro de tipo. Si algún día se
-- particiona ese espacio por tipo, hay que revisar `get_trd_serie_vigente_by_
-- code` (la llamada de `close_expediente` que escribe el pin WORM) ANTES.

-- ---------------------------------------------------------------------------
-- 1. Discriminador y contenido propio de la TVD
-- ---------------------------------------------------------------------------

-- DEFAULT 'TRD' rellena las filas EXISTENTES con un hecho VERIFICABLE (toda
-- fila anterior a esta migración es, por construcción, una TRD) y se QUEDA
-- como default permanente: los seeds y todo el código TRD existente siguen
-- funcionando sin tocarse. Contraste deliberado con la 037, donde el default
-- de `estado` SÍ tuvo que invertirse: allí el backfill habría afirmado un
-- hecho jurídico no verificado; aquí describe la forma de la fila.
ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS tipo_instrumento         VARCHAR(3) NOT NULL DEFAULT 'TRD',
    ADD COLUMN IF NOT EXISTS fondo_nombre             TEXT,
    ADD COLUMN IF NOT EXISTS fecha_extrema_inicial    DATE,
    ADD COLUMN IF NOT EXISTS fecha_extrema_final      DATE,
    ADD COLUMN IF NOT EXISTS justificacion_valoracion TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_tipo_instrumento_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_tipo_instrumento_check
            CHECK (tipo_instrumento IN ('TRD','TVD'));
    END IF;

    -- Capa 3 (ADR-026 D2), dirección TVD: la TVD EXIGE su contenido propio y
    -- NO tiene fase de archivo de gestión (ya está en central/histórico).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_tvd_forma_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_tvd_forma_check
            CHECK (tipo_instrumento <> 'TVD' OR (
                fondo_nombre             IS NOT NULL AND
                justificacion_valoracion IS NOT NULL AND
                fecha_extrema_inicial    IS NOT NULL AND
                fecha_extrema_final      IS NOT NULL AND
                fecha_extrema_final     >= fecha_extrema_inicial AND
                COALESCE(archivo_gestion_years, 0) = 0));
    END IF;

    -- Capa 3, dirección TRD: una TRD no puede vestirse de TVD. Las columnas
    -- NULL de una TRD son una NEGACIÓN DECLARADA, no columnas huérfanas.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_trd_forma_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_trd_forma_check
            CHECK (tipo_instrumento <> 'TRD' OR (
                fondo_nombre             IS NULL AND
                justificacion_valoracion IS NULL AND
                fecha_extrema_inicial    IS NULL AND
                fecha_extrema_final      IS NULL));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_trd_series_tipo_code ON trd_series (tipo_instrumento, code);

-- ---------------------------------------------------------------------------
-- 2. Capa 2 — vistas: el aislamiento se mueve del WHERE (olvidable) al FROM
-- ---------------------------------------------------------------------------
-- DROP + CREATE, no `CREATE OR REPLACE`: con `SELECT *` un REPLACE falla en
-- cuanto una migración futura añada una columna a `trd_series`. Las vistas se
-- resuelven por search_path => son per-schema sin necesidad de guard.
DROP VIEW IF EXISTS v_trd_series;
CREATE VIEW v_trd_series AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TRD'
    WITH CHECK OPTION;

DROP VIEW IF EXISTS v_tvd_agrupaciones;
CREATE VIEW v_tvd_agrupaciones AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TVD'
    WITH CHECK OPTION;

-- ---------------------------------------------------------------------------
-- 3. Inmutabilidad: el tipo y el contenido valorado son append-only
-- ---------------------------------------------------------------------------
-- Extiende `reject_trd_version_mutation` (037/038). Dos reglas nuevas:
--  (a) `tipo_instrumento` es inmutable en TODO estado, incluido `borrador`:
--      un borrador que cambia de instrumento invalidaría el pin de cualquier
--      expediente y el criterio de las vistas. Un cambio de tipo es una fila
--      nueva, no una edición.
--  (b) las cuatro columnas de contenido TVD entran en la lista de campos
--      sustantivos inmutables fuera de `borrador`, junto a retención y
--      disposición: son el FUNDAMENTO de la eliminación (D5).
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 001/2024, que compila el 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    -- (a) ADR-026: inmutable SIEMPRE, sin excepcion de estado.
    IF NEW.tipo_instrumento IS DISTINCT FROM OLD.tipo_instrumento THEN
        RAISE EXCEPTION 'trd_series: `tipo_instrumento` es inmutable (codigo %, version %) — TRD y TVD no se convierten entre si (ADR-026)',
            OLD.code, OLD.version;
    END IF;

    IF OLD.estado <> 'borrador' THEN
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
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from
        -- (b) ADR-026: contenido valorado de la TVD.
        OR NEW.fondo_nombre             IS DISTINCT FROM OLD.fondo_nombre
        OR NEW.justificacion_valoracion IS DISTINCT FROM OLD.justificacion_valoracion
        OR NEW.fecha_extrema_inicial    IS DISTINCT FROM OLD.fecha_extrema_inicial
        OR NEW.fecha_extrema_final      IS DISTINCT FROM OLD.fecha_extrema_final THEN
            RAISE EXCEPTION 'trd_series: contenido inmutable en estado % (codigo %, version %) — una modificacion sustantiva crea una version nueva (ADR-025)',
                OLD.estado, OLD.code, OLD.version;
        END IF;
    END IF;

    -- (038) desde `convalidada` el unico destino de `estado` es `derogada`;
    -- acto/fecha/instancia inmutables; rusd/publicacion fill-once.
    -- >>> Al implementar: CONSERVAR ÍNTEGRO el cuerpo vigente de la 038 aquí.
    --     Este bloque se reproduce abreviado a propósito; la migración real
    --     debe copiar el texto exacto instalado por la 038 y añadirle (a)/(b).
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- El trigger `trg_trd_series_append_only` (037) ya apunta a esta funcion:
-- CREATE OR REPLACE FUNCTION basta, no hay que recrear el trigger.

-- ---------------------------------------------------------------------------
-- 4. Coherencia de la jerarquia: un padre no cruza de instrumento
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION assert_trd_parent_mismo_tipo() RETURNS trigger AS $$
DECLARE
    parent_tipo VARCHAR(3);
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        SELECT tipo_instrumento INTO parent_tipo FROM trd_series WHERE id = NEW.parent_id;
        IF parent_tipo IS DISTINCT FROM NEW.tipo_instrumento THEN
            RAISE EXCEPTION 'trd_series: el padre % es de tipo % y la fila es de tipo % — la jerarquia no cruza instrumentos (ADR-026 D2)',
                NEW.parent_id, COALESCE(parent_tipo,'(inexistente)'), NEW.tipo_instrumento;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_parent_tipo ON trd_series;
CREATE TRIGGER trg_trd_series_parent_tipo
    BEFORE INSERT OR UPDATE ON trd_series
    FOR EACH ROW EXECUTE FUNCTION assert_trd_parent_mismo_tipo();

-- ---------------------------------------------------------------------------
-- 5. Capa 4 — el expediente no se clasifica bajo el instrumento equivocado
-- ---------------------------------------------------------------------------
-- Forma ESTRICTA del Increment 1: `origen` todavia no existe, asi que NINGUN
-- expediente puede apuntar a una TVD. Consecuencia buscada: el Increment 1
-- registra y convalida TVD con riesgo WORM CERO, garantizado por el motor —
-- ninguna ruta (incluido `batch_service`) puede colar una TVD en el calculo
-- de retencion antes de que exista `compute_retention` con fecha base
-- correcta (ADR-026 D4). El Increment 2 RELAJA este trigger a la regla
-- `origen <-> tipo_instrumento`; no lo elimina.
CREATE OR REPLACE FUNCTION assert_expediente_pin_tipo_instrumento() RETURNS trigger AS $$
DECLARE
    pin_tipo VARCHAR(3);
BEGIN
    IF NEW.trd_serie_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id) THEN
        SELECT tipo_instrumento INTO pin_tipo FROM trd_series WHERE id = NEW.trd_serie_id;
        IF pin_tipo = 'TVD' THEN
            RAISE EXCEPTION 'expedientes: la agrupacion TVD % no puede gobernar un expediente todavia — el amarre de fondo acumulado llega en el Increment 2 (ADR-026 D3/D4)',
                NEW.trd_serie_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expedientes_pin_tipo ON expedientes;
CREATE TRIGGER trg_expedientes_pin_tipo
    BEFORE INSERT OR UPDATE ON expedientes
    FOR EACH ROW EXECUTE FUNCTION assert_expediente_pin_tipo_instrumento();

-- Rollback (referencia manual, no ejecutado por el runner):
--   DROP TRIGGER IF EXISTS trg_expedientes_pin_tipo ON expedientes;
--   DROP TRIGGER IF EXISTS trg_trd_series_parent_tipo ON trd_series;
--   DROP VIEW IF EXISTS v_trd_series, v_tvd_agrupaciones;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_tvd_forma_check, ...;
--   (restaurar `reject_trd_version_mutation` al cuerpo instalado por la 038)
--   (el rollback NO puede ejecutarse si ya existe alguna fila tipo_instrumento='TVD')
```

**Validación exigida antes de integrar** (contenedor Postgres 15 desechable, CLAUDE.md): dos schemas de tenant, migración aplicada **en ambos, en orden**, comprobando que el tenant #2 obtiene *todos* los objetos —incluidas las **dos vistas**, que es el objeto nuevo y por tanto el candidato a repetir el fallo histórico—; re-aplicación no-op; y **verificación por mutación**, una por capa:

| Mutación | Debe |
|---|---|
| `INSERT` TVD sin `justificacion_valoracion` | fallar (Capa 3) |
| `INSERT` TVD con `archivo_gestion_years = 2` | fallar (Capa 3) |
| `INSERT` TRD con `fecha_extrema_final` | fallar (Capa 3) |
| `INSERT` TVD con un `code` ya usado por una TRD vigente | fallar (Capa 1, `ux_trd_series_vigente`) |
| `UPDATE trd_series SET tipo_instrumento='TVD'` sobre una fila `borrador` | fallar (trigger, regla (a)) |
| `INSERT INTO v_tvd_agrupaciones` sin `tipo_instrumento` | fallar (`WITH CHECK OPTION`) |
| `SELECT count(*) FROM v_trd_series` con TVD sembradas | no contarlas (Capa 2) |
| `INSERT`/`UPDATE` de `expedientes` con `trd_serie_id` de una TVD | fallar (Capa 4) |
| `UPDATE trd_series SET retention_years=99` sobre `borrador` | **pasar** (no se rompió la 037) |

---

## Máquina de estados

**La misma, sin una transición nueva.** Es el argumento entero de este ADR condensado en una frase: el Ac. AGN 001/2024 (que compila el 004/2019) somete TRD y TVD al mismo circuito, así que el diagrama de ADR-025 §"Máquina de estados" se aplica literal a `tipo_instrumento='TVD'`, con `aprobar` / `devolver` / `convalidar` / `registrar-rusd` / `derogar` idénticos.

Dos diferencias, ambas por ausencia y ambas a favor:

- **`migrada` no se usa nunca en TVD.** Ese estado existe porque había cientos de filas TRD preexistentes cuya convalidación el sistema no podía verificar (ADR-025 D6). La población TVD nace vacía: **toda** TVD entra por `borrador` y recorre el circuito completo. Consecuencia: ninguna TVD puede gobernar un cierre sin estar convalidada, y **ninguna marca `trd_revision_requerida` se genera por TVD**. La TVD arranca en el estado de conformidad al que la TRD solo llegará ratificando.
- **`derogar` con expedientes abiertos** (guarda del hallazgo #3 de la 038): en fondo acumulado casi no hay expedientes `open` —se registran ya cerrados—, así que la guarda casi nunca se activa. Se conserva sin cambios: es la misma consulta y su coste es nulo.

---

## Superficie de API

Rutas **separadas**, `tipo_instrumento` fijado por el router (nunca aceptado del cliente), espejo 1:1 de las de TRD para que ninguna de las dos superficies pueda evolucionar sin que la asimetría salte a la vista en el diff.

| Método y ruta | Incremento | Notas |
|---|---|---|
| `POST /api/v1/tvd` | 1 | Crea agrupación **versión 1 en `borrador`**. Body: `code`, `name`, `description?`, `parent_id?`, `fondo_nombre`, `fecha_extrema_inicial`, `fecha_extrema_final`, `justificacion_valoracion`, `archivo_central_years`, `total_retention`, `disposition`, `pdfa_profile?`. **No** acepta `archivo_gestion_years`, `estado` ni `tipo_instrumento`. `409 instrumento_code_en_uso` si el código ya pertenece a otro instrumento (Capa 1), indicando el tipo del ocupante. |
| `GET /api/v1/tvd?page=&size=&is_active=&historico=` | 1 | Lee `v_tvd_agrupaciones`. Solo vigentes salvo `historico=true`. `X-Total-Count`. |
| `GET /api/v1/tvd/{id}` | 1 | `404` si el id existe pero es TRD (lee la vista: no "se filtra", no está). |
| `PATCH /api/v1/tvd/{id}` | 1 | Solo `borrador` (+ `pdfa_profile` siempre), igual que TRD. |
| `POST /api/v1/tvd/{code}/versiones` | 1 | Clona la vigente como `borrador` `version+1`. |
| `GET /api/v1/tvd/{code}/versiones` | 1 | Historia completa. **Escribir el gemelo `GET /api/v1/trd/{code}/versiones`, hoy inexistente** (ADR-025 lo declaró aditivo y no se implementó): la pantalla de TVD lo necesita desde el día uno y dejar la TRD sin él consolidaría la asimetría. |
| `POST /api/v1/tvd/{code}/versiones/{version}/aprobar` | 1 | Body `{acta_comite, fecha_aprobacion_comite}`. |
| `.../devolver` | 1 | Body `{motivo}`. |
| `.../convalidar` | 1 | Body `{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}`. Deroga la vigente previa en la misma transacción. |
| `.../registrar-rusd` | 1 | Body `{rusd_radicado?, fecha_publicacion?}` (al menos uno). |
| `.../derogar` | 1 | Sin body. |
| `GET /api/v1/tvd/{id}/retention?base_date=` | **2** | `base_date` **obligatoria y explícita** — sin default a hoy (D4). |
| `POST /api/v1/expedientes` con `origen=fondo_acumulado` | **2** | Exige `fecha_extrema_inicial/final` y `trd_serie_id` de una TVD. |
| `GET /api/v1/trd/revision-pendiente` | **2** | `ExpedienteRevisionPendienteResponse` gana `tipo_instrumento` (aditivo) + filtro opcional `?tipo=`. **No tocar el filtro no-read-up de clearance** que la 038 introdujo: es el hallazgo `[Alta, BLOQUEANTE]` de esa auditoría y cualquier reescritura de esa consulta debe conservarlo como primera condición del `WHERE`, compartido por `COUNT` y página. |

**Permiso**: se **reutiliza `USUA_PERM_TRD`** (con `min_crud=3` en las mutaciones, igual que TRD). Es una decisión, no un descuido: la potestad sobre los instrumentos archivísticos es del mismo cargo y del mismo Comité, y crear `USUA_PERM_TVD` obligaría a una migración de RBAC en auth-service y a tocar los mapas de rol del frontend por una distinción que la norma no hace. Si una entidad quisiera separarlas, se añade después sin romper nada.

**Gateway**: `services/api-gateway/app/routers/proxy.py` debe registrar `("/api/v1/tvd/", app_state.archive_service_url)` junto a la entrada de `/api/v1/trd/` (línea 68). Sin eso el frontend recibe 404 y el síntoma no apunta al gateway.

**Snapshot** (`RetentionSchedule` → `expedientes.disposition`), aditivo sobre lo que ADR-025 ya congela:

```jsonc
{
  // … claves de ADR-025 (serie_id, code, serie_version, serie_estado,
  //    acto_administrativo, fecha_convalidacion, instancia_convalidante, …)
  "tipo_instrumento": "TVD",
  "fondo_nombre": "Fondo Instituto Departamental de X (liquidado 2003)",
  "justificacion_valoracion": "Valoración secundaria: …",   // fundamento de la eliminación (D5)
  "base_date": "1989-12-31",
  "base_date_origen": "fecha_extrema_final",                 // nunca "closed_at" en TVD (D4)
  "archivo_gestion_years": 0
}
```

Para TRD, `tipo_instrumento: "TRD"` y `base_date_origen: "closed_at"` — **también** se escriben, explícitamente. Un snapshot que solo declara la base cuando es la rara no permite distinguir "es la fecha de cierre" de "alguien olvidó escribirlo".

---

## Qué necesita el frontend

- **Pantalla nueva `/(app)/admin/tvd`**, espejo de `/(app)/admin/trd` (`+page.svelte`, `+page.server.js`, `api/+server.js`, `api/[id]/+server.js`), con las rutas BFF proxy hacia `/api/v1/tvd`. Columnas propias en la tabla: **fondo**, **fechas extremas**, **estado del instrumento**, **acto**. Sin columna de archivo de gestión — no existe, y mostrarla vacía sugiere que falta un dato.
- **Formulario de creación**: `justificacion_valoracion` como `textarea` obligatorio y visualmente principal, no como campo secundario: es el objeto del instrumento, no una nota.
- **Acciones del circuito** (aprobar / devolver / convalidar / registrar RUSD / derogar) con la misma UI que TRD; si esa UI todavía no existe en `/admin/trd` (ADR-025 la dejó en su Increment 3), **construirla una vez como componente compartido** parametrizado por instrumento, no dos veces.
- **Entrada de navegación** en `/admin` y en el mapa de permisos, gateada por `USUA_PERM_TRD`.
- **`TrdSuggestChips.svelte`** (`frontend/src/lib/components/features/rag/`) — sugerencia de serie para clasificar. **Es el punto exacto donde la mezcla de instrumentos haría daño visible**: nunca debe proponer una agrupación TVD para producción corriente. Queda cubierto automáticamente **si y solo si** su backend consume `GET /api/v1/trd` (que lee `v_trd_series`); si resolviera contra `trd_series` por su cuenta, hay que corregirlo. **Verificar, no asumir.**
- **`DisposicionExpedienteDrawer.svelte`** — aditivo-compatible; puede mostrar `tipo_instrumento`, `fondo_nombre` y `base_date_origen` sin cambio de contrato. Mostrar `base_date_origen` es recomendable: es la explicación de por qué un expediente de fondo acumulado aparece vencido desde el primer día.
- **Selector de serie al crear expediente** (Increment 2): debe ofrecer TRD **o** TVD según `origen`, y `origen` debe elegirse **antes** que la serie. Si el usuario puede elegir serie primero, el trigger de la Capa 4 devolverá un 500/409 tardío en vez de una UI que nunca ofrece la opción inválida.

---

## Plan de migración / backfill

**No hay backfill.** Toda fila preexistente es una TRD y el `DEFAULT 'TRD'` lo dice sin afirmar nada que no sea verificable — a diferencia de ADR-025 D6, donde el default sí codificaba una afirmación jurídica y por eso hubo que elegir `migrada`. Aquí el discriminador describe la **forma** de la fila, no un acto administrativo.

Consecuencias directas:

- **Seeds intactos**: `seeds/fondecund_trd.sql` y compañía no cambian una línea.
- **Ninguna marca de revisión nueva.**
- **Ninguna ruptura de contrato**: `TrdSerieResponse` gana campos (aditivo) y `list_trd_series` devuelve exactamente lo mismo que antes.
- **Ninguna población TVD inicial**: la entidad la crea. Un fondo acumulado no se puede sembrar.

---

## Orden de incrementos

**Increment 1 — registro y convalidación de TVD (alcance mínimo viable, lo que el usuario pidió).** Migración 039 · vistas + repositorios leyendo `v_trd_series` / `v_tvd_agrupaciones` · router `/api/v1/tvd` completo (circuito entero, que ya existe en el servicio y aquí solo se parametriza) · ruta en el gateway · `GET /{code}/versiones` para **ambos** instrumentos · asientos `archive.tvd_*` · pantalla `/admin/tvd` · tests unit + **integración con Postgres real** (las nueve mutaciones de la tabla de validación; los triggers y las vistas no se prueban con mocks).

Propiedad que define este incremento: **una TVD todavía no gobierna nada**. El trigger de la Capa 4 lo garantiza en el motor. La entidad puede registrar, aprobar, convalidar, inscribir en RUSD y publicar su TVD — que es exactamente el bloqueo del hallazgo— con **riesgo WORM cero**.

**Increment 2 — amarre y calendario del fondo acumulado.** `expedientes.origen` + `fecha_extrema_inicial/final` + CHECK · trigger de la Capa 4 relajado a `origen ↔ tipo_instrumento` · `compute_retention` con **fecha base explícita** (D4) · `close_expediente` re-resolviendo igual · snapshot con `tipo_instrumento`/`justificacion_valoracion`/`base_date` · `revision-pendiente` con `tipo_instrumento` · **tests de integración con fechas históricas antes de habilitar cualquier ruta WORM**.

**Increment 3 — disposición y preservación del fondo acumulado.** Clamp de `retain_until` vencido (D4.4) · gate de publicación previa del inventario de eliminación, **una vez que `archival-compliance-auditor` fije artículo y plazo** · propagación a informes y export.

---

## Impacto en lo existente

**No requieren cambio** (y esta es la propiedad que justifica D1 — **verificarla en la re-auditoría, no asumirla**): `compute_retention` resolviendo por id; `IndexService._resolve_worm_retention` / `_resolve_ct_retencion_anios`; `TransferenciaService._resolve_worm_retention_acta` / `_resolve_ct_retencion_anios_acta`; `AipWiringService._resolve_worm_retention` / `_resolve_aip_pdfa_profile`; `jobs/indice_reconciliation.py`; `assert_disposicion_final_autorizada` (D5); `get_trd_serie_vigente_by_code` y `get_trd_serie_ultima_by_code` (Capa 1); `count_expedientes_open_por_trd_serie_code`.

**Requieren cambio o revisión explícita**:

1. **`list_trd_series`** (`archive_repository.py:592`) — pasa a `FROM v_trd_series`. Es **la** consulta de la clase "filtro olvidado" en el código actual (`WHERE 1=1` sobre la tabla base). Cambio de una palabra; sin él, todo lo demás sobra.
2. **`ÍNDICE ELECTRÓNICO E15 — NO TOCAR.`** `index_builder.py` emite `<expediente><serie>` = UUID y `<contexto><serie>/<subserie>` = nombres. Un expediente amarrado a una TVD serializa la agrupación **en esos mismos elementos, sin cambio de esquema XML**. El índice está firmado **XAdES** y su perfil es "conforme completo" **v4** (ADR-022): añadir `tipo_instrumento` o el fondo al XML exigiría **perfil v5** e invalidaría la comparación con índices ya firmados. **No se hace en ningún incremento de este ADR.** Que la TVD quepa en el perfil v4 sin tocarlo es, por sí solo, un argumento a favor del discriminador.
3. **FUID / acta de transferencia** — `_fuid_to_xml` emite `serie_code`, estable entre versiones y entre instrumentos: **sin impacto**. El acta es un FUID congelado + SHA-256 + XAdES; no se toca. (La transferencia de un fondo acumulado es el caso de uso clásico del FUID y funciona tal cual.)
4. **AIP / PREMIS** — recibe `retencion_anios` y `pdfa_profile` de la fila apuntada; funciona sin cambio. **Pero** `retain_until` es COMPLIANCE e irreversible: el riesgo real no está aquí sino en la fecha base (D4). Increment 3 debe además decidir el clamp del `retain_until` ya vencido, que en fondo acumulado es el caso **normal**.
5. **`batch_service.py:114-118`** — el import hace `INSERT` directo con `trd_serie_id`. Con el trigger de la Capa 4, un import que traiga una TVD **falla en el Increment 1** (buscado) y debe declarar `origen` a partir del Increment 2. Documentar el nuevo modo de fallo.
6. **`tipos_documentales.trd_serie_id` y `expediente_metadata_templates.trd_serie_id`** — el re-apunte al convalidar (038, con exclusión de colisiones y `409 trd_repoint_colision`) funciona igual para TVD sin cambios. Tienen sentido archivístico en un fondo acumulado.
7. **`GET /trd/revision-pendiente`** — mezclaría marcas de ambos instrumentos. Ninguna TVD las genera (no hay `migrada`), así que en la práctica solo hay marcas TRD; aun así el campo `tipo_instrumento` debe ir en la respuesta para que el informe sea legible cuando eso cambie. **Conservar íntegro el filtro no-read-up de clearance de la 038.**
8. **mcp-server, tool `listar_trd`** (`assistant.py:136`) — consume `GET /api/v1/trd` a través del gateway: queda correcto por la Capa 2, sin tocarlo. Añadir una tool `listar_tvd` es opcional (Increment 3); si se añade, el asistente sigue siendo solo-lectura.
9. **Frontend `TrdSuggestChips`** — ver §"Qué necesita el frontend". **Verificar la fuente de datos**, es el único punto donde una mezcla sería visible para el usuario final y archivísticamente incorrecta.
10. **document-service (`schemas/batch.py`) y storage-service (`schemas/preservacion.py`)** — mencionan `trd_serie` como identificador opaco; sin impacto. `documents.disposition` sigue siendo JSONB manual (deuda declarada en ADR-025, sin cambio aquí).
11. **Tests** — ningún test existente debería romper (el default `'TRD'` conserva el comportamiento). Si alguno rompe, es que resolvía contra la tabla base algo que debía resolver contra la vista: **es un hallazgo, no un test que arreglar**.
12. **knowledge-service** — cero referencias a TRD. Sin impacto.

---

## Consecuencias

**Positivas**: una entidad con fondo acumulado puede registrar, aprobar, convalidar, inscribir en RUSD y publicar su TVD, que era el bloqueo; el circuito del Ac. AGN 001/2024 (que compila el 004/2019) existe **una sola vez** en el código y en el motor, de modo que las dos copias no pueden divergir porque no hay dos copias; el gate D5, el índice E15 firmado, el FUID y el acta funcionan sin tocarse; el amarre WORM-crítico no se bifurca; la TVD nace con conformidad plena (sin `migrada`, sin marcas de revisión); el aislamiento entre instrumentos lo sostienen un índice único, dos vistas, cuatro CHECK y tres triggers, no un recordatorio.

**Negativas / límites**: la tabla se llama `trd_series` y contiene TVD — un desajuste de nombre permanente que solo mitigan las vistas y este documento; TRD y TVD comparten espacio de códigos, así que la entidad no puede reutilizar un código entre instrumentos (deliberado, pero es una restricción real que la UI debe explicar bien); `USUA_PERM_TRD` gobierna ambos, de modo que quien administra la TRD administra la TVD; el Increment 1 deja la TVD **sin poder gobernar ningún expediente**, lo cual es honesto pero significa que entre el 1 y el 2 el fondo acumulado se puede *valorar* y todavía no *gestionar*; el cálculo del calendario sobre fecha extrema (D4) es la pieza de mayor riesgo del diseño y no se cierra hasta el Increment 2; el requisito de publicación previa del inventario de eliminación queda **declarado y no resuelto**, a la espera de que `archival-compliance-auditor` fije artículo y plazo; y nada de esto acerca al proyecto a la acreditación ONAC ni a un repositorio WORM acreditado — sigue sin estarlo.

**Lo que este ADR NO afirma**: que el modelo de contenido de la TVD aquí propuesto agote los requisitos del Ac. AGN 001/2024 para fondos acumulados (falta contrastar la correspondencia de artículos de la compilación); que la mera existencia de una TVD convalidada autorice eliminar (falta la publicación del inventario); que un fondo acumulado registrado quede correctamente calendarizado antes del Increment 2 (no puede: el motor lo impide a propósito).

---

## Alternativas consideradas

- **Tabla propia `tvd_agrupaciones` con su propio circuito**: descartada por D1. Obliga a bifurcar `expedientes.trd_serie_id` —el seam de nueve derivaciones, siete con efecto WORM **irreversible**— o a destruir la FK que ADR-025 acaba de introducir. Además duplicaría la máquina de estados, el trigger de inmutabilidad, los cinco CHECK de acto/estado y el gate D5, garantizando la divergencia que el propio planteamiento del problema teme. El argumento archivístico ("no son el mismo objeto") es correcto y no compensa siete oportunidades de producir objetos imborrables.
- **Tabla propia + sustrato común `instrumentos_version`** (extraer estado/acto a una tabla genérica con las tablas de contenido apuntando a ella): descartada. Es el modelo más limpio de los tres y exige **refactorizar en caliente** `trd_series` — una tabla con triggers, FK entrante desde `expedientes`, snapshots ya congelados en producción y siete lectores WORM. El coste de migración es desproporcionado frente a un discriminador y dos vistas, y el riesgo se concentra justo donde es irreversible. Si alguna vez aparece un **tercer** instrumento con este mismo circuito (p. ej. un CCD versionado), reevaluar: con tres instrumentos la balanza cambia.
- **Discriminador con espacio de códigos particionado por tipo** (`UNIQUE (tipo_instrumento, code, version)` y partial indexes sobre `(tipo_instrumento, code)`): descartada, y es la alternativa más tentadora. Permitiría reutilizar códigos entre instrumentos, a cambio de que **toda** resolución por código necesitara acordarse del tipo — incluida `get_trd_serie_vigente_by_code` dentro de `close_expediente`, la que escribe el pin que alimenta WORM. Reintroduce por la puerta de atrás exactamente el fallo "un filtro olvidado" que este diseño debe eliminar, y lo reintroduce en el único sitio donde equivocarse no se puede deshacer. El espacio compartido de códigos cuesta una restricción de nomenclatura y compra imposibilidad estructural de confusión.
- **Reutilizar `/api/v1/trd?tipo=TVD`** en vez de rutas separadas: descartada. Convierte el tipo en un parámetro **del cliente** —un parámetro que se puede omitir, y cuya omisión devuelve la mezcla— cuando debe ser una constante del servidor. Con rutas separadas, `tipo_instrumento` no viaja nunca por la red.
- **Reutilizar los nombres de asiento `archive.trd_*` para TVD** añadiendo un campo al payload: descartada. En el modelo de datos compartir la fila es lo correcto; en `audit_log` compartir el nombre obligaría a un perito o a un auditor a recordar un filtro adicional, que es precisamente lo que este ADR se propone hacer innecesario.

---

## Addendum (2026-10-20) — C-4, [ADR-040](ADR-040-cuadro-de-clasificacion-derivado-y-fases-de-la-trd.md)

- **La TVD exige su fase central** (`TvdAgrupacionCreate.archivo_central_years`, obligatorio y sin valor por
  omisión; CHECK `trd_series_fases_check`): antes `total_retention` valía 10 por omisión mientras
  `archivo_central_years` quedaba `NULL`, el calendario se calculaba con 0 y la pantalla mostraba 10 años.
  `total_retention` sale de la escritura (`422 extra_forbidden`); la respuesta lo deriva (la TVD no tiene gestión,
  así que su total es su fase central).
- El cuerpo del trigger de inmutabilidad que este ADR cita pierde las dos líneas del juego legado (ver ADR-025).
- **Retracción**: este ADR decía «Seeds intactos» (`seeds/fondecund_trd.sql` «no cambia una línea»). Ya no es cierto: los seeds y `scripts/migrate_trd_from_fondecund.py` escriben las dos fases (y el script, la dependencia productora desde `dependencia.codigo`).
- La TVD **no** lleva dependencia productora (tiene `fondo_nombre`) ni entra en el gate de aprobar/convalidar.

## Relacionados

- [ADR-025](ADR-025-trd-instrumento-convalidado-versionado.md) — instrumento convalidado y versionado; **este ADR extiende su modelo a un segundo instrumento sin duplicarlo**, y reutiliza su criterio de decisión (D3: radio de impacto sobre el seam, irreversibilidad WORM).
- [ADR-015](ADR-015-trd-ccd-modelo.md) — modelo TRD/CCD de contenido.
- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) / [ADR-012](ADR-012-migraciones-por-tenant.md) — la migración corre por tenant; guards por `current_schema()`, y las **vistas** son el objeto nuevo a verificar en el tenant #2.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — asyncpg + SQL crudo; sin ORM ni Alembic.
- [ADR-008](ADR-008-auditoria-inmutable.md) / [ADR-010](ADR-010-libreria-compartida.md) — asientos atómicos con la mutación; nombres `archive.tvd_*` distintos a propósito.
- [ADR-022](ADR-022-indice-electronico-v2-metadatos.md) — perfil v4 firmado: por qué la TVD **no** toca el XML del índice.
- [ADR-023](ADR-023-worm-object-lock-preservacion.md) — Object-Lock COMPLIANCE: por qué la fecha base del calendario (D4) es el punto de mayor riesgo de este diseño.

---

## Enmienda C-5 (2026-10-21, [ADR-043](ADR-043-retencion-legal-por-motivo-prestamo-identificado-y-soporte-citado.md) §2)

Se retira el clamp de D4.4 (`max(hoy + margen, fecha_extrema_final + total)` como COMPLIANCE): dejaba un régimen irreversible de ~400 días sobre contenido cuyo calendario ya había vencido. Para el contenido (`indice`, `aip`) de un fondo acumulado con calendario vencido ahora solo se fija el legal hold `valoracion_pendiente`, sin retención, hasta que el Comité registre la valoración de la TVD (`POST /tvd/{serie_id}/valoracion`). Con calendario futuro, COMPLIANCE natural. Las actas de transferencia toman como base el acto (`firmado_at`). El margen queda solo como umbral del piso de storage (`worm_piso_margen_dias`). Costo declarado: sin red WORM irreversible para contenido vencido, compensada por el hold.
