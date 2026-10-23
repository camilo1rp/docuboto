-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 072 (C-5 / C11; Ley 594/2000 art. 26 -- cadena de custodia fisica;
-- Ley 1581/2012 -- finalidad de custodia; RF-SEG-08 no-read-up).
--
-- El prestamo de una unidad de conservacion guardaba `prestado_a` / `prestado_por`
-- como TEXTO LIBRE del cliente: nadie quedaba identificado, el clearance del
-- prestatario no se miraba, la comprobacion "ya prestada" iba FUERA del INSERT
-- (dos prestamos simultaneos pasaban) y `ON DELETE CASCADE` desde la unidad
-- borraba la traza de custodia. Ahora:
--   * el prestatario es un usuario interno (`auth_users.id`, validado en el INSERT)
--     o un tercero con identificador minimo (instantanea en la fila);
--   * `prestado_por` / `devuelto_recibido_por` son UUID resueltos por el servidor;
--   * un solo prestamo vivo por arbol de unidades (indice parcial + advisory lock
--     sobre la RAIZ del arbol, tomado por TODOS los actos que sacan o meten);
--   * la traza es append-only: la FK es RESTRICT y el unico UPDATE admitido es
--     `prestado -> devuelto`;
--   * simetria con transferencias: no se transfiere ni se elimina un expediente
--     cuya unidad esta prestada (trigger), ni se presta lo que esta saliendo.
--
-- Entorno de prueba: sin backfill. Si hay filas de `prestamo` con el esquema viejo
-- la migracion ABORTA (se recrea el entorno); jamas inventa un prestatario.
-- Idempotente. search_path = tenant_{slug}.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'prestamo'
                  AND column_name = 'prestado_a') THEN
        IF EXISTS (SELECT 1 FROM prestamo) THEN
            RAISE EXCEPTION 'prestamo_con_filas_del_esquema_viejo: recree el entorno; '
                'no se inventa un prestatario identificado para filas sin el';
        END IF;
        ALTER TABLE prestamo DROP COLUMN prestado_a;
        ALTER TABLE prestamo DROP COLUMN prestado_por;
    END IF;
END $$;

ALTER TABLE prestamo
    ADD COLUMN IF NOT EXISTS prestatario_tipo           TEXT NOT NULL
        CONSTRAINT ck_prestamo_prestatario_tipo CHECK (prestatario_tipo IN ('interno','externo')),
    ADD COLUMN IF NOT EXISTS prestatario_user_id        UUID,
    ADD COLUMN IF NOT EXISTS tercero_nombre             TEXT,
    ADD COLUMN IF NOT EXISTS tercero_documento_tipo     TEXT,
    ADD COLUMN IF NOT EXISTS tercero_documento_numero   TEXT,
    ADD COLUMN IF NOT EXISTS tercero_entidad            TEXT,
    ADD COLUMN IF NOT EXISTS tercero_cargo              TEXT,
    ADD COLUMN IF NOT EXISTS tercero_correo             TEXT,
    ADD COLUMN IF NOT EXISTS tercero_telefono           TEXT,
    ADD COLUMN IF NOT EXISTS prestado_por               UUID NOT NULL,
    ADD COLUMN IF NOT EXISTS devuelto_recibido_por      UUID,
    ADD COLUMN IF NOT EXISTS observaciones              TEXT;

ALTER TABLE prestamo DROP CONSTRAINT IF EXISTS ck_prestamo_prestatario_forma;
ALTER TABLE prestamo ADD CONSTRAINT ck_prestamo_prestatario_forma CHECK (
    (prestatario_tipo = 'interno'
        AND prestatario_user_id IS NOT NULL
        AND tercero_nombre IS NULL AND tercero_documento_tipo IS NULL
        AND tercero_documento_numero IS NULL AND tercero_entidad IS NULL
        AND tercero_cargo IS NULL AND tercero_correo IS NULL AND tercero_telefono IS NULL)
    OR
    (prestatario_tipo = 'externo'
        AND prestatario_user_id IS NULL
        AND tercero_nombre IS NOT NULL AND length(btrim(tercero_nombre)) >= 3
        AND tercero_documento_tipo IS NOT NULL
        AND tercero_documento_numero IS NOT NULL
        AND length(btrim(tercero_documento_numero)) >= 3)
);

ALTER TABLE prestamo DROP CONSTRAINT IF EXISTS ck_prestamo_devolucion_coherente;
ALTER TABLE prestamo ADD CONSTRAINT ck_prestamo_devolucion_coherente CHECK (
    (estado = 'prestado' AND fecha_devolucion_real IS NULL AND devuelto_recibido_por IS NULL)
    OR
    (estado = 'devuelto' AND fecha_devolucion_real IS NOT NULL
        AND devuelto_recibido_por IS NOT NULL)
);

-- Defensa en profundidad del caso "la MISMA unidad": el motor, no solo el servicio.
CREATE UNIQUE INDEX IF NOT EXISTS ux_prestamo_unidad_activo
    ON prestamo (unidad_id) WHERE estado = 'prestado';
