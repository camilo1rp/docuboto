-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E23, RF-PRO-09/11): versión y huella del cuerpo.
--
-- ── Por qué el hash lo calcula la BASE y no el servicio ─────────────────────
--
-- El hash del cuerpo es la BISAGRA del circuito de aprobación: es lo que un
-- visto bueno afirma haber leído y lo que una firma compromete. Si lo calculara
-- la capa de servicio, sería una convención — se respeta hasta que alguien
-- añade otro camino de escritura y se le olvida. Con un trigger, NINGÚN camino
-- —presente o futuro, ni siquiera un UPDATE a mano en una consola— puede
-- guardar texto nuevo conservando la huella vieja.
--
-- ── Y por qué el trigger BORRA el documento producido ───────────────────────
--
-- Si el cuerpo cambia, el PDF/A que se generó antes ya no corresponde a este
-- borrador. Dejarlo colgando sería peor que no tenerlo: alguien lo firmaría
-- creyendo que es el documento del texto que está leyendo. La invalidación del
-- circuito es DERIVADA de este hecho, no un acto que alguien deba recordar
-- ejecutar — si dependiera de que alguien pulse «revocar», el caso en que se
-- olvida sería exactamente el caso de fraude.
--
-- `sha256()` es nativo de PostgreSQL (11+): no se depende de pgcrypto, que en
-- un tenant nuevo podría no estar instalada y dejaría el trigger fallando en un
-- sitio donde el fallo es difícil de atribuir.

ALTER TABLE borrador
    ADD COLUMN IF NOT EXISTS version       INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS cuerpo_sha256 CHAR(64);

CREATE OR REPLACE FUNCTION borrador_sellar_version() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.cuerpo_sha256 := CASE
            WHEN NEW.cuerpo IS NULL THEN NULL
            ELSE encode(sha256(convert_to(NEW.cuerpo, 'UTF8')), 'hex')
        END;
        RETURN NEW;
    END IF;

    IF NEW.cuerpo IS DISTINCT FROM OLD.cuerpo THEN
        NEW.version       := OLD.version + 1;
        NEW.cuerpo_sha256 := CASE
            WHEN NEW.cuerpo IS NULL THEN NULL
            ELSE encode(sha256(convert_to(NEW.cuerpo, 'UTF8')), 'hex')
        END;
        -- El documento producido deja de corresponder al texto: se retira.
        -- No se conserva «por si acaso» — esos son justo los bytes que nadie
        -- debe firmar creyendo que son los del texto que tiene delante.
        NEW.documento_file_id := NULL;
        NEW.documento_sha256  := NULL;
        NEW.documento_formato := NULL;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_borrador_sellar_version ON borrador;
CREATE TRIGGER trg_borrador_sellar_version
    BEFORE INSERT OR UPDATE ON borrador
    FOR EACH ROW EXECUTE FUNCTION borrador_sellar_version();

-- Backfill: los borradores que ya existen se quedarían sin huella, y un
-- circuito abierto sobre ellos no tendría contra qué comparar.
UPDATE borrador
   SET cuerpo_sha256 = encode(sha256(convert_to(cuerpo, 'UTF8')), 'hex')
 WHERE cuerpo IS NOT NULL AND cuerpo_sha256 IS NULL;

COMMENT ON COLUMN borrador.cuerpo_sha256 IS
    'Huella del cuerpo, calculada por TRIGGER (E23, RF-PRO-09/11). Ningún camino '
    'de escritura puede guardar texto nuevo conservando la huella vieja.';

COMMENT ON COLUMN borrador.version IS
    'Sube en cada edición del cuerpo. Cambiar el texto invalida el circuito de '
    'aprobación: la invalidación es DERIVADA del hash, no un acto que recordar.';
