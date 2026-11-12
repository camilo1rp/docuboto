-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 032: campos que quedaron sin datos al combinar la plantilla
-- (E23, RF-PRO-01). search_path = tenant_{slug} (ADR-012).
--
-- El defecto que esto cierra. La lista de campos sin resolver se calculaba al
-- combinar, viajaba en UNA respuesta HTTP y se perdía. El cuerpo se guarda YA
-- combinado —los marcadores sustituidos, los que no tenían dato por cadena
-- vacía—, así que volver a combinarlo no encuentra ningún marcador que
-- reportar: al reabrir el borrador no había aviso, y `X-Faltantes` llegaba
-- vacío en la previsualización.
--
-- Es decir: el aviso existía exactamente en el instante en que el hueco todavía
-- no importaba, y desaparecía para siempre justo antes de importar. Quien
-- abriera el borrador al día siguiente —o quien no fuera la persona que lo
-- creó— vería un espacio en blanco en mitad del oficio sin nada que le dijera
-- que ahí faltaba un dato. El docstring de `previsualizar` ya decía lo que
-- tenía que pasar: «sin esa lista, la persona vería un hueco en el documento
-- sin saber que es un hueco — y el oficio saldría así al ciudadano».
--
-- Se guarda como HECHO HISTÓRICO de la combinación, no como estado a mantener:
-- dice qué campos no tenían dato cuando la plantilla se combinó. Eso sigue
-- siendo cierto por mucho que después se edite el texto a mano, y por eso no se
-- limpia al editar: borrarlo a la primera pulsación quitaría el aviso
-- precisamente cuando alguien empieza a tocar el documento sin haber rellenado
-- necesariamente los huecos.
ALTER TABLE borrador
    ADD COLUMN IF NOT EXISTS faltantes TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN borrador.faltantes IS
    'Campos del vocabulario que no tenían dato al combinar la plantilla (E23, '
    'RF-PRO-01). Hecho histórico de la combinación: no se recalcula al editar.';
