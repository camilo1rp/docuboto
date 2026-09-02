-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E23 Inc.5, RF-PRO-06): alcance de plantillas por dependencia.
--
-- Paridad con `sgd_plan_plantillas.depe_codi` + `plan_global` del Orfeo legado.
-- Aquí una plantilla era global y punto, así que la lista que veía un
-- funcionario de Jurídica incluía las de Contratación, las de Talento Humano y
-- las de todas las demás. No es un problema de seguridad —una plantilla no
-- contiene datos de nadie— sino de USO: con cuarenta plantillas en la lista, la
-- que hace falta no se encuentra, y el camino corto es no usar ninguna.
--
-- **`es_global` por defecto TRUE, y es deliberado.** Las plantillas que ya existen
-- las ve hoy todo el mundo; ponerlas en FALSE las haría desaparecer de todas las
-- pantallas a la vez, y el fallo se leería como «se borraron las plantillas».
-- Restringir es un acto que la entidad decide plantilla por plantilla.
--
-- Se llama `es_global` y no `global` —como en el legado y como decía el plan—
-- porque `global` es palabra reservada de Python: un schema Pydantic no puede
-- declarar ese atributo, y salir del paso con un alias dejaría dos nombres para
-- lo mismo en la frontera más leída del servicio.

ALTER TABLE plantilla
    ADD COLUMN IF NOT EXISTS es_global  BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS creado_por UUID;

CREATE TABLE IF NOT EXISTS plantilla_dependencia (
    plantilla_id UUID    NOT NULL REFERENCES plantilla(id) ON DELETE CASCADE,
    depe_codigo  INTEGER NOT NULL,
    PRIMARY KEY (plantilla_id, depe_codigo)
);

CREATE INDEX IF NOT EXISTS ix_plantilla_dependencia_depe
    ON plantilla_dependencia (depe_codigo);

-- `ON DELETE CASCADE` es correcto aquí, y hay que decir por qué porque en este
-- proyecto una FK en cascada ya se llevó un acta por delante: lo que cuelga es
-- una ASIGNACIÓN —«esta plantilla la usa esta dependencia»—, no un documento de
-- archivo. Borrar la plantilla debe borrar a quién estaba asignada; no queda
-- nada que conservar. La dirección importa y hay que mirarla cada vez: aquí
-- apunta de la asignación a la plantilla, no al revés.

COMMENT ON COLUMN plantilla.es_global IS
    'TRUE = la ve cualquier dependencia (E23, RF-PRO-06). FALSE = solo las '
    'listadas en plantilla_dependencia. Por defecto TRUE para no hacer '
    'desaparecer las plantillas existentes de todas las pantallas a la vez.';

COMMENT ON TABLE plantilla_dependencia IS
    'Dependencias que alcanzan una plantilla NO global (paridad legado: '
    'sgd_plan_plantillas.depe_codi). Vacía sobre una plantilla no global = no la '
    've nadie, que es un estado valido: una plantilla retirada de circulacion '
    'sin borrar su historial.';
