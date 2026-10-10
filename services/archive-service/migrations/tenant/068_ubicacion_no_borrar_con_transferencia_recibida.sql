-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 068 (B4 re-auditoría; Ley 594/2000 art. 26, Anexo FUID Ac. AGN 001/2024):
-- la FK `transferencias.ubicacion_{origen,destino}_id ... ON DELETE SET NULL` (012) borraba,
-- al eliminar una ubicación, la TRAZA FÍSICA de una transferencia ya RECIBIDA: el acta FUID
-- atestigua un movimiento entre dos depósitos y la fila que lo respalda quedaba sin ellos.
-- Un trigger BEFORE DELETE sobre `ubicacion` rechaza (23514) borrar una ubicación referenciada
-- por una transferencia en estado `recibida`. Las demás transferencias (preparada/enviada/
-- rechazada) conservan el comportamiento SET NULL de la 012. No se cambia la FK.
--
-- Idempotente (CREATE OR REPLACE + DROP TRIGGER IF EXISTS). search_path = tenant_{slug}.

CREATE OR REPLACE FUNCTION ubicacion_guard_transferencia_recibida() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.estado = 'recibida'
                  AND (t.ubicacion_origen_id = OLD.id OR t.ubicacion_destino_id = OLD.id)) THEN
        RAISE EXCEPTION 'ubicacion_con_transferencia_recibida'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_ubicacion_traza_transferencia_recibida';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ubicacion_guard_transferencia_recibida ON ubicacion;
CREATE TRIGGER trg_ubicacion_guard_transferencia_recibida
    BEFORE DELETE ON ubicacion
    FOR EACH ROW EXECUTE FUNCTION ubicacion_guard_transferencia_recibida();

-- Rollback manual (no automatizado):
-- DROP TRIGGER IF EXISTS trg_ubicacion_guard_transferencia_recibida ON ubicacion;
-- DROP FUNCTION IF EXISTS ubicacion_guard_transferencia_recibida();
