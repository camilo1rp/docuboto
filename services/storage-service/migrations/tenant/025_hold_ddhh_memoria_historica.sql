-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 025 (C-5, ADR-043; auditoría de cierre del lote C): un motivo más para la
-- retención legal, `ddhh_memoria_historica` (información de derechos humanos y memoria
-- histórica; Ley 1448/2011 art. 144 [confirmar]). No la levanta el vencimiento de la retención
-- de la TRD ni una valoración: solo un acto de autoridad competente, y el servicio exige un
-- fundamento de levantamiento de al menos 50 caracteres (`HoldLevantarRequest`).
--
-- Idempotente. Rollback: restaurar el CHECK de la 024 (sin `ddhh_memoria_historica`) cuando no
-- haya filas con ese motivo.

ALTER TABLE preservacion_legal_hold DROP CONSTRAINT IF EXISTS ck_plh_motivo;
ALTER TABLE preservacion_legal_hold ADD CONSTRAINT ck_plh_motivo CHECK (
    motivo_tipo IN ('judicial', 'disciplinario', 'fiscal', 'administrativo', 'ddhh_memoria_historica',
                    'valoracion_pendiente')
);
