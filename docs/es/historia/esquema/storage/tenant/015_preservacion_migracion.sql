-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10, RF-PRE-04, 2026-08-29): MIGRACIÓN DE FORMATO
-- contra la obsolescencia (Decreto 2609/2012 art. 32).
--
-- La norma pide tres cosas y las tres viven en esta tabla: **conservar el
-- original**, registrar la **versión origen y destino**, y **verificar la
-- integridad antes y después**. De ahí que el registro guarde los DOS hashes y
-- los DOS `file_id`: el de origen sigue existiendo intacto, y el de destino es
-- un artefacto DERIVADO, nunca un reemplazo.
--
-- **La cadena de procedencia es la fila entera.** Sin ella, dentro de diez años
-- habría dos PDF y ninguna forma de saber cuál salió de cuál, con qué
-- herramienta ni si alguien comprobó que el contenido sobrevivió al viaje —
-- que es exactamente lo que una migración de preservación tiene que poder
-- demostrar.

CREATE TABLE IF NOT EXISTS preservacion_migracion (
    id                  UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,

    file_id_origen      UUID        NOT NULL,
    -- NULL mientras la migración no produjo un derivado admisible: un intento
    -- fallido TAMBIÉN se registra, porque «se intentó y no se pudo» es
    -- información de preservación, no ruido — es lo que dice qué documentos
    -- siguen esperando una migración que no sale.
    file_id_destino     UUID,

    formato_origen      TEXT        NOT NULL,
    formato_destino     TEXT        NOT NULL,

    -- Fixity ANTES y DESPUÉS (criterio de aceptación de RF-PRE-04). El de
    -- origen se RECALCULA sobre los bytes descargados y se compara con el
    -- almacenado: migrar un archivo ya corrupto propagaría la corrupción a la
    -- copia de preservación y la dejaría con aspecto de buena.
    sha256_origen       CHAR(64)    NOT NULL,
    sha256_destino      CHAR(64),

    herramienta         TEXT        NOT NULL,
    estado              TEXT        NOT NULL,
    motivo              TEXT,

    iniciada_por        UUID,
    iniciada_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT preservacion_migracion_estado_check
        CHECK (estado IN ('migrado', 'fallido', 'no_verificado')),
    CONSTRAINT preservacion_migracion_sha_origen_check
        CHECK (sha256_origen ~ '^[0-9a-f]{64}$'),
    -- Una migración «migrada» sin derivado ni su hash es una afirmación no
    -- verificable: si se dice que hay copia de preservación, tiene que haber
    -- algo que enseñar y con qué comprobarlo.
    CONSTRAINT preservacion_migracion_forma_check
        CHECK (
            estado <> 'migrado'
            OR (file_id_destino IS NOT NULL AND sha256_destino ~ '^[0-9a-f]{64}$')
        )
);

COMMENT ON TABLE preservacion_migracion IS
    'Cadena de procedencia de una migración de formato (E10, RF-PRE-04): origen y destino, hashes antes y después, herramienta y desenlace. El original NUNCA se reemplaza.';
COMMENT ON COLUMN preservacion_migracion.estado IS
    'migrado (derivado producido Y verificado conforme) | fallido (no se pudo convertir, o el resultado no era conforme) | no_verificado (se convirtió pero no había con qué comprobar la conformidad -- NO se promueve a copia de preservación).';

CREATE INDEX IF NOT EXISTS ix_preservacion_migracion_origen
    ON preservacion_migracion (file_id_origen, iniciada_at DESC);

-- Idempotencia: una sola migración VIGENTE por (origen, formato destino). Un
-- reintento tras un fallo sí puede volver a intentarse —por eso el índice es
-- parcial sobre `estado = 'migrado'`— pero no se acumulan dos copias de
-- preservación del mismo archivo al mismo formato, que es como se acaba sin
-- saber cuál es la buena.
CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_migracion_vigente
    ON preservacion_migracion (file_id_origen, formato_destino)
    WHERE estado = 'migrado';

-- Rollback (manual, no automatizado por el runner):
-- DROP TABLE IF EXISTS preservacion_migracion;
