-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (C17, ADR-038, 2026-10): FICHA PROVISIONAL del remitente
-- anónimo y su CONCILIACIÓN interna.
--
-- ── Qué había y por qué no bastaba ──────────────────────────────────────────
--
-- La ventanilla pública asociaba al remitente POR NÚMERO DE DOCUMENTO
-- (`crear_o_recuperar` → `get_por_documento`): quien escribía la cédula de otra
-- persona quedaba atado a SU ficha —con su dirección y teléfono vigentes— sin que
-- nadie verificara que era ella. Eso es a la vez un oráculo de enumeración (la
-- respuesta o el tiempo delatan que el documento existe) y una suplantación
-- gratuita (la ficha ajena entra a un radicado que el suplantador redactó).
--
-- ── Qué hace esta migración ─────────────────────────────────────────────────
--
-- Una ficha PROVISIONAL guarda lo que el anónimo DECLARÓ, pero NO ocupa el
-- documento: el número declarado vive en `documento_declarado_*` y
-- `tipo_documento/numero_documento` quedan en NULL, así que no choca con
-- `ux_tercero_documento` (UNIQUE sobre tipo+número, fusionadas incluidas). Un
-- funcionario la CONCILIA después: la vincula a la ficha existente (reutiliza
-- `fusionado_en`), la confirma como ficha nueva o la descarta. El radicado y su
-- número no se tocan (`radicado_actor.tercero_id` es inmutable por trigger y no
-- hace falta).
--
-- Las filas existentes quedan `identidad='confirmada'` por DEFAULT: no hay
-- backfill ni rama «legado».

ALTER TABLE tercero
    ADD COLUMN IF NOT EXISTS identidad                  text NOT NULL DEFAULT 'confirmada',
    ADD COLUMN IF NOT EXISTS documento_declarado_tipo   text,
    ADD COLUMN IF NOT EXISTS documento_declarado_numero text,
    ADD COLUMN IF NOT EXISTS conciliacion               text,
    ADD COLUMN IF NOT EXISTS conciliada_at              timestamptz,
    -- `auth_users.id` (el X-User-Id interno que firma el gateway), NUNCA el `sub`
    -- de Keycloak. Sin FK, igual que `creado_por` y `fusionado_por`.
    ADD COLUMN IF NOT EXISTS conciliada_por             uuid,
    ADD COLUMN IF NOT EXISTS conciliacion_motivo        text,
    -- Con qué se verificó la identidad al vincular o confirmar (evidencia archivística:
    -- sin ella el acto dice QUIÉN decidió, no en qué se basó).
    ADD COLUMN IF NOT EXISTS conciliacion_medio         text;

COMMENT ON COLUMN tercero.identidad IS
    'confirmada: la identidad (tipo+número de documento) fue verificada o es del catálogo. provisional: la declaró un anónimo en la ventanilla pública y NO ocupa el documento.';
COMMENT ON COLUMN tercero.documento_declarado_numero IS
    'Documento que el anónimo DECLARÓ. Evidencia: inmutable (trigger). No identifica a nadie hasta que un funcionario concilia.';
COMMENT ON COLUMN tercero.conciliacion IS
    'pendiente | vinculada (fusionada en una ficha existente) | confirmada (pasó a ficha definitiva) | descartada. NULL en fichas que nunca fueron provisionales.';
COMMENT ON COLUMN tercero.conciliacion_medio IS
    'presencial_documento | respuesta_canal_registrado | llamada_telefono_registrado | otro. Obligatorio al vincular o confirmar; con `otro`, el motivo es obligatorio.';
COMMENT ON COLUMN tercero.conciliada_por IS
    'auth_users.id de quien concilió; nunca el sub de Keycloak.';

ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_identidad_valores;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_identidad_valores
    CHECK (identidad IN ('confirmada', 'provisional'));

ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_conciliacion_valores;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_conciliacion_valores
    CHECK (conciliacion IS NULL
           OR conciliacion IN ('pendiente', 'vinculada', 'confirmada', 'descartada'));

