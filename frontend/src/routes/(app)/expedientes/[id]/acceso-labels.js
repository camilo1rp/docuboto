// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Vocabulario del historial de acceso del expediente (RF-BUS-10).
 *
 * **Vive en un módulo aparte, y no dentro del `.svelte`, por lo que pasó con la
 * versión anterior**: el mapa usaba claves —`read`, `download`, `verify`— que
 * no correspondían a ninguna acción real de `audit_log`, así que todas caían al
 * `humanize` y nadie se enteró. Peor: la entrada "Descarga" hacía creer que la
 * pestaña ya mostraba las descargas de anexos, que era justo lo que NO
 * mostraba. Un diccionario dentro de una plantilla no se puede probar; aquí sí,
 * y su test exige que toda clave sea un nombre de acción del backend.
 *
 * Las claves son los nombres EXACTOS de `audit_log.action`.
 */

export const ACCESO_ACCION_LABELS = {
  "archive.expediente_consultado": "Lectura del expediente",
  "archive.indice_consultado": "Lectura del índice electrónico",
  "archive.expediente_exportado": "Exportación del expediente",
  "archive.aip_consultado": "Consulta del AIP",
  "archive.preservacion_eventos_consultados": "Consulta de eventos de preservación",
  // Las dos que faltaban: la traza existía en document-service desde el
  // principio y el historial del expediente no la pedía.
  "document.radicado_consultado": "Lectura del documento",
  "document.anexo_descargado": "Descarga de un anexo",
};
