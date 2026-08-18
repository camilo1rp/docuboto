// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * flujo.js — Presentación de los pasos y eventos de trámite, fuente única.
 *
 * ## Dos mapas de estado, uno correcto y otro no
 *
 * `WorkflowCard` traducía bien los estados de paso; `HojaRuta` tenía su propia
 * copia con `active`, `completed` y `cancelled`. Pero **`active` no existe en el
 * backend** y faltaba `pending`, que es el valor por DEFECTO de la columna: cada
 * paso recién asignado se mostraba como «Oficina Jurídica pending», en inglés,
 * en la hoja de ruta del radicado.
 *
 * ## Los eventos vienen de DOS familias, y solo una estaba cubierta
 *
 * `flow_events.tipo_tx` mezcla:
 *  - los **tipos de transacción** del catálogo `workflow_transaction_types`
 *    (agendar, vobo, anular…), que la entidad configura y que la API ya
 *    devuelve con su `nombre` en `GET /workflows/transaction-types`; y
 *  - los **eventos de sistema** que emite el propio flujo: `assign`,
 *    `transfer`, `complete`, `devolucion`, `reassign`, `rollback`.
 *
 * El mapa del frontend cubría solo los doce del catálogo, así que las seis
 * operaciones más comunes —asignar, trasladar, completar, devolver— salían con
 * su código crudo en la trazabilidad.
 *
 * Las etiquetas del catálogo se conservan aquí como **reserva**: lo correcto es
 * preferir el `nombre` que manda la API, porque ese catálogo es configurable
 * por entidad y cualquier copia local queda obsoleta en cuanto alguien lo
 * cambie. Ver `etiquetaEventoFlujo`.
 */

/** @type {Record<string,string>} Estados reales de `flow_steps.status`. */
export const PASO_ESTADO_LABEL = {
  pending: "Pendiente",
  in_progress: "En trámite",
  completed: "Completado",
  cancelled: "Cancelado",
  returned: "Devuelto",
};

/** @type {Record<string,string>} Variante de StatusChip por estado de paso. */
export const PASO_ESTADO_CHIP = {
  pending: "warning",
  in_progress: "info",
  completed: "success",
  cancelled: "error",
  returned: "pending",
};

/** @type {Record<string,string>} Eventos que emite el propio flujo. */
export const EVENTO_SISTEMA_LABEL = {
  assign: "Asignación",
  transfer: "Traslado",
  complete: "Paso completado",
  devolucion: "Devolución",
  reassign: "Reasignación",
  rollback: "Reversión",
};

/** @type {Record<string,string>} Reserva de los tipos del catálogo configurable. */
export const TIPO_TX_LABEL = {
  informar: "Informar (copia)",
  borrar_informado: "Borrar informado",
  nrr: "No requiere respuesta",
  agendar: "Agendar",
  no_agendar: "Quitar de agenda",
  change_folder: "Cambiar de carpeta",
  marcar_leido: "Marcar como leído",
  validate_trd_send: "Validar TRD para envío",
  solicitar_firma: "Solicitar firma",
  vobo: "Visto bueno",
  cerrar_exp: "Cerrar expediente",
  anular: "Anular",
};

/**
 * Etiqueta de un estado de paso; ante uno desconocido devuelve el crudo.
 * @param {string|null|undefined} estado
 */
export function etiquetaPaso(estado) {
  if (!estado) return "—";
  return PASO_ESTADO_LABEL[estado] ?? estado;
}

/**
 * Etiqueta de un evento de trámite.
 *
 * `catalogo` es opcional y **tiene prioridad**: son los tipos que devuelve
 * `GET /workflows/transaction-types`, con el `nombre` que la entidad haya
 * configurado. Sin él se usa la reserva local, y en último término el código
 * crudo — que es feo, pero informa; un hueco no.
 *
 * @param {string|null|undefined} tipo
 * @param {Array<{code: string, nombre: string}>} [catalogo]
 */
export function etiquetaEventoFlujo(tipo, catalogo) {
  if (!tipo) return "—";
  const delCatalogo = catalogo?.find((t) => t.code === tipo)?.nombre;
  return delCatalogo ?? EVENTO_SISTEMA_LABEL[tipo] ?? TIPO_TX_LABEL[tipo] ?? tipo;
}
