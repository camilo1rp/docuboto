-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant: qué tipos documentales admite la VENTANILLA ÚNICA pública.
--
-- La radicación pública estaba cableada a la taxonomía de PQRS
-- (petición/queja/reclamo/sugerencia/denuncia) en un `Literal` de Python. Pero
-- una ventanilla única recibe TODA la correspondencia externa: tutelas, oficios
-- de otras entidades, facturas, invitaciones, hojas de vida. Fijar en código lo
-- que cada entidad recibe es decidir por ella.
--
-- El vocabulario sale del catálogo que la entidad ya mantiene y que **gobierna
-- la TRD**: así lo que entra por la ventanilla queda clasificado desde el minuto
-- uno y hereda su retención y su disposición final, en vez de entrar sin serie y
-- esperar a que alguien lo clasifique después (Ley 594/2000 arts. 21-24).
--
-- **Opt-in, no opt-out**: el default es FALSE. Un tipo documental interno
-- —«Acta de comité», «Memorando de sanción»— no debe aparecer en un formulario
-- público solo porque exista en el catálogo. Que la entidad declare qué recibe
-- del exterior es un acto suyo, y el silencio no puede significar «todo».

ALTER TABLE tipos_documentales
    ADD COLUMN IF NOT EXISTS admite_ventanilla_publica BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN tipos_documentales.admite_ventanilla_publica IS
    'Si TRUE, este tipo se ofrece en la ventanilla única pública (radicación sin '
    'autenticar). Opt-in deliberado: el silencio no significa «todo».';

-- Índice parcial: la consulta pública pide siempre el mismo subconjunto y suele
-- ser una fracción pequeña del catálogo.
CREATE INDEX IF NOT EXISTS ix_tipos_documentales_ventanilla
    ON tipos_documentales (admite_ventanilla_publica)
    WHERE admite_ventanilla_publica;
