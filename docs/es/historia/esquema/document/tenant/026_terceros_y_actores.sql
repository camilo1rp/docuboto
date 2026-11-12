-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E01, RF-RAD-05, 2026-08-30): TERCEROS con identidad y
-- ACTORES de la comunicación (remitente / destinatarios).
--
-- ── Qué había y por qué no bastaba ──────────────────────────────────────────
--
-- El remitente y el destinatario vivían en dos columnas de TEXTO LIBRE de
-- `radicados` —`sender_name` y `sender_entity`— **reutilizadas para los dos
-- papeles** y distinguidas solo por `doc_type`. De ahí salían tres problemas, y
-- el que más duele no es el que parece:
--
--   1. Una salida admitía UN destinatario, cuando el Acuerdo AGN 060/2001 y el
--      propio requisito piden varios.
--   2. Sin tipología, un ciudadano y una entidad pública se guardaban igual.
--   3. **Sin identidad, «Juan Pérez», «JUAN PEREZ» y «Juan Perez Gómez» son
--      tres destinatarios distintos.** No se podía responder «¿qué le hemos
--      enviado a esta persona?», ni reutilizar, ni deduplicar. El riesgo de
--      duplicación no estaba en una lista —no había lista— sino en la ausencia
--      de identidad.
--
-- ── Y la lección que trae el Orfeo original ─────────────────────────────────
--
-- Allí el mismo sujeto llegaba a aparecer TREINTA veces en el catálogo, con
-- direcciones distintas y variaciones de tildes o de escritura. La causa no era
-- que faltara una restricción de unicidad: era **atar la identidad a los datos
-- de contacto**, de modo que cada dirección nueva obligaba a crear un tercero
-- nuevo. Por eso aquí la dirección vive en el ENVÍO (`radicado_actor`) y no en
-- el sujeto: cambiar de dirección no crea un tercero.
--
-- Y por eso mismo esa copia es **inmutable**. Si una correspondencia salió a una
-- dirección concreta y mañana se actualiza la ficha, sin la copia congelada
-- todos los envíos pasados dirían que se enviaron a otra parte: información
-- errónea sobre un acto hecho con un dato que era verdad entonces.
--
-- ── Qué NO hace esta migración ──────────────────────────────────────────────
--
-- **No toca `sender_name` ni `sender_entity`.** Cuarenta y tres archivos las
-- leen (búsqueda, reportes, exportación, índice electrónico), y arrancarlas de
-- golpe convertiría una mejora en una migración de riesgo. El modelo nuevo es
-- ADITIVO: la capa de servicio mantiene las columnas viejas en sintonía con el
-- actor principal, y quien las lee sigue funcionando sin enterarse.

CREATE TABLE IF NOT EXISTS tercero (
    id                UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Las cinco tipologías del requisito. Un `direccion_libre` es el caso
    -- honesto de «sé a dónde mandarlo y no sé quién es»: existe a propósito
    -- para no obligar a inventar una identidad que no se tiene.
    tipo              TEXT        NOT NULL,
    nombre            TEXT        NOT NULL,
    entidad           TEXT,

    -- La IDENTIDAD, que es lo que hacía falta. Van juntos o ninguno: un número
    -- de documento sin decir de qué tipo no identifica a nadie, y un tipo sin
    -- número tampoco.
    tipo_documento    TEXT,
    numero_documento  TEXT,

    email             TEXT,
    telefono          TEXT,
    direccion         TEXT,
    municipio         TEXT,
    departamento      TEXT,
    pais              TEXT        NOT NULL DEFAULT 'CO',

    activo            BOOLEAN     NOT NULL DEFAULT true,
    creado_por        UUID,
    creado_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT tercero_tipo_check CHECK (tipo IN (
        'ciudadano', 'empresa', 'direccion_libre', 'usuario_interno', 'entidad_publica'
    )),
    CONSTRAINT tercero_nombre_forma_check CHECK (length(btrim(nombre)) >= 2),
    CONSTRAINT tercero_documento_pareja_check CHECK (
        (tipo_documento IS NULL AND numero_documento IS NULL)
        OR (tipo_documento IS NOT NULL AND numero_documento IS NOT NULL)
    )
);

COMMENT ON TABLE tercero IS
    'Catálogo de terceros de la comunicación (E01, RF-RAD-05): remitentes y destinatarios CON identidad, reutilizables entre radicados.';

-- **La deduplicación de raíz**: dos terceros no pueden compartir documento de
-- identidad. Es un índice y no un CHECK porque solo aplica a quien tiene
-- documento — una `direccion_libre` no lo tiene y no debe quedar excluida del
-- catálogo por eso.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tercero_documento
    ON tercero (tipo_documento, numero_documento)
    WHERE tipo_documento IS NOT NULL;

