-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04, RF-TRD-07 fase 4, 2026-08-29): el ACTA de eliminación.
--
-- El acta es **lo que sobrevive a lo eliminado**. Cuando la fase 5 borre los
-- bytes, el expediente dejará de existir y esta fila será, junto con la lápida
-- y el `audit_log`, la única constancia de qué se destruyó, bajo qué acto y
-- quién lo autorizó. Es de **conservación permanente**: no tiene calendario de
-- retención ni disposición final -- no se elimina jamás.
--
-- Por eso son DOS tablas y no una, exactamente como el acta de transferencia
-- (migraciones 026/027):
--
--   * `eliminacion_acta`       -> el CONTENIDO congelado. Append-only por
--                                 trigger: ni se edita ni se borra.
--   * `eliminacion_acta_firma` -> el ESTADO MUTABLE del sellado XAdES, que
--                                 avanza y reintenta.
--
-- Mezclarlas obligaría a que el contenido fuera mutable para que el sellado
-- pudiera avanzar, y el contenido es justo lo que no puede moverse: su
-- SHA-256 queda anclado en `audit_log` en la misma transacción que lo escribe.

CREATE TABLE IF NOT EXISTS eliminacion_acta (
    lote_id          UUID        NOT NULL PRIMARY KEY
                     REFERENCES eliminacion_lotes(id) ON DELETE CASCADE,
    acta_xml         TEXT        NOT NULL,
    -- SHA-256 del XML tal cual se congeló. Se ancla además en `audit_log`, que
    -- es indeleble: un acta alterada saltándose el trigger (solo un
    -- superusuario podría) seguiría delatándose contra ese asiento.
    acta_sha256      TEXT        NOT NULL,
    -- Declaración de la entidad, no del sistema: el acta debe decir CÓMO se
    -- destruye (Ac. AGN 001/2024 art. 4.3.2.5). Se exige al generarla en vez
    -- de inventar una frase por defecto -- un método de borrado inventado por
    -- el sistema sería una afirmación que nadie hizo.
    metodo_borrado   TEXT        NOT NULL,
    observaciones    TEXT,
    generado_por     UUID        NOT NULL,
    generado_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT eliminacion_acta_sha_forma_check
        CHECK (acta_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT eliminacion_acta_metodo_forma_check
        CHECK (length(btrim(metodo_borrado)) >= 10)
);

COMMENT ON TABLE eliminacion_acta IS
    'Acta de eliminación documental (E04, RF-TRD-07 fase 4) -- CONSERVACION PERMANENTE: sobrevive a lo eliminado y no se elimina jamás. Append-only por trigger.';

-- El acta no se edita ni se borra. Un acta que se pudiera reescribir no
-- serviría para lo único que sirve: dar fe de una destrucción irreversible
-- cuando ya no queda nada con qué contrastarla.
CREATE OR REPLACE FUNCTION reject_eliminacion_acta_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'eliminacion_acta es de conservación permanente y no admite % (E04, RF-TRD-07)',
        TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Consecuencia DELIBERADA, verificada en Postgres real: con el acta escrita,
-- la fila del lote **ya no se puede borrar** -- el `ON DELETE CASCADE` intenta
-- llevarse el acta y el trigger aborta la operación entera. Es lo que se
-- quiere: un lote deja de ser desechable en cuanto existe el documento que da
-- fe de lo que se destruyó. (Nada borra lotes hoy; `anular` es un estado.)
DROP TRIGGER IF EXISTS trg_eliminacion_acta_append_only ON eliminacion_acta;
CREATE TRIGGER trg_eliminacion_acta_append_only
    BEFORE UPDATE OR DELETE ON eliminacion_acta
    FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_acta_mutation();


-- Estado MUTABLE del sellado, 1:1 con el acta. Gemela de
-- `transferencia_acta_firma` (migración 026), con las mismas columnas de
-- backoff para que la reconciliación sea el mismo patrón y no uno nuevo.
CREATE TABLE IF NOT EXISTS eliminacion_acta_firma (
    lote_id              UUID        NOT NULL PRIMARY KEY
                         REFERENCES eliminacion_acta(lote_id) ON DELETE CASCADE,
    estado               TEXT        NOT NULL DEFAULT 'pendiente_firma',
    firma_id             UUID,
    minio_key_firmado    TEXT,
    xades_level          TEXT,
    -- RF-FIR-15: SIEMPRE false hoy -- art. 7 de la Ley 527/1999, nunca art. 28
    -- (no hay acreditación ONAC). Declararlo en la columna evita que alguien
    -- lea «firmado» como «firma digital cualificada».
    acreditado           BOOLEAN     NOT NULL DEFAULT false,
    last_error           TEXT,
    reconcile_intentos   INT         NOT NULL DEFAULT 0,
    reconcile_proximo_at TIMESTAMPTZ,
    reconcile_agotado    BOOLEAN     NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    firmado_at           TIMESTAMPTZ,

    CONSTRAINT eliminacion_acta_firma_estado_check
        CHECK (estado IN ('pendiente_firma', 'firmado')),
    -- Un acta «firmada» sin firma ni artefacto es una afirmación no
    -- verificable: mismo criterio que el aval sin acta del Comité.
    CONSTRAINT eliminacion_acta_firma_forma_check
        CHECK (
            estado <> 'firmado'
            OR (firma_id IS NOT NULL AND minio_key_firmado IS NOT NULL
                AND firmado_at IS NOT NULL)
        )
);

COMMENT ON TABLE eliminacion_acta_firma IS
    'Estado MUTABLE del sellado XAdES del acta de eliminación -- 1:1 con eliminacion_acta (append-only), NUNCA la misma tabla.';

CREATE INDEX IF NOT EXISTS ix_eliminacion_acta_firma_pendiente
    ON eliminacion_acta_firma (reconcile_proximo_at)
    WHERE estado = 'pendiente_firma' AND reconcile_agotado = false;

-- Rollback (manual, no automatizado por el runner):
-- DROP TABLE IF EXISTS eliminacion_acta_firma;
-- DROP TRIGGER IF EXISTS trg_eliminacion_acta_append_only ON eliminacion_acta;
-- DROP FUNCTION IF EXISTS reject_eliminacion_acta_mutation();
-- DROP TABLE IF EXISTS eliminacion_acta;
