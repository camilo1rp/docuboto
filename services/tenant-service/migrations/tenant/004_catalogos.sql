-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 004: catálogos de referencia — E14.
-- search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS tipos_identificacion (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(10) NOT NULL UNIQUE, nombre VARCHAR(100) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS tipos_remitente (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(10) NOT NULL UNIQUE, nombre VARCHAR(100) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS medios_recepcion (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(10) NOT NULL UNIQUE, nombre VARCHAR(100) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS tipos_anexo (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(10) NOT NULL UNIQUE, nombre VARCHAR(100) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO medios_recepcion (codigo, nombre) VALUES
    ('VEN','Ventanilla'), ('COR','Correo'), ('EMA','Email'), ('FAX','Fax'), ('WEB','Web')
ON CONFLICT (codigo) DO NOTHING;
