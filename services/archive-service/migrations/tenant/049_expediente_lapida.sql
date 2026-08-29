-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, RF-TRD-07 fase 5, 2026-08-29): la LÁPIDA, y las
-- ataduras que había que soltar para que sobreviva lo que debe sobrevivir.
--
-- Un expediente eliminado NO deja un hueco: deja una lápida. Sin ella, la única
-- respuesta a «¿qué pasó con el expediente 2015-DEM-000123?» sería el silencio,
-- que es indistinguible de «nunca existió» -- y un archivo que no puede
-- distinguir esas dos cosas ha perdido la traza, aunque haya eliminado bien.
-- La lápida es CONTENT-FREE por construcción: código, serie, fechas extremas,
-- volumen y el acto que lo autorizó. Nada del contenido.
--
-- ── Las tres ataduras ────────────────────────────────────────────────────────
--
-- Al ir a borrar el expediente aparecieron tres claves foráneas que hacían justo
-- lo contrario de lo que hace falta:
--
--   1. `eliminacion_lote_items.expediente_id`  -> BLOQUEA el borrado.
--   2. `disposicion_decisiones.expediente_id`  -> BLOQUEA el borrado.
--   3. `transferencias.expediente_id`          -> ON DELETE CASCADE: se llevaría
--      por delante las transferencias y, en cascada, `transferencia_acta` -- un
--      INSTRUMENTO DE CONTROL que el propio código de storage-service ya declara
--      que debe sobrevivir al contenido como prueba del acto (Ley 594/2000 art.
--      26). Habría sido una pérdida silenciosa: nadie la habría visto ocurrir.
--
-- Las tres se sueltan, y con un solo criterio: **lo que debe sobrevivir a la
-- eliminación conserva el `expediente_id` como referencia, sin clave foránea**
-- -- exactamente como `expediente_eventos` (migración 014) ya lo hacía desde el
-- principio. Después de la eliminación ese identificador ya no apunta a un
-- expediente: apunta a una lápida.
--
-- Y una que va al revés: `expediente_aip` bloqueaba el borrado y no debe. El
-- AIP EMPAQUETA el contenido; con el contenido destruido no describe nada, así
-- que pasa a CASCADE y muere con él, igual que el índice electrónico.

CREATE TABLE IF NOT EXISTS expediente_lapida (
    expediente_id        UUID        NOT NULL PRIMARY KEY,
    -- Identificación mínima, CONTENT-FREE. `code` sí; `name` NO: la
    -- denominación de un expediente es contenido («Historia laboral de …»), y
    -- una lápida que lo conservara filtraría para siempre justo lo que se
    -- destruyó por orden de la TRD.
    expediente_code      TEXT        NOT NULL,
    trd_serie_code       TEXT,
    fecha_extrema_inicial DATE,
    fecha_extrema_final   DATE,
    radicados_eliminados  INTEGER    NOT NULL DEFAULT 0,
    anexos_eliminados     INTEGER    NOT NULL DEFAULT 0,

    -- El acto que lo autorizó: sin esto la lápida diría que algo se destruyó
    -- pero no bajo qué potestad, que es la mitad que importa.
    lote_id              UUID        NOT NULL REFERENCES eliminacion_lotes(id),
    lote_codigo          TEXT        NOT NULL,
    acta_comite          TEXT        NOT NULL,
    acto_administrativo  TEXT        NOT NULL,
    acta_sha256          TEXT        NOT NULL,

    eliminado_por        UUID        NOT NULL,
    eliminado_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT expediente_lapida_sha_forma_check
        CHECK (acta_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE expediente_lapida IS
    'Lápida de un expediente eliminado por disposición final (E04, RF-TRD-07 fase 5) -- CONTENT-FREE y permanente: dice QUE existió y bajo qué acto se destruyó, nunca qué contenía.';

CREATE INDEX IF NOT EXISTS ix_expediente_lapida_code ON expediente_lapida (expediente_code);
CREATE INDEX IF NOT EXISTS ix_expediente_lapida_lote ON expediente_lapida (lote_id);

-- Una lápida no se edita ni se borra: es lo único que queda.
CREATE OR REPLACE FUNCTION reject_expediente_lapida_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'expediente_lapida es permanente y no admite % (E04, RF-TRD-07)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expediente_lapida_append_only ON expediente_lapida;
CREATE TRIGGER trg_expediente_lapida_append_only
    BEFORE UPDATE OR DELETE ON expediente_lapida
    FOR EACH ROW EXECUTE FUNCTION reject_expediente_lapida_mutation();


-- ── Soltar las ataduras ─────────────────────────────────────────────────────
-- Los nombres se resuelven por catálogo y no a mano: una restricción creada sin
-- nombre explícito lo recibe de PostgreSQL, y darlo por sabido es exactamente
-- cómo una migración pasa en un tenant y falla en otro.

DO $$
DECLARE
    r RECORD;
BEGIN
    -- 1 y 2: las que BLOQUEAN y deben soltarse (lo que sobrevive conserva el
    -- identificador sin clave foránea).
    FOR r IN
        SELECT c.conname, c.conrelid::regclass AS tabla
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.contype = 'f'
           AND n.nspname = current_schema()
           AND c.confrelid = (current_schema() || '.expedientes')::regclass
           AND t.relname IN ('eliminacion_lote_items', 'disposicion_decisiones', 'transferencias')
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tabla, r.conname);
    END LOOP;

    -- 3: `expediente_aip` pasa de bloquear a morir con el contenido.
    FOR r IN
        SELECT c.conname, c.conrelid::regclass AS tabla
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.contype = 'f'
           AND n.nspname = current_schema()
           AND c.confrelid = (current_schema() || '.expedientes')::regclass
           AND t.relname = 'expediente_aip'
           AND c.confdeltype <> 'c'   -- 'c' = CASCADE: ya está como debe
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tabla, r.conname);
        EXECUTE format(
            'ALTER TABLE %s ADD CONSTRAINT expediente_aip_expediente_id_fkey '
            'FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE',
            r.tabla
        );
    END LOOP;
END $$;

-- Rollback (manual, no automatizado por el runner):
-- DROP TABLE IF EXISTS expediente_lapida;
-- DROP FUNCTION IF EXISTS reject_expediente_lapida_mutation();
-- (las claves foráneas soltadas NO se restauran: hacerlo volvería a impedir
--  que sobreviva lo que debe sobrevivir.)