CREATE INDEX IF NOT EXISTS ix_prestamo_prestatario_user
    ON prestamo (prestatario_user_id) WHERE prestatario_user_id IS NOT NULL;

-- La traza de custodia no la borra el borrado de la unidad (antes CASCADE).
ALTER TABLE prestamo DROP CONSTRAINT IF EXISTS prestamo_unidad_id_fkey;
ALTER TABLE prestamo ADD CONSTRAINT prestamo_unidad_id_fkey
    FOREIGN KEY (unidad_id) REFERENCES unidad_conservacion(id) ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- Arbol de unidades (caja -> carpeta): helpers STABLE, resueltos por search_path.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION unidad_raiz(p uuid) RETURNS uuid AS $$
    WITH RECURSIVE anc(id, parent_id) AS (
        SELECT id, parent_id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id, u.parent_id FROM unidad_conservacion u JOIN anc a ON u.id = a.parent_id
    )
    SELECT id FROM anc WHERE parent_id IS NULL LIMIT 1
$$ LANGUAGE sql STABLE;

-- La unidad y todos sus descendientes.
CREATE OR REPLACE FUNCTION unidad_arbol(p uuid) RETURNS SETOF uuid AS $$
    WITH RECURSIVE d(id) AS (
        SELECT id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id FROM unidad_conservacion u JOIN d ON u.parent_id = d.id
    )
    SELECT id FROM d
$$ LANGUAGE sql STABLE;

-- Una unidad CON prestamo vivo entre {la unidad, sus ancestros, sus descendientes}
-- (un ancestro prestado se lleva a sus descendientes; un descendiente prestado
-- deja el ancestro incompleto). NULL si no hay ninguna.
CREATE OR REPLACE FUNCTION unidad_con_prestamo_en_arbol(p uuid) RETURNS uuid AS $$
    WITH RECURSIVE anc(id, parent_id) AS (
        SELECT id, parent_id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id, u.parent_id FROM unidad_conservacion u JOIN anc a ON u.id = a.parent_id
    )
    SELECT pr.unidad_id
      FROM prestamo pr
     WHERE pr.estado = 'prestado'
       AND (pr.unidad_id IN (SELECT id FROM anc) OR pr.unidad_id IN (SELECT unidad_arbol(p)))
     LIMIT 1
$$ LANGUAGE sql STABLE;

-- Bloqueo de transaccion por RAIZ del arbol (incluye el schema: sin cruce entre
-- tenants). Lo toman, en SENTENCIA APARTE de la comprobacion, quien presta, quien
-- vincula un expediente y quien lo transfiere/elimina: bajo READ COMMITTED una
-- subconsulta de la misma sentencia usaria la instantanea anterior al bloqueo.
CREATE OR REPLACE FUNCTION unidad_lock_arbol(p uuid) RETURNS void AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('unidad_arbol:' || current_schema() || ':' || COALESCE(unidad_raiz(p), p)::text, 0));
END;
$$ LANGUAGE plpgsql;

-- Unidades que contienen a un expediente (con su raiz bloqueada, en orden estable
-- para no interbloquearse) y, si alguna esta fuera, el CODIGO de la prestada.
CREATE OR REPLACE FUNCTION expediente_unidad_prestada(exp uuid) RETURNS text AS $$
DECLARE
    r uuid;
    prestada uuid;
    cod text;
BEGIN
    FOR r IN
        SELECT DISTINCT COALESCE(unidad_raiz(eu.unidad_id), eu.unidad_id) AS raiz
          FROM expediente_unidad eu WHERE eu.expediente_id = exp ORDER BY 1
    LOOP
        PERFORM unidad_lock_arbol(r);
    END LOOP;
    SELECT unidad_con_prestamo_en_arbol(eu.unidad_id) INTO prestada
      FROM expediente_unidad eu
     WHERE eu.expediente_id = exp AND unidad_con_prestamo_en_arbol(eu.unidad_id) IS NOT NULL
     LIMIT 1;
    IF prestada IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT codigo INTO cod FROM unidad_conservacion WHERE id = prestada;
    RETURN COALESCE(cod, prestada::text);
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

