-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 007: PINAR — Plan Institucional de Archivos (RF-ADM-08) — E14.
-- Acuerdo AGN 003/2015. OrpycaMCP gestiona el PINAR como instrumento de planeación
-- (lo modela, prioriza, versiona y le da seguimiento) y lo articula por REFERENCIA
-- con CCD/TRD (E04), FUID/IUD (E12/E17) y plan de preservación (E10) — no los copia.
-- search_path = tenant_{slug} (ADR-002/012). SQL crudo, sin ORM (ADR-003).

-- ── Cabecera del plan (paso "Plan" + paso 4 visión estratégica) ───────────────
CREATE TABLE IF NOT EXISTS pinar_plan (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version             INTEGER NOT NULL DEFAULT 1,
    vigencia_desde      DATE,
    vigencia_hasta      DATE,
    vision              TEXT,
    acto_administrativo VARCHAR(200),
    estado              VARCHAR(20) NOT NULL DEFAULT 'borrador'
                        CHECK (estado IN ('borrador','aprobado','en_ejecucion','cerrado')),
    aprobado_por        UUID,
    aprobado_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (vigencia_hasta IS NULL OR vigencia_desde IS NULL OR vigencia_hasta >= vigencia_desde)
);

-- Invariante 1: a lo sumo UN plan en 'en_ejecucion' por tenant (índice único parcial).
CREATE UNIQUE INDEX IF NOT EXISTS ux_pinar_plan_en_ejecucion
    ON pinar_plan (estado) WHERE estado = 'en_ejecucion';

-- ── Paso 1: Diagnóstico → aspectos críticos (con su riesgo) ───────────────────
CREATE TABLE IF NOT EXISTS pinar_aspecto_critico (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     UUID NOT NULL REFERENCES pinar_plan(id) ON DELETE CASCADE,
    descripcion TEXT NOT NULL,
    riesgo      TEXT,
    orden       SMALLINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pinar_aspecto_plan ON pinar_aspecto_critico (plan_id, orden);

-- ── Paso 2: Ejes articuladores (catálogo fijo, seed de 5 — Manual PINAR AGN) ──
CREATE TABLE IF NOT EXISTS pinar_eje (
    code    SMALLINT PRIMARY KEY,
    nombre  VARCHAR(120) NOT NULL
);
INSERT INTO pinar_eje (code, nombre) VALUES
    (1, 'Administración de archivos'),
    (2, 'Acceso a la información'),
    (3, 'Preservación de la información'),
    (4, 'Aspectos tecnológicos y de seguridad'),
    (5, 'Fortalecimiento y articulación')
ON CONFLICT (code) DO NOTHING;

-- ── Paso 3: Priorización (matriz aspecto × eje, impacto 1–10) ─────────────────
CREATE TABLE IF NOT EXISTS pinar_priorizacion (
    aspecto_id  UUID NOT NULL REFERENCES pinar_aspecto_critico(id) ON DELETE CASCADE,
    eje_code    SMALLINT NOT NULL REFERENCES pinar_eje(code),
    impacto     SMALLINT NOT NULL CHECK (impacto BETWEEN 1 AND 10),
    PRIMARY KEY (aspecto_id, eje_code)
);

-- ── Paso 5: Objetivos ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pinar_objetivo (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     UUID NOT NULL REFERENCES pinar_plan(id) ON DELETE CASCADE,
    descripcion TEXT NOT NULL,
    orden       SMALLINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pinar_objetivo_plan ON pinar_objetivo (plan_id, orden);

-- ── Paso 6: Planes y proyectos (meta, indicador, responsable, recurso, tiempos) ─
CREATE TABLE IF NOT EXISTS pinar_proyecto (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id      UUID NOT NULL REFERENCES pinar_plan(id) ON DELETE CASCADE,
    objetivo_id  UUID REFERENCES pinar_objetivo(id) ON DELETE SET NULL,
    nombre       VARCHAR(200) NOT NULL,
    meta         TEXT,
    indicador    TEXT,
    responsable  VARCHAR(200),
    recurso      TEXT,
    fecha_inicio DATE,
    fecha_fin    DATE,
    estado       VARCHAR(20) NOT NULL DEFAULT 'planeado'
                 CHECK (estado IN ('planeado','en_curso','terminado','cancelado')),
    avance_pct   SMALLINT NOT NULL DEFAULT 0 CHECK (avance_pct BETWEEN 0 AND 100),
    CHECK (fecha_fin IS NULL OR fecha_inicio IS NULL OR fecha_fin >= fecha_inicio)
);
CREATE INDEX IF NOT EXISTS ix_pinar_proyecto_plan ON pinar_proyecto (plan_id);
CREATE INDEX IF NOT EXISTS ix_pinar_proyecto_objetivo ON pinar_proyecto (objetivo_id);

-- M:N proyecto ↔ aspectos que mitiga.
CREATE TABLE IF NOT EXISTS pinar_proyecto_aspecto (
    proyecto_id UUID NOT NULL REFERENCES pinar_proyecto(id) ON DELETE CASCADE,
    aspecto_id  UUID NOT NULL REFERENCES pinar_aspecto_critico(id) ON DELETE CASCADE,
    PRIMARY KEY (proyecto_id, aspecto_id)
);

-- ── Herramienta de seguimiento (avance append-only) ──────────────────────────
CREATE TABLE IF NOT EXISTS pinar_seguimiento (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proyecto_id    UUID NOT NULL REFERENCES pinar_proyecto(id) ON DELETE CASCADE,
    fecha          DATE NOT NULL,
    avance_pct     SMALLINT NOT NULL CHECK (avance_pct BETWEEN 0 AND 100),
    observacion    TEXT,
    registrado_por UUID,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pinar_seguimiento_proyecto
    ON pinar_seguimiento (proyecto_id, created_at);

-- Invariante 3: seguimiento append-only, forzado por el motor (no solo procedimental).
CREATE OR REPLACE FUNCTION pinar_seguimiento_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'pinar_seguimiento is append-only (no % permitido)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pinar_seguimiento_no_update ON pinar_seguimiento;
CREATE TRIGGER trg_pinar_seguimiento_no_update
    BEFORE UPDATE OR DELETE ON pinar_seguimiento
    FOR EACH ROW EXECUTE FUNCTION pinar_seguimiento_append_only();

DROP TRIGGER IF EXISTS trg_pinar_seguimiento_no_truncate ON pinar_seguimiento;
CREATE TRIGGER trg_pinar_seguimiento_no_truncate
    BEFORE TRUNCATE ON pinar_seguimiento
    FOR EACH STATEMENT EXECUTE FUNCTION pinar_seguimiento_append_only();

-- ── Articulación con otros instrumentos (por REFERENCIA, no por copia) ─────────
CREATE TABLE IF NOT EXISTS pinar_instrumento_ref (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     UUID NOT NULL REFERENCES pinar_plan(id) ON DELETE CASCADE,
    tipo        VARCHAR(30) NOT NULL
                CHECK (tipo IN ('ccd','trd','fuid','plan_preservacion','plan_contingencia')),
    ref         VARCHAR(200) NOT NULL,
    descripcion TEXT
);
CREATE INDEX IF NOT EXISTS ix_pinar_instrumento_plan ON pinar_instrumento_ref (plan_id);
