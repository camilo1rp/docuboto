-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E08, RF-SEG-08 / RF-BUS-04): nivel de seguridad del radicado.
--
-- Implementa la pieza que faltaba del control de acceso: la matriz rol→nivel máximo
-- (role_clearance) y el catálogo de niveles (security_levels) ya viven en el schema
-- del tenant (auth-service, tenant/003_classification.sql), pero el NIVEL CONCRETO de
-- cada documento lo guarda document-service. Esta columna lo materializa.
--
-- Escala (idéntica a security_levels.code): 1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA.
-- DEFAULT 1 (PUBLICA): todo radicado existente y todo el que no se clasifique queda
-- accesible para cualquier usuario autenticado — no rompe el comportamiento previo.
-- La búsqueda (E09) filtra `nivel_seguridad <= clearance_del_llamante` (no read-up,
-- mínimo privilegio, ADR-013: la BD autoriza).

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS nivel_seguridad SMALLINT NOT NULL DEFAULT 1;

ALTER TABLE radicados
    DROP CONSTRAINT IF EXISTS ck_radicados_nivel_seguridad;
ALTER TABLE radicados
    ADD CONSTRAINT ck_radicados_nivel_seguridad
    CHECK (nivel_seguridad BETWEEN 1 AND 3);

CREATE INDEX IF NOT EXISTS ix_radicados_nivel_seguridad ON radicados (nivel_seguridad);
