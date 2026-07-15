-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12, RF-SEG-08 / Ac. AGN 001/2024 art. 4.3.2.2): la
-- REMISIÓN de una transferencia (`enviar`, preparada->enviada) es un acto de
-- custodia -- el remitente se desprende del acervo y abre la responsabilidad
-- del destino -- y hasta ahora la fila no registraba quién lo ejecutó (H-B/H-C):
-- la traza solo existiría en `audit_log`, pero la fila de `transferencias` es
-- el registro que respalda el acta/FUID de la operación. Gemela de
-- `creada_por`/`decidida_por` (012_transferencias.sql).

ALTER TABLE transferencias ADD COLUMN IF NOT EXISTS enviada_por TEXT;