-- Búsqueda por nombre SIN acentos ni mayúsculas: es lo que impide que quien va
-- a registrar «Jose Perez» no encuentre al «José Pérez» que ya existe y cree un
-- duplicado. La restricción de arriba ataja los duplicados con documento; este
-- índice ataja los que se crean por no encontrar lo que ya está.
--
-- Se indexa el vector de texto con la configuración `public.spanish_unaccent`
-- —la misma que ya usa la búsqueda de expedientes— y NO `lower(unaccent(...))`:
-- `unaccent` no está marcada IMMUTABLE, así que PostgreSQL rechaza el índice de
-- expresión. La configuración de búsqueda sí lo permite y además reutiliza el
-- diccionario que el proyecto ya mantiene.
CREATE INDEX IF NOT EXISTS ix_tercero_nombre_norm
    ON tercero USING GIN (
        to_tsvector('public.spanish_unaccent'::regconfig, coalesce(nombre, '') || ' ' || coalesce(entidad, ''))
    );

CREATE INDEX IF NOT EXISTS ix_tercero_tipo ON tercero (tipo) WHERE activo;


CREATE TABLE IF NOT EXISTS radicado_actor (
    id           UUID    NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id  UUID    NOT NULL REFERENCES radicados(id) ON DELETE CASCADE,
    -- IDENTIDAD por referencia: es lo que permite responder «¿qué le hemos
    -- enviado a esta persona?» aunque su ficha cambie con los años.
    tercero_id   UUID    NOT NULL REFERENCES tercero(id),
    rol          TEXT    NOT NULL,
    orden        INTEGER NOT NULL DEFAULT 0,
    principal    BOOLEAN NOT NULL DEFAULT false,

    -- ── DATOS DE CONTACTO POR COPIA CONGELADA ───────────────────────────────
    --
    -- **Esto es lo que impide falsificar el pasado.** Si una correspondencia
    -- salió a una dirección concreta y mañana alguien actualiza la ficha del
    -- tercero, sin esta copia TODOS los envíos anteriores pasarían a decir que
    -- se enviaron a otra parte: información errónea sobre un acto que se hizo
    -- con un dato que era verdad en su momento, sin importar que hoy sea otro.
    -- La dirección usada pertenece al ENVÍO, no al sujeto.
    --
    -- Y de paso ataja la plaga que arrastraba el Orfeo original: allí cada
    -- dirección nueva obligaba a crear un tercero nuevo, y el mismo señor
    -- acababa treinta veces en el catálogo con tildes y datos distintos. Con la
    -- dirección en el envío, cambiar de dirección NO crea un tercero nuevo.
    --
    -- Se copian también nombre y entidad: quien lea el radicado dentro de diez
    -- años tiene que ver a quién se dirigió entonces, aunque el tercero se haya
    -- renombrado o fusionado desde.
    nombre       TEXT    NOT NULL,
    entidad      TEXT,
    email        TEXT,
    telefono     TEXT,
    direccion    TEXT,
    municipio    TEXT,
    departamento TEXT,
    pais         TEXT,

    -- ── POR DÓNDE se le envía, y qué dato exige ese canal ───────────────────
    --
    -- Un destinatario se atiende por correo electrónico, en físico, o por los
    -- dos. Y cada canal **exige su dato**: sin correo no hay envío electrónico
    -- posible, y sin dirección no hay envío físico posible. No es una
    -- preferencia, es una condición material del envío.
    --
    -- `NULL` = todavía sin decidir. Se admite a propósito: al vincular un
    -- destinatario puede no estar resuelto aún por dónde se le atiende, y
    -- obligar a decidirlo entonces empujaría a poner un canal cualquiera para
    -- poder seguir — que es peor que decir que no se sabe.
    canal        TEXT,

    creado_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT radicado_actor_rol_check CHECK (rol IN ('remitente', 'destinatario')),
    CONSTRAINT radicado_actor_nombre_forma_check CHECK (length(btrim(nombre)) >= 2),
    CONSTRAINT radicado_actor_canal_check
        CHECK (canal IS NULL OR canal IN ('email', 'fisico', 'ambos')),

    -- **Un envío imposible no se puede ni registrar.** Marcar a alguien para
    -- envío físico sin dirección, o para electrónico sin correo, deja el
    -- radicado prometiendo algo que no se puede cumplir — y el fallo aparecería
    -- al despachar, cuando ya no hay a quién preguntarle el dato. La condición
    -- se comprueba sobre la copia CONGELADA, que es la que se va a usar: la
    -- ficha del catálogo puede tener un correo que aquí no se aportó.
    CONSTRAINT radicado_actor_canal_email_check
        CHECK (canal NOT IN ('email', 'ambos') OR length(btrim(coalesce(email, ''))) > 0),
    CONSTRAINT radicado_actor_canal_fisico_check
        CHECK (canal NOT IN ('fisico', 'ambos') OR length(btrim(coalesce(direccion, ''))) > 0)
);

