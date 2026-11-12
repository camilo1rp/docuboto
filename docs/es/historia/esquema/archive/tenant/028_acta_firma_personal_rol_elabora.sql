-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17/F4 Incremento 3 -- firma PERSONAL PKI del
-- ELABORADOR del acta de entrega, "Elaborado por" del Anexo FUID): amplía el
-- dominio del CHECK de `transferencia_acta_firma_personal.rol` para admitir
-- el tercer responsable del FUID `'elabora'` (= transferencias.creada_por, el
-- actor que CREÓ la transferencia), junto a los ya admitidos 'recibe' (Inc.1)
-- y 'entrega' (Inc.2). El elaborador es un firmante ADITIVO (no una parte del
-- traspaso): NO condiciona `firmada_completa` (que sigue siendo entrega +
-- recibe) -- solo se expone `elaborador_firmado` en la verificación.
--
-- SOLO amplía el dominio permitido (ningún dato existente puede violar el
-- nuevo CHECK, que es un superconjunto del anterior). El CHECK inline de la
-- 027 lo auto-nombró Postgres como `transferencia_acta_firma_personal_rol_check`.
-- DROP IF EXISTS + ADD es idempotente como par (re-ejecutar deja el mismo
-- constraint). NO toca el NOT NULL de la columna (constraint separado).

ALTER TABLE transferencia_acta_firma_personal
    DROP CONSTRAINT IF EXISTS transferencia_acta_firma_personal_rol_check;

ALTER TABLE transferencia_acta_firma_personal
    ADD CONSTRAINT transferencia_acta_firma_personal_rol_check
    CHECK (rol IN ('recibe', 'entrega', 'elabora'));

COMMENT ON COLUMN transferencia_acta_firma_personal.rol IS
    'recibe (archivista receptor, Inc.1) | entrega (remitente, Inc.2) | elabora (elaborador = creada_por, Inc.3, firmante ADITIVO -- no condiciona firmada_completa).';
