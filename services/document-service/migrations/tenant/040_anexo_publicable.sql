-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 040 (AREA-07-12): control de publicación POR ANEXO.
--
-- Publicar un radicado de oficio (Ley 1712/2014 art. 12) hacía que CMIS sirviera el
-- PRIMER anexo, sin que nadie hubiera decidido difundir ese soporte (puede traer
-- datos personales de terceros, Ley 1581). `anexos.publicable` es el acto positivo
-- por soporte, con DEFAULT FALSE: un anexo nuevo no se difunde por no haber dicho nada.
--
-- Backfill: los anexos de radicados que YA están publicados conservan su difusión
-- (la entidad los publicó a sabiendas con el esquema anterior); sin esto, la migración
-- retiraría en silencio contenido que hoy se sirve. Lo no publicado queda en FALSE.
--
-- Idempotente. Rollback: ALTER TABLE anexos DROP COLUMN IF EXISTS publicable;

DO $$
DECLARE
    v_existia BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'anexos' AND column_name = 'publicable'
    ) INTO v_existia;

    IF NOT v_existia THEN
        ALTER TABLE anexos ADD COLUMN publicable BOOLEAN NOT NULL DEFAULT FALSE;
        UPDATE anexos a SET publicable = TRUE
          FROM radicados r
         WHERE r.id = a.radicado_id AND r.publicable AND r.nivel_seguridad = 1;
    END IF;
END $$;

COMMENT ON COLUMN anexos.publicable IS
    'Acto positivo de difusión POR ANEXO (Ley 1712/2014 art. 12). FALSE por defecto. '
    'CMIS solo sirve anexos con publicable = TRUE de radicados publicados.';