-- Una provisional NO tiene identidad: ningún PATCH de ficha puede dársela por la
-- puerta de atrás, y por eso no choca con `ux_tercero_documento`.
ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_provisional_sin_identidad;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_provisional_sin_identidad
    CHECK (identidad <> 'provisional'
           OR (tipo_documento IS NULL
               AND numero_documento IS NULL
               AND documento_declarado_numero IS NOT NULL
               AND conciliacion IN ('pendiente', 'vinculada', 'descartada')));

ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_confirmada_conciliacion;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_confirmada_conciliacion
    CHECK (identidad <> 'confirmada'
           OR conciliacion IS NULL OR conciliacion = 'confirmada');

-- `pendiente` ⇔ nadie la ha conciliado todavía. Lo conciliado SIEMPRE tiene autor
-- y fecha: un acto sobre el directorio sin responsable no se puede registrar.
ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_conciliacion_autor;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_conciliacion_autor
    CHECK (conciliacion IS NULL
           OR ((conciliacion = 'pendiente')
               = (conciliada_at IS NULL AND conciliada_por IS NULL)));

ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_conciliacion_vinculada;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_conciliacion_vinculada
    CHECK (conciliacion IS DISTINCT FROM 'vinculada' OR fusionado_en IS NOT NULL);

ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_conciliacion_motivo;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_conciliacion_motivo
    CHECK ((conciliacion_motivo IS NULL OR length(btrim(conciliacion_motivo)) >= 10)
           AND (conciliacion IS DISTINCT FROM 'descartada' OR conciliacion_motivo IS NOT NULL));

-- El medio de verificación es obligatorio al vincular o confirmar (no al descartar), y
-- `otro` no se explica solo: exige motivo.
ALTER TABLE tercero DROP CONSTRAINT IF EXISTS chk_tercero_conciliacion_medio;
ALTER TABLE tercero
    ADD CONSTRAINT chk_tercero_conciliacion_medio
    CHECK ((conciliacion_medio IS NULL
            OR conciliacion_medio IN ('presencial_documento', 'respuesta_canal_registrado',
                                      'llamada_telefono_registrado', 'otro'))
           AND (conciliacion NOT IN ('vinculada', 'confirmada') OR conciliacion_medio IS NOT NULL)
           AND (conciliacion_medio IS DISTINCT FROM 'otro' OR conciliacion_motivo IS NOT NULL));

-- Lo declarado es evidencia: no se reescribe. Y un estado terminal no vuelve a
-- `pendiente` ni salta a otro terminal (una conciliación no se «deshace»; si fue
-- un error, se corrige con una ficha nueva y un asiento nuevo).
CREATE OR REPLACE FUNCTION reject_tercero_conciliacion_rewrite() RETURNS trigger AS $$
BEGIN
    IF NEW.documento_declarado_tipo IS DISTINCT FROM OLD.documento_declarado_tipo
       OR NEW.documento_declarado_numero IS DISTINCT FROM OLD.documento_declarado_numero
    THEN
        RAISE EXCEPTION
            'el documento declarado por el remitente es evidencia y no se edita (C17, ADR-038)';
    END IF;
    IF OLD.conciliacion IN ('vinculada', 'confirmada', 'descartada')
       AND NEW.conciliacion IS DISTINCT FROM OLD.conciliacion
    THEN
        RAISE EXCEPTION
            'una conciliación terminada no se rehace (C17, ADR-038)';
    END IF;
    IF OLD.identidad = 'confirmada' AND NEW.identidad = 'provisional' THEN
        RAISE EXCEPTION
            'una ficha confirmada no vuelve a provisional (C17, ADR-038)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tercero_conciliacion ON tercero;
CREATE TRIGGER trg_tercero_conciliacion
    BEFORE UPDATE ON tercero
    FOR EACH ROW EXECUTE FUNCTION reject_tercero_conciliacion_rewrite();

-- La cola de conciliación: lo pendiente, lo más antiguo primero.
CREATE INDEX IF NOT EXISTS ix_tercero_por_conciliar
    ON tercero (creado_at)
    WHERE conciliacion = 'pendiente';

-- Rollback (manual, no automatizado por el runner):
-- DROP TRIGGER IF EXISTS trg_tercero_conciliacion ON tercero;
-- DROP FUNCTION IF EXISTS reject_tercero_conciliacion_rewrite();
-- DROP INDEX IF EXISTS ix_tercero_por_conciliar;
-- ALTER TABLE tercero DROP COLUMN identidad, DROP COLUMN documento_declarado_tipo, ...;
