-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10, RF-PRE-04): la política de obsolescencia pasa a
-- vivir en el PLAN DE PRESERVACIÓN de cada entidad.
--
-- El barrido periódico de migración ya existía, pero solo sabía leer una
-- variable de entorno del despliegue (`FORMAT_MIGRATION_MIME_TYPES`). Eso
-- contradecía lo que el propio código declaraba: que la política la decide la
-- ENTIDAD. Una variable de entorno es del DESPLIEGUE — es la misma para todas
-- las entidades que comparten la instalación, no la puede tocar el archivista,
-- no queda versionada y no dice quién la declaró ni cuándo.
--
-- `formatos_origen` es el otro extremo de `formatos_destino`, que ya estaba
-- aquí desde la primera versión del plan: qué se convierte, no solo a qué. Al
-- vivir en `preservacion_plan` hereda las tres propiedades que hacían falta:
-- versionado append-only (vigente = MAX(version)), autoría (`creado_por`) y
-- fecha. Cambiar la política de conversión del archivo de una entidad es un
-- acto archivístico y tiene que poder mirarse hacia atrás.
--
-- Por defecto VACÍO: una entidad que no ha declarado nada sigue sin migrar
-- nada (el barrido cae entonces al valor del despliegue, si lo hay). Migrar no
-- destruye —el original se conserva intacto, Decreto 2609/2012 art. 32— pero
-- lo peligroso aquí nunca fue convertir de más, es CREER que se convirtió.

ALTER TABLE preservacion_plan
    ADD COLUMN IF NOT EXISTS formatos_origen TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN preservacion_plan.formatos_origen IS
    'Tipos MIME que la entidad declara en riesgo de obsolescencia (RF-PRE-04). '
    'Vacío = política no declarada en el plan.';
