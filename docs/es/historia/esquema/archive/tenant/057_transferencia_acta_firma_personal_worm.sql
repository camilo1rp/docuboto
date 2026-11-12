-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 057 (E10 / E12, AREA-07-10): Object-Lock de las firmas PERSONALES del acta.
-- search_path = tenant_{slug} (ADR-012).
--
-- El sello institucional del acta ya tenía WORM (migración 030). Las firmas personales
-- (`transferencia_acta_firma_personal`, migración 027: elabora/entrega/recibe) NO: su XML
-- firmado vivía en el bucket de documentos, sin retención. Se protegen con el mismo
-- cableado best-effort + reconciliación.
--
-- Tabla APARTE y no columnas en `transferencia_acta_firma_personal`: aquella es
-- estrictamente append-only (y desde la 054 el rol de la app no tiene UPDATE sobre ella),
-- y el estado del cableado WORM es por naturaleza mutable (intentos, backoff, confirmación).
-- Misma separación bookkeeping/probatorio que ya hay entre `transferencia_acta` y
-- `transferencia_acta_firma`. 1:1 con la firma personal.
--
-- Backfill: toda firma personal que ya existe nace pendiente y la reconciliación la
-- protege (la fecha base de la retención es la de la propia firma, `creada_en`).
--
-- Idempotente: CREATE TABLE/INDEX IF NOT EXISTS + INSERT ... ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS transferencia_acta_firma_personal_worm (
    firma_personal_id           UUID PRIMARY KEY
                                REFERENCES transferencia_acta_firma_personal(id) ON DELETE CASCADE,
    worm_protegido_at           TIMESTAMPTZ,
    worm_object_id              UUID,
    worm_retain_until           TIMESTAMPTZ,
    worm_reconcile_intentos     INTEGER NOT NULL DEFAULT 0,
    worm_reconcile_proximo_at   TIMESTAMPTZ,
    worm_reconcile_ultimo_error TEXT,
    worm_reconcile_agotado      BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE transferencia_acta_firma_personal_worm IS
    'Estado del cableado WORM (Object-Lock) del XML de cada firma personal del acta de transferencia (AREA-07-10). 1:1 con transferencia_acta_firma_personal; mutable por diseño (backoff), a diferencia de aquella.';
COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_object_id IS
    'id de la fila preservacion_worm_objeto (storage-service, tipo acta_firma_<rol>) -- foránea lógica, cruza servicio.';

CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_personal_worm_pendiente
    ON transferencia_acta_firma_personal_worm (worm_reconcile_proximo_at)
    WHERE worm_protegido_at IS NULL AND worm_reconcile_agotado = FALSE;

INSERT INTO transferencia_acta_firma_personal_worm (firma_personal_id)
SELECT id FROM transferencia_acta_firma_personal
ON CONFLICT (firma_personal_id) DO NOTHING;

-- Rollback manual (no automatizado):
-- DROP TABLE IF EXISTS transferencia_acta_firma_personal_worm;
