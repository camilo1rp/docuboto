-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 010: política XAdES de la firma, POR ENTIDAD (AREA-04-12, ADR-016).
-- search_path = tenant_{slug} (ADR-012).
--
-- `firma_personal_require_xades` ∈ {off, clasificados, todos} la lee
-- signature-service desde esta tabla en cada firma de cadena:
--   off          — un acto puede firmarse con HMAC nativo si el firmante no tiene
--                  certificado personal o el canal de credenciales está caído
--                  (queda el asiento firma.fallback_nativa_canal_caido).
--   clasificados — nivel_seguridad >= 2 exige certificado XAdES personal; sin él
--                  422 firma_xades_requerida, con el canal caído 503
--                  canal_firma_personal_no_disponible (fail-closed, nunca nativa).
--   todos        — igual en cualquier nivel.
--
-- Es decisión de la ENTIDAD (depende de si ya enroló los certificados de sus
-- firmantes): por eso vive aquí, editable en /admin/parametros, y no solo en el
-- entorno del despliegue. La clave se SIEMBRA aquí porque un parámetro cuyo
-- nombre solo vive en el código no se puede configurar. Se siembra JSON null
-- («la entidad no lo define»; signature-service lo lee como None y manda el
-- ENTORNO, cuyo default 'off' es el comportamiento de siempre). Sembrar '"off"'
-- anulaba el respaldo en silencio: un despliegue con FIRMA_PERSONAL_REQUIRE_XADES=todos
-- quedaba en off para toda entidad (corregido para las ya sembradas por la 011). El env FIRMA_PERSONAL_REQUIRE_XADES de
-- signature-service queda como respaldo SOLO para el tenant donde falte la fila.
INSERT INTO sgd_param_admin (key, value) VALUES
    ('firma_personal_require_xades', 'null'::jsonb)
ON CONFLICT (key) DO NOTHING;
