-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 009: identificación de la entidad para el membrete de los
-- documentos producidos (E23, RF-PRO-04).
-- search_path = tenant_{slug} (ADR-012).
--
-- Los siete marcadores del grupo «Entidad» —*NOM_E*, *SIGLA_E*, *NIT_E*,
-- *DIR_E*, *TEL_E*, *MAIL_E*, *WEB_E*— se resuelven desde estas claves
-- (`PARAMS_ENTIDAD` en document-service). Hasta ahora los nombres de las claves
-- existían ÚNICAMENTE dentro de ese diccionario de Python: ninguna migración
-- las creaba, así que `/admin/parametros` —que muestra las filas que hay— no
-- las ofrecía, y no había forma de configurarlas. El membrete de todo oficio
-- salía en blanco y nadie podía rellenarlo.
--
-- Se siembran en vacío, no con datos de ejemplo: un NIT inventado en el
-- membrete de un documento oficial es peor que un hueco, porque el hueco se ve.
INSERT INTO sgd_param_admin (key, value) VALUES
    ('entidad_nombre',    '""'::jsonb),
    ('entidad_sigla',     '""'::jsonb),
    ('entidad_nit',       '""'::jsonb),
    ('entidad_direccion', '""'::jsonb),
    ('entidad_telefono',  '""'::jsonb),
    ('entidad_email',     '""'::jsonb),
    ('entidad_web',       '""'::jsonb)
ON CONFLICT (key) DO NOTHING;
