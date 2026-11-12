-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (RF-SEG-08, D-15): snapshot de nivel de seguridad en cadena de firma.
--
-- Añade nivel_seguridad a firma_solicitud como snapshot capturado una única vez al
-- crear la cadena desde el objeto firmado (radicado u otro). Persistir el nivel evita
-- acoplar cada consulta de firma al servicio de documentos y permite gatear directamente
-- contra el clearance del actor (RF-SEG-08, no-read-up) sin join inter-servicio.
--
-- Niveles: 1=PUBLICA (default para filas existentes), 2=RESERVADA, 3=CLASIFICADA.
--
-- Rollback:
--   ALTER TABLE firma_solicitud DROP COLUMN IF EXISTS nivel_seguridad;

ALTER TABLE firma_solicitud
    ADD COLUMN IF NOT EXISTS nivel_seguridad SMALLINT NOT NULL DEFAULT 1
    CHECK (nivel_seguridad BETWEEN 1 AND 3);

COMMENT ON COLUMN firma_solicitud.nivel_seguridad IS
    'Snapshot del nivel de seguridad (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) del objeto firmado. '
    'Se captura al crear la cadena desde radicados.nivel_seguridad (o declarado para otros tipos). '
    'Todas las lecturas se gatean contra el clearance del actor (RF-SEG-08, D-15, no-read-up).';
