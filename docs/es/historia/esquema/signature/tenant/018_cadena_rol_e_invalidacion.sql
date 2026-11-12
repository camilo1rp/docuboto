-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E23, RF-PRO-10/11): visto bueno y firma en UNA cadena,
-- y el estado que faltaba para poder invalidarla.
--
-- ── Qué había ───────────────────────────────────────────────────────────────
--
-- `firma_cadena` ya implementaba turnos ordenados con 2FA y rechazo con motivo.
-- Le faltaban dos cosas para servir al circuito real de una entidad:
--
--   1. **El rol.** En la práctica varias personas dan visto bueno y UNA firma:
--      la que compromete a la entidad y produce el documento definitivo.
--      Presentar los dos actos igual atribuiría a un revisor un compromiso que
--      no adquirió.
--   2. **Un estado de invalidación.** `estado` solo admitía
--      pendiente|firmado|rechazado, así que cuando el texto cambiaba no había
--      forma de decirlo: la cadena seguía «pendiente» sobre un documento que ya
--      no existe, o peor, «firmado» sobre uno que cambió después.

ALTER TABLE firma_cadena
    ADD COLUMN IF NOT EXISTS rol VARCHAR(12) NOT NULL DEFAULT 'firma';

-- El CHECK se declara aparte para poder reemplazarlo idempotentemente. `firma`
-- es el default a propósito: las cadenas que ya existen son todas de firma, y
-- un default distinto reescribiría su significado en silencio.
ALTER TABLE firma_cadena DROP CONSTRAINT IF EXISTS firma_cadena_rol_check;
ALTER TABLE firma_cadena ADD CONSTRAINT firma_cadena_rol_check
    CHECK (rol IN ('visto_bueno', 'firma'));

-- ── El CHECK de estado se REEMPLAZA, no se añade otro ──────────────────────
--
-- Dos CHECK sobre la misma columna se conjugan con AND: añadir uno nuevo con
-- 'invalidada' no habría admitido nada, porque el viejo la seguiría rechazando.
-- Y el modo de fallo es el peor de los conocidos en este proyecto: un valor que
-- el código produce y el CHECK no admite: el `503` acusa a un servicio sano y
-- nadie mira la restricción.
ALTER TABLE firma_solicitud DROP CONSTRAINT IF EXISTS firma_solicitud_estado_check;
ALTER TABLE firma_solicitud ADD CONSTRAINT firma_solicitud_estado_check
    CHECK (estado IN ('pendiente', 'firmado', 'rechazado', 'invalidada'));

ALTER TABLE firma_cadena DROP CONSTRAINT IF EXISTS firma_cadena_estado_check;
ALTER TABLE firma_cadena ADD CONSTRAINT firma_cadena_estado_check
    CHECK (estado IN ('pendiente', 'firmado', 'rechazado', 'invalidada'));

-- Motivo de la invalidación, para que la bandeja pueda decir POR QUÉ decayó un
-- turno que alguien tenía pendiente. «Desapareció» sin explicación es lo que
-- hace que la gente desconfíe de la herramienta.
ALTER TABLE firma_solicitud
    ADD COLUMN IF NOT EXISTS invalidada_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS invalidada_motivo TEXT;

-- El índice de unicidad de cadena abierta ya filtra `WHERE estado='pendiente'`,
-- así que una solicitud invalidada NO bloquea la creación de la que la
-- sustituye. Se comprueba aquí explícitamente para que un cambio futuro del
-- índice no lo rompa por descuido: sin esto, invalidar dejaría el objeto sin
-- poder volver a someterse a firma nunca más.
DO $$
DECLARE
    predicado TEXT;
BEGIN
    SELECT pg_get_expr(i.indpred, i.indrelid) INTO predicado
      FROM pg_index i
      JOIN pg_class c ON c.oid = i.indexrelid
     WHERE c.relname = 'ux_firma_solicitud_objeto_abierta'
       AND c.relnamespace = current_schema()::regnamespace;
    IF predicado IS NULL OR predicado NOT LIKE '%pendiente%' THEN
        RAISE EXCEPTION
            'ux_firma_solicitud_objeto_abierta debe seguir filtrando por estado '
            '= ''pendiente'' (predicado actual: %). Sin ese filtro, una solicitud '
            'invalidada bloquearia para siempre la cadena que la sustituye.',
            COALESCE(predicado, '<sin indice>');
    END IF;
END $$;

COMMENT ON COLUMN firma_cadena.rol IS
    'visto_bueno | firma (E23, RF-PRO-10). El turno de rol=firma es el que '
    'compromete a la entidad y produce el documento definitivo; los vistos '
    'buenos son evidencia del tramite, no firmas.';

COMMENT ON COLUMN firma_solicitud.invalidada_motivo IS
    'Por que decayo la cadena (E23, RF-PRO-11), normalmente porque el texto '
    'cambio. Un turno que desaparece sin explicacion hace desconfiar de la '
    'herramienta.';
