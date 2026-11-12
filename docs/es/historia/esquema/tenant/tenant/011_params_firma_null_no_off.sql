-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.



-- Tenant migration 011: corrige la siembra de la 010 (política XAdES por entidad).
-- search_path = tenant_{slug} (ADR-012).
--
-- La 010 sembró `firma_personal_require_xades = "off"`. Un valor presente en la
-- tabla GANA sobre el entorno (FIRMA_PERSONAL_REQUIRE_XADES), así que sembrar
-- "off" anulaba el respaldo del despliegue: un entorno con `todos` quedaba en
-- `off` en silencio. La 010 ya siembra JSON null (= «la entidad no lo define»);
-- esta migración lo aplica a los tenants donde la 010 vieja ya corrió.
--
-- Criterio para distinguir el "off" SEMBRADO del "off" ELEGIDO: la API
-- (`PUT /config/params/{key}`) escribe SIEMPRE una fila en `admin_audit`
-- (entity='param', entity_id=clave) en la misma transacción, mientras que la
-- siembra de la 010 no deja ninguna. Un "off" sin esa fila nunca lo tocó un
-- humano; con ella, es decisión de la entidad y NO se toca (aunque sea "off").
-- (`updated_by IS NULL` no sirve: la API puede escribir actor nulo.)
--
-- Idempotente: tras correr, el valor ya no es "off" (o hay fila de auditoría);
-- una segunda pasada no encuentra nada. El cambio deja su propia fila en
-- `admin_audit` (actor NULL = migración) para que sea visible.
WITH corregidos AS (
    UPDATE sgd_param_admin p
       SET value = 'null'::jsonb,
           updated_at = now()
     WHERE p.key = 'firma_personal_require_xades'
       AND p.value = '"off"'::jsonb
       AND NOT EXISTS (
            SELECT 1 FROM admin_audit a
             WHERE a.entity = 'param' AND a.entity_id = p.key
       )
 RETURNING p.key
)
INSERT INTO admin_audit (entity, entity_id, action, old_value, new_value, actor_id)
SELECT 'param', key, 'update', '"off"'::jsonb, 'null'::jsonb, NULL FROM corregidos;
