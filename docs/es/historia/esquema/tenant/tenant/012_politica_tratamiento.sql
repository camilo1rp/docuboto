-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 012: política de tratamiento de datos personales, POR ENTIDAD
-- (B2, ADR-031; Ley 1581/2012 art. 12, DUR 1074/2015 art. 2.2.2.25.3.2).
-- search_path = tenant_{slug} (ADR-012).
--
-- La ventanilla pública recoge la autorización del titular, y esa constancia es
-- prueba: tiene que decir A QUÉ aviso de privacidad se dio. Hasta ahora la versión
-- y la URL de la política vivían en el entorno del FRONTEND (una sola para todas
-- las entidades) y el servidor aceptaba cualquier cadena como versión. Ahora la
-- política es de la entidad: document-service la sirve y la relee al radicar.
--
-- Se siembran VACÍAS (no con un texto de ejemplo): una política inventada en la
-- constancia de un tratamiento de datos es peor que un hueco, porque el hueco
-- se ve — y mientras esté vacía la ventanilla responde 409 politica_no_publicada
-- (sin aviso no hay autorización informada). Se editan por
-- PUT /config/params/{key} (administrador nivel 3, con asiento).
INSERT INTO sgd_param_admin (key, value) VALUES
    ('politica_tratamiento_version', '""'::jsonb),
    ('politica_tratamiento_url',     '""'::jsonb),
    ('politica_tratamiento_texto',   '""'::jsonb)
ON CONFLICT (key) DO NOTHING;
