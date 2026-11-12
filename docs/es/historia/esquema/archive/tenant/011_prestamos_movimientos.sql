-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17 RF-ARF-07/08): préstamos e historial de movimientos.
--
-- Préstamos de unidades de conservación (prestado → devuelto; "vencido" se deriva
-- de la fecha esperada) e historial APPEND-ONLY de movimientos físicos
-- (archivado/trasladado/retirado/devuelto/transferido) ligado a la auditoría.

CREATE TABLE IF NOT EXISTS prestamo (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unidad_id                 UUID NOT NULL REFERENCES unidad_conservacion(id) ON DELETE CASCADE,
    prestado_a                TEXT NOT NULL,
    prestado_por              TEXT,
    fecha_prestamo            TIMESTAMPTZ NOT NULL DEFAULT now(),
    fecha_devolucion_esperada DATE NOT NULL,
    fecha_devolucion_real     TIMESTAMPTZ,
    estado                    VARCHAR(12) NOT NULL DEFAULT 'prestado'
                              CHECK (estado IN ('prestado','devuelto')),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_prestamo_unidad ON prestamo (unidad_id);
CREATE INDEX IF NOT EXISTS ix_prestamo_estado
    ON prestamo (estado, fecha_devolucion_esperada);

CREATE TABLE IF NOT EXISTS movimiento_fisico (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unidad_id        UUID NOT NULL REFERENCES unidad_conservacion(id) ON DELETE CASCADE,
    accion           VARCHAR(20) NOT NULL,   -- ARCHIVADO|TRASLADADO|RETIRADO|DEVUELTO|TRANSFERIDO
    ubicacion_before UUID,
    ubicacion_after  UUID,
    performed_by     TEXT,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_movimiento_unidad ON movimiento_fisico (unidad_id, ts);
