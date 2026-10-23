-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 071 (C-4 / C10, ADR-040). search_path = tenant_{slug}.
--
-- `publicable`: la entidad declara que este TIPO documental entra en su esquema de
-- publicación (Ley 1712/2014 art. 12). Es una marca PROPIA, ortogonal a
-- `admite_ventanilla_publica` («se recibe del público», Ley 1755): que un tipo
-- se reciba por la ventanilla no dice que se difunda, ni al revés.
--
-- Opt-in, default FALSE: el silencio no significa «publicar». document-service
-- lo lee en vivo al publicar un radicado (`set_publicable`, en el WHERE).

ALTER TABLE tipos_documentales
    ADD COLUMN IF NOT EXISTS publicable BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN tipos_documentales.publicable IS
    'Si TRUE, los radicados de este tipo pueden publicarse de oficio (Ley 1712/2014 art. 12). '
    'Distinto de admite_ventanilla_publica (recepción del público, Ley 1755). Opt-in.';

CREATE INDEX IF NOT EXISTS ix_tipos_documentales_publicable
    ON tipos_documentales (publicable)
    WHERE publicable;
