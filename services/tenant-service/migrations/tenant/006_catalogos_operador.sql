-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 006: catálogos de operador (E14 + paridad legado).
-- Causales (devolución/anulación/reasignación), formas de envío, soportes y
-- mensajes rápidos. Mismo patrón genérico (codigo/nombre/activo). search_path tenant.

CREATE TABLE IF NOT EXISTS causales (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(15) NOT NULL UNIQUE, nombre VARCHAR(150) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS formas_envio (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(15) NOT NULL UNIQUE, nombre VARCHAR(150) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS soportes (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(15) NOT NULL UNIQUE, nombre VARCHAR(150) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS mensajes_rapidos (
    id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo VARCHAR(15) NOT NULL UNIQUE, nombre VARCHAR(150) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO causales (codigo, nombre) VALUES
    ('DEV-DEP','Devolución: dirigido a otra dependencia'),
    ('DEV-INC','Devolución: información/anexos incompletos'),
    ('DEV-NOC','Devolución: no es competencia de la entidad'),
    ('ANU-ERR','Anulación: error de radicación'),
    ('ANU-DUP','Anulación: radicado duplicado'),
    ('REA-COM','Reasignación: por competencia')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO formas_envio (codigo, nombre) VALUES
    ('FIS','Físico'), ('EMA','Correo electrónico'), ('POS','Operador postal'), ('FAX','Fax')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO soportes (codigo, nombre) VALUES
    ('PAP','Papel'), ('ELE','Electrónico'), ('HIB','Híbrido')
ON CONFLICT (codigo) DO NOTHING;