COMMENT ON TABLE radicado_actor IS
    'Actores de un radicado (E01, RF-RAD-05): identidad por referencia (tercero_id) + datos de contacto CONGELADOS del momento del envío. Los datos congelados son inmutables por trigger.';
COMMENT ON COLUMN radicado_actor.direccion IS
    'La dirección REALMENTE USADA en este radicado. No se actualiza nunca: cambiarla daría información errónea sobre un envío ya hecho.';
COMMENT ON COLUMN radicado_actor.canal IS
    'email | fisico | ambos | NULL (sin decidir). Cada canal exige su dato en la copia congelada: sin correo no hay envío electrónico, sin dirección no hay envío físico. Lo imponen dos CHECK, no la pantalla.';

-- La copia congelada es inmutable, y por la misma razón que el inventario de un
-- lote de eliminación o el calendario de retención pineado: si se pudiera
-- reescribir, no serviría para lo único que sirve — dar fe de a dónde se envió.
-- El `orden`, el `principal` y el `canal` sí pueden ajustarse mientras el
-- radicado se está componiendo, así que el trigger protege SOLO los datos de
-- contacto — y los CHECK de canal siguen vigilando que el canal elegido tenga
-- su dato.
CREATE OR REPLACE FUNCTION reject_radicado_actor_snapshot_update() RETURNS trigger AS $$
BEGIN
    IF NEW.nombre IS DISTINCT FROM OLD.nombre
       OR NEW.entidad IS DISTINCT FROM OLD.entidad
       OR NEW.email IS DISTINCT FROM OLD.email
       OR NEW.telefono IS DISTINCT FROM OLD.telefono
       OR NEW.direccion IS DISTINCT FROM OLD.direccion
       OR NEW.municipio IS DISTINCT FROM OLD.municipio
       OR NEW.departamento IS DISTINCT FROM OLD.departamento
       OR NEW.pais IS DISTINCT FROM OLD.pais
       OR NEW.tercero_id IS DISTINCT FROM OLD.tercero_id
    THEN
        RAISE EXCEPTION
            'los datos de contacto de un actor son los USADOS en el envío y no se editan '
            '(E01, RF-RAD-05): cambiarlos daría información errónea sobre un acto ya ocurrido';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_radicado_actor_snapshot ON radicado_actor;
CREATE TRIGGER trg_radicado_actor_snapshot
    BEFORE UPDATE ON radicado_actor
    FOR EACH ROW EXECUTE FUNCTION reject_radicado_actor_snapshot_update();

CREATE INDEX IF NOT EXISTS ix_radicado_actor_radicado ON radicado_actor (radicado_id, rol, orden);
CREATE INDEX IF NOT EXISTS ix_radicado_actor_tercero ON radicado_actor (tercero_id);

-- **Deduplicación por IDENTIDAD + DESTINO, no solo por identidad.** El mismo
-- señor en dos direcciones distintas es un envío legítimo —pasa, y hay que
-- poder hacerlo—; el mismo señor en la misma dirección dos veces es un
-- duplicado. `coalesce` porque `NULL <> NULL` en un índice único dejaría pasar
-- dos filas sin dirección, que es justo el caso de la dirección libre.
CREATE UNIQUE INDEX IF NOT EXISTS ux_radicado_actor_sin_duplicado
    ON radicado_actor (
        radicado_id, tercero_id, rol,
        coalesce(direccion, ''), coalesce(email, '')
    );

-- Un radicado tiene UN remitente, no varios. Los destinatarios son N a propósito.
CREATE UNIQUE INDEX IF NOT EXISTS ux_radicado_actor_remitente
    ON radicado_actor (radicado_id)
    WHERE rol = 'remitente';

-- Y un solo actor PRINCIPAL por papel: es el que alimenta las columnas
-- heredadas de `radicados`, así que dos principales dejarían indefinido qué
-- nombre acaba en la búsqueda y en los reportes.
CREATE UNIQUE INDEX IF NOT EXISTS ux_radicado_actor_principal
    ON radicado_actor (radicado_id, rol)
    WHERE principal;

-- Rollback (manual, no automatizado por el runner):
-- DROP TABLE IF EXISTS radicado_actor;
-- DROP FUNCTION IF EXISTS reject_radicado_actor_snapshot_update();
-- DROP TABLE IF EXISTS tercero;
