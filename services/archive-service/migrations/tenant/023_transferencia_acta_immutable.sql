-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E12 — endurecimiento de la inmutabilidad del acta de entrega):
-- eleva la inmutabilidad de `transferencia_acta` de PROCEDIMENTAL (sin ruta de UPDATE
-- en el repositorio + PK que bloquea un 2º INSERT) a ESTRUCTURAL. El acta es el
-- artefacto probatorio del cambio de custodia (Ley 594/2000 art. 16; Ac. AGN 001/2024
-- Anexo FUID): inalterable por el MOTOR, no solo por convención de código.
-- Detectable-por-fixity (el SHA-256 dejaría de casar) NO es imposibilitado-por-el-motor;
-- esto último es lo que aquí se cierra.
--
-- Alcance HONESTO frente a public.audit_log (RF-FIR-15): esto aporta la CAPA DE TRIGGER
-- equivalente a la de audit_log, NO su segunda capa de hash-chain. La tamper-evidence
-- del acta ante un bypass del trigger (sólo superusuario) NO vive aquí, sino en el
-- `acta_fuid_sha256` anclado en el payload del asiento hash-encadenado
-- `archive.transferencia_recibida` (ver `TransferenciaService.recibir`). Tampoco se
-- añade capa de privilegios (REVOKE) como audit_log; el rol de la app no tiene DDL.
--
-- Cobertura: UPDATE + DELETE (FOR EACH ROW) + TRUNCATE (FOR EACH STATEMENT), igual que
-- audit_log. Se rechaza DELETE (no solo UPDATE): un DELETE por SQL directo es tan
-- destructivo de un registro probatorio como un UPDATE. Se cubre TRUNCATE porque
-- bypassea los triggers de fila -- omitirlo (como expediente_eventos, migración 014)
-- dejaría un vector para borrar TODAS las actas del tenant sin guarda; declarar
-- "inmutable como audit_log" sin cubrirlo sería sobre-declarar (RF-FIR-15).
--
-- CASCADE dormido: transferencia_acta.transferencia_id REFERENCES transferencias(id)
-- ON DELETE CASCADE, y expedientes → transferencias también CASCADE. El trigger
-- BEFORE DELETE/TRUNCATE bloquearía un CASCADE entrante. Aceptado y correcto: (1) no
-- existe hoy ruta que borre un expediente ni una transferencia (los expedientes nunca
-- se borran físicamente; grep sin DELETE/TRUNCATE de transferencias) -> el CASCADE
-- está dormido; (2) el trigger solo muerde cuando existe un acta, y un acta solo
-- existe para una transferencia 'recibida' (custodia irreversible ya consumada) ->
-- rehusar ESE borrado es la semántica de un registro legal permanente, no una
-- sorpresa; (3) el teardown de integración usa DROP SCHEMA CASCADE (DDL) -> los
-- triggers de DML/TRUNCATE no se disparan.
--
-- No se replica la capa extra FOR EACH STATEMENT anti-UPDATE/DELETE de audit_log
-- (migración 004): esa existía por tablas PARTICIONADAS; esta tabla es simple, un
-- trigger FOR EACH ROW captura todo UPDATE/DELETE de forma completa.
--
-- Idempotente: CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS + DROP/CREATE.

CREATE OR REPLACE FUNCTION reject_transferencia_acta_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta is append-only (acta de entrega inmutable; Ley 594/2000 art. 16, Ac. AGN 001/2024 Anexo FUID)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transferencia_acta_immutable ON transferencia_acta;
CREATE TRIGGER trg_transferencia_acta_immutable
    BEFORE UPDATE OR DELETE ON transferencia_acta
    FOR EACH ROW EXECUTE FUNCTION reject_transferencia_acta_mutation();

CREATE OR REPLACE FUNCTION reject_transferencia_acta_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta is append-only: TRUNCATE denegado (Ley 594/2000 art. 16, Ac. AGN 001/2024 Anexo FUID)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transferencia_acta_no_truncate ON transferencia_acta;
CREATE TRIGGER trg_transferencia_acta_no_truncate
    BEFORE TRUNCATE ON transferencia_acta
    FOR EACH STATEMENT EXECUTE FUNCTION reject_transferencia_acta_truncate();
