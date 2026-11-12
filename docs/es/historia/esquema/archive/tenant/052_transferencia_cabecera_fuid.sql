-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 052 (E12/E17): cabecera del FUID de la transferencia.
-- search_path = tenant_{slug} (ADR-012).
--
-- El Anexo FUID del Acuerdo AGN 001/2024 abre con tres datos: unidad
-- administrativa, oficina productora y objeto de la transferencia. Los tres
-- existían como parámetros de `generate_fuid` y NADIE los enviaba:
-- `TransferenciaService.recibir` congelaba el acta, le calculaba el SHA-256 y
-- la sellaba en XAdES-T con la cabecera EN BLANCO. Un acta de entrega que no
-- dice de qué oficina salen los documentos no identifica la entrega.
--
-- Por qué se guardan AQUÍ y no se resuelven al congelar el acta: la oficina
-- productora es la de quien PREPARA la transferencia (quien entrega), y para
-- cuando alguien recibe pueden haber pasado semanas — el preparador pudo
-- cambiar de dependencia, o la dependencia pudo renombrarse. Se captura el
-- SNAPSHOT en el instante de la preparación, mismo criterio que los tres
-- responsables del acta (migración 022): un acta es autocontenida o no es un
-- acta.

ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS objeto TEXT;

ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS oficina_productora_codigo INTEGER;
ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS oficina_productora_nombre TEXT;

ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS unidad_administrativa_codigo INTEGER;
ALTER TABLE transferencias
    ADD COLUMN IF NOT EXISTS unidad_administrativa_nombre TEXT;

COMMENT ON COLUMN transferencias.objeto IS
    'Objeto/proposito de la transferencia (Anexo FUID, Ac. AGN 001/2024). Lo escribe quien prepara: es un dato humano que ninguna derivacion puede inventar. NULL solo en las transferencias anteriores a esta migracion.';
COMMENT ON COLUMN transferencias.oficina_productora_nombre IS
    'Snapshot de la dependencia ACTIVA de quien prepara la transferencia (auth_users.active_depe_id -> dependencias). Es quien ENTREGA los documentos, no quien los recibe.';
COMMENT ON COLUMN transferencias.unidad_administrativa_nombre IS
    'Snapshot de la dependencia PADRE de la oficina productora (dependencias.parent_id); si la oficina no tiene padre, se repite la propia -- una oficina raiz ES su unidad administrativa.';

-- NOT NULL en `objeto` se deja FUERA a proposito: las transferencias ya
-- existentes no lo tienen y no hay valor honesto con el que rellenarlas
-- (inventar un objeto en un acta es exactamente lo que este arreglo evita).
-- La obligatoriedad se aplica en la ENTRADA (`TransferenciaCreate.objeto`,
-- min_length=3), que es donde hay alguien a quien pedirselo.

-- Rollback manual (no automatizado):
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS objeto;
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS oficina_productora_codigo;
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS oficina_productora_nombre;
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS unidad_administrativa_codigo;
-- ALTER TABLE transferencias DROP COLUMN IF EXISTS unidad_administrativa_nombre;
