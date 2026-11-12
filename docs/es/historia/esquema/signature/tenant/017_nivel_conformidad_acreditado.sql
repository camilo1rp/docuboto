-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se
-- entrega SIN GARANTÍA ALGUNA. Véase LICENSE y <https://www.gnu.org/licenses/>.
--
-- 017 — El CHECK admite los perfiles LT/LTA ACREDITADOS (E06).
--
-- Por qué ahora: la acreditación ONAC dejó de ser deuda del PRODUCTO y pasó a
-- ser propia de cada INSTALACIÓN — depende de que la entidad contrate un
-- operador de firma acreditado y aporte su material. Para que esa decisión sea
-- ejecutable, el sistema tiene que poder DECLARAR acreditado un sello que lo es
-- (`<signer_cert_dir>/<slug>/acreditacion.json`, app/core/keys.py). Hasta hoy
-- `acreditado` era un literal `False` y el perfil se escribía siempre
-- `*_no_acreditado`: una entidad podía montar un certificado de una CA
-- acreditada y el sistema seguía SUB-declarando la conformidad que pagó.
--
-- El 'xades_b_acreditado' ya estaba admitido desde la 008 («reservado»). Lo que
-- faltaba eran sus gemelos LT/LTA, que es donde acaba una firma con material de
-- validación a largo plazo: sin ellos, un sello acreditado + CA/CRL montadas
-- produciría 'xades_lta_acreditado' y el INSERT lo rechazaría — exactamente el
-- fallo que motivó la 016, un literal del código fuera del CHECK.
--
-- NO se añade 'xades_personal_acreditado', y no es un olvido: la firma personal
-- se custodia en el servidor, así que no hay CONTROL EXCLUSIVO del firmante
-- (Ley 527/1999 art. 28) por muy acreditado que esté el certificado. Ese camino
-- se abre con HSM o firma en el dispositivo, no con una entrada en esta lista.
--
-- Idempotente: no hay ADD CONSTRAINT IF NOT EXISTS para CHECK en Postgres, así
-- que se DROPea (IF EXISTS) y se re-crea siempre — mismo criterio que la 013 y
-- la 016. Tras el DROP el estado es idéntico, sea la primera corrida o una
-- repetición.
--
-- Rollback (manual, si hiciera falta):
--   ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
--   ALTER TABLE firma ADD CONSTRAINT firma_nivel_conformidad_check
--       CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
--           'firma_electronica_nativa', 'xades_b_no_acreditado',
--           'xades_b_acreditado', 'xades_lt_no_acreditado',
--           'xades_lta_no_acreditado', 'xades_personal_no_acreditado'));
--   -- Solo posible si no hay ya filas con los dos valores nuevos.

ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
ALTER TABLE firma
    ADD CONSTRAINT firma_nivel_conformidad_check
    CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
        'firma_electronica_nativa',
        'xades_b_no_acreditado',
        'xades_b_acreditado',
        'xades_lt_no_acreditado',
        'xades_lt_acreditado',
        'xades_lta_no_acreditado',
        'xades_lta_acreditado',
        'xades_personal_no_acreditado'  -- firma personal por-usuario (E17, art. 7)
    ));

COMMENT ON COLUMN firma.nivel_conformidad IS
    'Perfil de conformidad de la firma. ''firma_electronica_nativa'' = HMAC '
    'de retrocompatibilidad; ''xades_b_*''/''xades_lt_*''/''xades_lta_*'' = '
    'sello INSTITUCIONAL del tenant; ''xades_personal_no_acreditado'' = firma '
    'PERSONAL por-usuario (E17), con certificado de la sub-CA de usuarios y '
    'segundo factor TOTP. El sufijo ''_acreditado'' lo produce ÚNICAMENTE un '
    'sello institucional cuyo directorio declara la acreditación en '
    'acreditacion.json (material de un operador acreditado ONAC aportado por '
    'la entidad); el sello de DESARROLLO nunca lo alcanza. La firma personal '
    'no tiene variante acreditada: con custodia en servidor no hay control '
    'exclusivo del firmante (art. 28), independientemente del certificado. '
    'Cuando el firmador añade material LT/LTA promueve el valor a '
    '''xades_lta_*'' (app/providers/local.py), también en la firma personal: '
    'la distinción personal/institucional se conserva en `firma.tipo_firma`.';
