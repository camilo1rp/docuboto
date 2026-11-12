-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E23, RF-PRO-02): el borrador sabe a qué responde.
--
-- La combinación de campos necesita un contexto, y la mitad de ese contexto
-- —quién escribió, sobre qué asunto, con qué número y en qué fecha— vive en el
-- radicado que se está respondiendo. Sin este vínculo, `*NOM_R*` y `*RAD_E*` no
-- tienen de dónde salir y la plantilla vuelve a ser el texto muerto que era.
--
-- `ON DELETE SET NULL` y no CASCADE: lo que cuelga aquí es un borrador propio,
-- no una copia del radicado padre. Si el padre desapareciera, el texto que un
-- funcionario escribió sigue siendo suyo y no debe evaporarse con él. (La FK en
-- cascada ya se llevó un acta por delante en este proyecto; la dirección de la
-- dependencia hay que mirarla cada vez.)

ALTER TABLE borrador
    ADD COLUMN IF NOT EXISTS radicado_padre_id UUID REFERENCES radicados(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_borrador_radicado_padre
    ON borrador (radicado_padre_id) WHERE radicado_padre_id IS NOT NULL;

COMMENT ON COLUMN borrador.radicado_padre_id IS
    'Radicado de Entrada que este borrador responde (E23, RF-PRO-02). Fuente del '
    'contexto de combinación: remitente, asunto, número y fecha del padre. Se '
    'lee SIEMPRE con el clearance del llamante: combinar sin gate exfiltraría el '
    'asunto de un radicado reservado al cuerpo de un documento legible.';