-- INSERT de un prestamo: ultima linea de defensa de las reglas de arbol y de
-- "no prestar lo que esta saliendo" (el servicio las evalua ademas en el WHERE).
CREATE OR REPLACE FUNCTION prestamo_guard_insert() RETURNS trigger AS $$
BEGIN
    IF NEW.estado <> 'prestado' THEN
        RETURN NEW;   -- una fila ya devuelta no saca nada del archivo
    END IF;
    PERFORM unidad_lock_arbol(NEW.unidad_id);
    IF unidad_con_prestamo_en_arbol(NEW.unidad_id) IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_ya_prestada'
            USING ERRCODE = '23505', CONSTRAINT = 'ux_prestamo_arbol_activo';
    END IF;
    IF EXISTS (SELECT 1 FROM transferencias t
                 JOIN expediente_unidad eu ON eu.expediente_id = t.expediente_id
                WHERE t.estado IN ('preparada','enviada')
                  AND eu.unidad_id IN (SELECT unidad_arbol(NEW.unidad_id))) THEN
        RAISE EXCEPTION 'unidad_en_transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_unidad_en_transferencia';
    END IF;
    IF EXISTS (SELECT 1 FROM eliminacion_lote_items i
                 JOIN eliminacion_lotes l ON l.id = i.lote_id
                 JOIN expediente_unidad eu ON eu.expediente_id = i.expediente_id
                WHERE l.estado IN ('borrador','publicado','avalado')
                  AND eu.unidad_id IN (SELECT unidad_arbol(NEW.unidad_id))) THEN
        RAISE EXCEPTION 'unidad_en_lote_eliminacion'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_unidad_en_lote';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prestamo_guard_insert ON prestamo;
CREATE TRIGGER trg_prestamo_guard_insert
    BEFORE INSERT ON prestamo
    FOR EACH ROW EXECUTE FUNCTION prestamo_guard_insert();

-- Append-only: el UNICO UPDATE admitido es prestado -> devuelto, y solo mueve
-- estado, fecha real y quien recibe. Nada se borra.
CREATE OR REPLACE FUNCTION prestamo_guard_mutacion() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prestamo_no_se_borra'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
    END IF;
    IF OLD.estado = 'prestado' AND NEW.estado = 'devuelto'
       AND (NEW.id, NEW.unidad_id, NEW.prestatario_tipo, NEW.prestatario_user_id,
            NEW.tercero_nombre, NEW.tercero_documento_tipo, NEW.tercero_documento_numero,
            NEW.tercero_entidad, NEW.tercero_cargo, NEW.tercero_correo, NEW.tercero_telefono,
            NEW.prestado_por, NEW.fecha_prestamo, NEW.fecha_devolucion_esperada,
            NEW.observaciones, NEW.created_at)
           IS NOT DISTINCT FROM
           (OLD.id, OLD.unidad_id, OLD.prestatario_tipo, OLD.prestatario_user_id,
            OLD.tercero_nombre, OLD.tercero_documento_tipo, OLD.tercero_documento_numero,
            OLD.tercero_entidad, OLD.tercero_cargo, OLD.tercero_correo, OLD.tercero_telefono,
            OLD.prestado_por, OLD.fecha_prestamo, OLD.fecha_devolucion_esperada,
            OLD.observaciones, OLD.created_at) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'prestamo_solo_se_devuelve'
        USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prestamo_guard_mutacion ON prestamo;
CREATE TRIGGER trg_prestamo_guard_mutacion
    BEFORE UPDATE OR DELETE ON prestamo
    FOR EACH ROW EXECUTE FUNCTION prestamo_guard_mutacion();

CREATE OR REPLACE FUNCTION prestamo_guard_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'prestamo_no_se_borra'
        USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prestamo_guard_truncate ON prestamo;
CREATE TRIGGER trg_prestamo_guard_truncate
    BEFORE TRUNCATE ON prestamo
    FOR EACH STATEMENT EXECUTE FUNCTION prestamo_guard_truncate();

-- No SACAR lo que esta fuera: una transferencia preparada/enviada de un expediente
-- cuya unidad esta prestada se rechaza (el servicio lo dice antes; esto es el motor).
CREATE OR REPLACE FUNCTION transferencia_guard_unidad_prestada() RETURNS trigger AS $$
DECLARE
    cod text;
BEGIN
    cod := expediente_unidad_prestada(NEW.expediente_id);
    IF cod IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_prestada: %', cod
            USING ERRCODE = '23514', CONSTRAINT = 'ck_transferencia_unidad_prestada';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transferencia_unidad_prestada ON transferencias;
CREATE TRIGGER trg_transferencia_unidad_prestada
    BEFORE INSERT OR UPDATE OF estado ON transferencias
    FOR EACH ROW WHEN (NEW.estado IN ('preparada','enviada'))
    EXECUTE FUNCTION transferencia_guard_unidad_prestada();

-- Ultima defensa: no se borra un expediente que esta (fisicamente) fuera.
CREATE OR REPLACE FUNCTION expediente_guard_unidad_prestada() RETURNS trigger AS $$
DECLARE
    cod text;
BEGIN
    cod := expediente_unidad_prestada(OLD.id);
    IF cod IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_prestada: %', cod
            USING ERRCODE = '23514', CONSTRAINT = 'ck_expediente_unidad_prestada';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expediente_unidad_prestada ON expedientes;
CREATE TRIGGER trg_expediente_unidad_prestada
    BEFORE DELETE ON expedientes
    FOR EACH ROW EXECUTE FUNCTION expediente_guard_unidad_prestada();

-- Rollback manual (no automatizado): DROP de los triggers y funciones anteriores,
-- de ux_prestamo_unidad_activo y de las columnas nuevas; NO restaura prestado_a.
