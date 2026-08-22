-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17/F4): admitir 'xades_personal_no_acreditado' en
-- `firma_nivel_conformidad_check`.
--
-- DEFECTO QUE CORRIGE — la firma personal por-usuario nunca llegó a
-- persistirse contra una base real. `PersonalSigningService` firma el
-- payload, lo sella con TSA, lo sube a MinIO... y el INSERT final revienta
-- con CheckViolationError, porque `SignerRef.nivel_conformidad` vale
-- 'xades_personal_no_acreditado' (app/services/personal_signing.py) y ese
-- valor no lo añadió NINGUNA de las tres migraciones que han tocado este
-- CHECK: la 008 lo creó con los tres valores del sello institucional, la 013
-- le sumó LT/LTA, y la 015 solo lo repone si falta. El incremento de firma
-- personal añadió el productor y se olvidó del consumidor.
--
-- Por qué no lo cazó ningún test: los nueve que usan el literal
-- (test_personal_signing_revocation, test_cadena_personal_signing y los seis
-- de archive-service/test_transferencia_acta_firma_personal) conducen el
-- repositorio con la base SIMULADA — un mock acepta cualquier cadena. Una
-- restricción de integridad no se prueba con mocks, igual que la atomicidad.
-- La protección contra recaída NO es esta migración sino el censo de
-- tests/test_nivel_conformidad_admitido.py, que compara los literales que el
-- código puede escribir contra los que el SQL admite.
--
-- El efecto se veía en pantalla como «signature-service no está disponible
-- para la firma personal» (503), a dos servicios de distancia de la causa.
--
-- NO se reserva un gemelo 'xades_personal_acreditado' —a diferencia de lo que
-- hizo la 008 con 'xades_b_acreditado'— porque no existe ninguna ruta que
-- pueda producirlo: la acreditación ONAC exige una CA acreditada que no está
-- integrada, y el día que se integre necesitará su propia migración de todos
-- modos. Un CHECK que admite lo que nadie escribe no protege nada.
--
-- Idempotente: no hay ADD CONSTRAINT IF NOT EXISTS para CHECK en Postgres, así
-- que se DROPea (IF EXISTS) y se re-crea siempre — mismo criterio que la 013.
-- Tras el DROP el estado es idéntico, sea la primera corrida o una repetición.
--
-- Rollback (manual, si hiciera falta):
--   ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
--   ALTER TABLE firma ADD CONSTRAINT firma_nivel_conformidad_check
--       CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
--           'firma_electronica_nativa', 'xades_b_no_acreditado',
--           'xades_b_acreditado', 'xades_lt_no_acreditado',
--           'xades_lta_no_acreditado'));
--   -- Solo posible si no hay ya filas 'xades_personal_no_acreditado'.

ALTER TABLE firma DROP CONSTRAINT IF EXISTS firma_nivel_conformidad_check;
ALTER TABLE firma
    ADD CONSTRAINT firma_nivel_conformidad_check
    CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
        'firma_electronica_nativa',
        'xades_b_no_acreditado',
        'xades_b_acreditado',        -- reservado: sin CA acreditada ONAC integrada
        'xades_lt_no_acreditado',
        'xades_lta_no_acreditado',
        'xades_personal_no_acreditado'  -- firma personal por-usuario (E17, art. 7 no acreditado)
    ));

COMMENT ON COLUMN firma.nivel_conformidad IS
    'Perfil de conformidad de la firma. ''firma_electronica_nativa'' = HMAC '
    'de retrocompatibilidad; ''xades_b_*''/''xades_lt_*''/''xades_lta_*'' = '
    'sello INSTITUCIONAL del tenant; ''xades_personal_no_acreditado'' = firma '
    'PERSONAL por-usuario (E17), con certificado de la sub-CA de usuarios y '
    'segundo factor TOTP. Ninguno de los perfiles ''no_acreditado'' proviene '
    'de una PKI acreditada ONAC (art. 7, no art. 28). Cuando el firmador '
    'añade material LT/LTA promueve el valor a ''xades_lta_no_acreditado'' '
    '(app/providers/local.py), también en la firma personal: la distinción '
    'personal/institucional se conserva entonces en `firma.tipo_firma`.';
