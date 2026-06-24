-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E08, RF-SEG-08 / RF-BUS-04): nivel de seguridad del expediente.
--
-- Simétrica a document-service tenant/016 (radicados.nivel_seguridad). El catálogo de
-- niveles (security_levels) y la matriz rol→nivel (role_clearance) viven en el schema
-- del tenant (auth-service, tenant/003_classification.sql). La búsqueda de expedientes
-- (E09) filtra `nivel_seguridad <= clearance_del_llamante` (no read-up, mínimo
-- privilegio, ADR-013: la BD autoriza).
--
-- Escala = security_levels.code: 1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA.
-- DEFAULT 1 (PUBLICA): no rompe expedientes existentes ni el comportamiento previo.

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS nivel_seguridad SMALLINT NOT NULL DEFAULT 1;

ALTER TABLE expedientes
    DROP CONSTRAINT IF EXISTS ck_expedientes_nivel_seguridad;
ALTER TABLE expedientes
    ADD CONSTRAINT ck_expedientes_nivel_seguridad
    CHECK (nivel_seguridad BETWEEN 1 AND 3);

CREATE INDEX IF NOT EXISTS ix_expedientes_nivel_seguridad ON expedientes (nivel_seguridad);
