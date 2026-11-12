-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 075 (C-5 / C14; ADR-043, enmienda ADR-030). search_path = tenant_{slug}.
--
-- El anexo que una constancia de «firma inobtenible» VIGENTE cita como soporte no se puede
-- purgar (storage lo consulta en la vista contrato `v_anexo_citado_vigente`). Para que la
-- disposición TRD no se bloquee para siempre, la salida es una BAJA DE SOPORTE avalada por el
-- Comité: al proponer el lote se CONGELAN las constancias vigentes que citan radicados del
-- expediente (`eliminacion_lote_soporte_citado`); la vista deja de bloquear solo cuando esa
-- baja está en un lote `avalado|ejecutado`. La constancia firmada NO se toca (ADR-030).
--
-- Contrato con storage-service: el NOMBRE y las columnas de `v_anexo_citado_vigente`. Hasta que
-- esta migración se aplique, storage trata todo archivo como citado (fail-closed por
-- `to_regclass`): declararlo en el runbook de migraciones.
--
-- Idempotente: IF NOT EXISTS / OR REPLACE / DROP TRIGGER IF EXISTS.

CREATE TABLE IF NOT EXISTS eliminacion_lote_soporte_citado (
    lote_id               UUID        NOT NULL REFERENCES eliminacion_lotes(id) ON DELETE CASCADE,
    -- Sin FK: el expediente desaparece al ejecutar el lote y la baja de soporte es evidencia que
    -- debe sobrevivirle (el inventario del lote conserva su código).
    expediente_id         UUID        NOT NULL,
    excepcion_id          UUID        NOT NULL REFERENCES transferencia_acta_firma_excepcion(id),
    transferencia_id      UUID        NOT NULL,
    rol                   TEXT        NOT NULL,
    ordinal               INTEGER     NOT NULL,
    file_id               UUID        NOT NULL,
    anexo_sha256          TEXT,
    congelado_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    soporte_eliminado_at  TIMESTAMPTZ,
    PRIMARY KEY (lote_id, excepcion_id)
);

CREATE INDEX IF NOT EXISTS ix_lote_soporte_citado_excepcion
    ON eliminacion_lote_soporte_citado (excepcion_id);
CREATE INDEX IF NOT EXISTS ix_lote_soporte_citado_expediente
    ON eliminacion_lote_soporte_citado (lote_id, expediente_id);

COMMENT ON TABLE eliminacion_lote_soporte_citado IS
    'Baja de soporte (ADR-043): constancias de firma inobtenible vigentes cuyo anexo de soporte se destruirá con el lote. Congelada al proponer; el aval del lote la hace efectiva para v_anexo_citado_vigente.';

-- Append-only salvo UN UPDATE admitido: `soporte_eliminado_at` de NULL a un valor. El borrado en
-- cascada del lote (que solo ocurre con el lote) sí pasa; el DELETE directo no.
CREATE OR REPLACE FUNCTION guard_lote_soporte_citado() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1 THEN
            RETURN OLD;  -- cascada desde eliminacion_lotes / expedientes
        END IF;
        RAISE EXCEPTION 'eliminacion_lote_soporte_citado es append-only (ADR-043)';
    END IF;
    IF OLD.soporte_eliminado_at IS NOT NULL
       OR NEW.soporte_eliminado_at IS NULL
       OR (to_jsonb(NEW) - 'soporte_eliminado_at') IS DISTINCT FROM (to_jsonb(OLD) - 'soporte_eliminado_at') THEN
        RAISE EXCEPTION 'eliminacion_lote_soporte_citado: el unico UPDATE admitido fija soporte_eliminado_at (de NULL a un valor)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lote_soporte_citado_guard ON eliminacion_lote_soporte_citado;
CREATE TRIGGER trg_lote_soporte_citado_guard
    BEFORE UPDATE OR DELETE ON eliminacion_lote_soporte_citado
    FOR EACH ROW EXECUTE FUNCTION guard_lote_soporte_citado();

CREATE OR REPLACE FUNCTION reject_lote_soporte_citado_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'eliminacion_lote_soporte_citado es append-only: TRUNCATE denegado';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lote_soporte_citado_no_truncate ON eliminacion_lote_soporte_citado;
CREATE TRIGGER trg_lote_soporte_citado_no_truncate
    BEFORE TRUNCATE ON eliminacion_lote_soporte_citado
    FOR EACH STATEMENT EXECUTE FUNCTION reject_lote_soporte_citado_truncate();

-- Vista CONTRATO con storage-service (ADR-043 / ADR-039 «contrato SQL entre servicios»).
-- Una fila por (anexo, constancia) cuya cita está VIGENTE: no revocada y sin baja de soporte
-- avalada. Un lote `anulado` o aún no avalado NO desbloquea.
CREATE OR REPLACE VIEW v_anexo_citado_vigente AS
SELECT e.referencia_anexo_id AS file_id,
       e.id                  AS excepcion_id,
       e.transferencia_id    AS transferencia_id,
       e.rol                 AS rol,
       e.ordinal             AS ordinal
  FROM transferencia_acta_firma_excepcion e
 WHERE e.referencia_anexo_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion r
                    WHERE r.excepcion_id = e.id)
   AND NOT EXISTS (SELECT 1
                     FROM eliminacion_lote_soporte_citado s
                     JOIN eliminacion_lotes l ON l.id = s.lote_id
                    WHERE s.excepcion_id = e.id
                      AND l.estado IN ('avalado', 'ejecutado'));

COMMENT ON VIEW v_anexo_citado_vigente IS
    'CONTRATO con storage-service (ADR-043): anexos (files.id) citados por una constancia de firma inobtenible vigente. Storage la consulta, en sentencia aparte tras bloquear la fila de files, antes de purgar. No renombrar ni quitar columnas sin migrar storage.';
