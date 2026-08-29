-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, RF-TRD-06/08, 2026-08-28): la DECISIÓN sobre un
-- vencimiento de disposición, APPEND-ONLY.
--
-- «Ninguna destrucción es automática sin confirmación de usuario autorizado»
-- (RF-TRD-08, regla de negocio 4 del RT 04). Esa confirmación es un ACTO, no
-- un estado que se pueda editar después: quien la firma responde de que la
-- disposición de la TRD procede sobre ESE expediente. Por eso la tabla es
-- append-only con el mismo trigger que `expediente_eventos`, y por eso la
-- decisión se guarda con el vencimiento CONGELADO sobre el que se decidió
-- (`fecha_vencimiento` + `disposition_code`): decidir «sobre el vencimiento
-- vigente» sería decidir sobre un blanco móvil, y una confirmación de 2026 no
-- puede autorizar la eliminación que una TRD reformulada ordene en 2030.
--
-- Dos decisiones posibles, las dos NO destructivas:
--   * `confirmada`: el usuario autorizado declara que procede la acción de la
--     TRD. NO elimina nada -- el brazo ejecutor (RF-TRD-07: borrado seguro con
--     acta, aval del Comité y publicidad previa del inventario) no existe
--     todavía. Es su precondición, no su disparo.
--   * `aplazada`: R.4.9 del Modelo SGDEA, «cancelar transferencia o
--     destrucción vencida». Exige motivo y una fecha para volver a mirarlo --
--     un aplazamiento sin fecha es un olvido con mejor nombre.

CREATE TABLE IF NOT EXISTS disposicion_decisiones (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    expediente_id      UUID         NOT NULL REFERENCES expedientes(id),
    fase               TEXT         NOT NULL,
    decision           TEXT         NOT NULL,
    -- El vencimiento sobre el que se decidió, congelado (ver cabecera).
    fecha_vencimiento  DATE         NOT NULL,
    disposition_code   TEXT,
    -- Obligatorio al aplazar; opcional al confirmar (una confirmación puede
    -- remitir al acta del Comité que la respalda).
    motivo             TEXT,
    revisar_de_nuevo_el DATE,
    decidido_por       UUID         NOT NULL,
    decidido_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT disposicion_decisiones_fase_check
        CHECK (fase IN ('gestion', 'central')),
    CONSTRAINT disposicion_decisiones_decision_check
        CHECK (decision IN ('confirmada', 'aplazada')),
    -- Un aplazamiento sin motivo y sin fecha de revisión no es una decisión,
    -- es dejar el expediente donde estaba y quitarlo de la vista.
    CONSTRAINT disposicion_decisiones_aplazada_forma_check
        CHECK (
            decision <> 'aplazada'
            OR (motivo IS NOT NULL AND length(btrim(motivo)) >= 10
                AND revisar_de_nuevo_el IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS ix_disposicion_decisiones_exp
    ON disposicion_decisiones (expediente_id, fase, fecha_vencimiento, decidido_at DESC);

-- Inmutabilidad: rechaza UPDATE y DELETE (append-only, Ley 594/2000) — mismo
-- criterio y misma forma que `expediente_eventos` (migración 014).
CREATE OR REPLACE FUNCTION reject_disposicion_decisiones_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'disposicion_decisiones is append-only (Ley 594/2000)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_disposicion_decisiones_immutable ON disposicion_decisiones;
CREATE TRIGGER trg_disposicion_decisiones_immutable
    BEFORE UPDATE OR DELETE ON disposicion_decisiones
    FOR EACH ROW EXECUTE FUNCTION reject_disposicion_decisiones_mutation();
