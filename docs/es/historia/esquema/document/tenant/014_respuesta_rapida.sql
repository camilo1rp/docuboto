-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (paridad legado): respuesta rápida (antecedente E↔S).
--
-- Un radicado de Salida puede responder a uno de Entrada; `responde_a` enlaza la
-- respuesta con su antecedente, habilitando la trazabilidad entrada↔salida.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS responde_a UUID REFERENCES radicados(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_radicados_responde_a ON radicados (responde_a);
