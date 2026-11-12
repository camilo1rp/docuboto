-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17, F5, ADR-017): modelo de archivo físico nuclear.
--
-- Patrón ArchivesSpace: Ubicación (dirección física recursiva) separada de Unidad
-- de conservación (contenedor móvil con signatura topográfica). Vínculo N:M
-- expediente↔unidad con rango de folios. Préstamos, historial, FUID y rótulo son
-- incrementos posteriores de E17.

CREATE TABLE IF NOT EXISTS ubicacion (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id    UUID REFERENCES ubicacion(id) ON DELETE CASCADE,
    nivel        VARCHAR(30) NOT NULL,           -- sede|edificio|...|gaveta|custodia_externa
    codigo       VARCHAR(30) NOT NULL UNIQUE,    -- único por tenant
    nombre       TEXT NOT NULL,
    tipo_archivo VARCHAR(20),                    -- gestion|central|historico
    capacidad_ml NUMERIC(10,2),
    activo       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ubicacion_parent ON ubicacion (parent_id);

CREATE TABLE IF NOT EXISTS unidad_conservacion (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id    UUID REFERENCES unidad_conservacion(id) ON DELETE CASCADE,
    ubicacion_id UUID REFERENCES ubicacion(id) ON DELETE SET NULL,
    tipo         VARCHAR(20) NOT NULL,           -- caja|carpeta|legajo|libro|tomo|az|folder
    codigo       VARCHAR(30) NOT NULL,
    signatura    VARCHAR(80) UNIQUE,             -- topográfica, derivada de la ruta + código
    soporte      VARCHAR(30),
    num_folios   INTEGER,
    activo       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_unidad_ubicacion ON unidad_conservacion (ubicacion_id);
CREATE INDEX IF NOT EXISTS ix_unidad_parent ON unidad_conservacion (parent_id);

CREATE TABLE IF NOT EXISTS expediente_unidad (
    expediente_id UUID NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    unidad_id     UUID NOT NULL REFERENCES unidad_conservacion(id) ON DELETE CASCADE,
    folio_inicio  INTEGER,
    folio_fin     INTEGER,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (expediente_id, unidad_id)
);
CREATE INDEX IF NOT EXISTS ix_exp_unidad_unidad ON expediente_unidad (unidad_id);
