// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * apiError.js — Leer el error de una respuesta del gateway, venga como venga.
 *
 * ## El sobre doble
 *
 * El formato de error del proyecto es `{error, detail, status}`. Pero los
 * routers lo levantan con `HTTPException(detail={...})`, y FastAPI serializa
 * **eso** dentro de su propio `detail`:
 *
 *     {"detail": {"error": "metadata_validation_failed", "detail": "…", "status": 422}}
 *
 * Los proxies BFF reenvían el cuerpo tal cual (`json(data, {status})`), así que
 * el sobre doble llega intacto al navegador. El frontend, en cambio, comparaba
 * `err.error` — el campo de PRIMER nivel, que no existe.
 *
 * Consecuencias medidas, no supuestas:
 *  - `HojaRuta` no reconocía `no_active_step` y un radicado recién radicado
 *    —sin flujo todavía, algo normal— decía «No se pudo cargar el paso actual»
 *    en vez de «Sin paso activo». El estado vacío correcto estaba escrito y no
 *    llegaba a pintarse nunca.
 *  - `/radicar` hacía `err?.detail ?? "Error al crear el radicado"` para el
 *    aviso: como `detail` es un OBJETO, el mensaje salía como
 *    «[object Object]», y el mapeo del error al campo concreto no se activaba.
 *
 * Estas funciones aceptan las dos formas a propósito: la plana existe de verdad
 * (errores que fabrica el propio proxy BFF, p. ej. `connection_error`) y la
 * anidada es la que produce el backend. Elegir una sola volvería a partir el
 * manejo de errores por la mitad.
 *
 * ## Y una tercera: la validación de Pydantic
 *
 * Cuando el que rechaza es un validador del esquema, FastAPI no usa el sobre
 * del proyecto: responde 422 con una LISTA.
 *
 *     {"detail": [{"type": "value_error", "loc": ["body"],
 *                  "msg": "Value error, fundamento_juridico es obligatorio…"}]}
 *
 * Ninguna de las dos ramas anteriores la reconocía, así que el aviso caía al
 * texto por defecto. Medido en la reclasificación de un radicado: el backend
 * decía exactamente qué faltaba y por qué —«fundamento_juridico es obligatorio
 * al reclasificar a nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28)»— y la
 * pantalla mostraba «Error 422 al reclasificar el nivel de seguridad». La
 * persona veía que no se podía, sin ninguna vía para averiguar qué le faltaba.
 * Afecta a TODA validación de esquema del sistema, no a esa pantalla.
 */

/**
 * Prefijo que Pydantic antepone a los `ValueError` que lanza un validador
 * ESCRITO POR NOSOTROS. Es lo que separa las dos clases de error de validación,
 * y la distinción importa:
 *
 *  - Con prefijo: el mensaje lo redactó este proyecto, en español y pensado
 *    para quien está delante («fundamento_juridico es obligatorio al
 *    reclasificar a nivel_seguridad >= 2, Ley 1712/2014 art. 19/28»). Mostrarlo
 *    es justo lo que falta cuando el aviso genérico deja a la persona sin saber
 *    qué corregir.
 *  - Sin prefijo: lo generó Pydantic solo, por tipo o formato («invalid uuid»,
 *    «field required»). Está en inglés, nombra el campo interno y no dice nada
 *    accionable: para eso es mejor el texto por defecto de la pantalla, que al
 *    menos explica qué operación falló.
 */
const PREFIJO_VALIDADOR_PROPIO = /^Value error,\s*/;

/**
 * Mensajes de una lista de errores de validación de FastAPI/Pydantic,
 * quedándose SOLO con los que escribió este proyecto.
 * @param {any} detail
 * @returns {string|null}
 */
function mensajeDeValidacion(detail) {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const mensajes = detail
    .map((e) => (typeof e?.msg === "string" ? e.msg : ""))
    .filter((m) => PREFIJO_VALIDADOR_PROPIO.test(m))
    .map((m) => m.replace(PREFIJO_VALIDADOR_PROPIO, "").trim())
    .filter(Boolean);
  if (mensajes.length === 0) return null;
  // Varios campos inválidos a la vez se enumeran: quedarse con el primero
  // dejaría a la persona corrigiendo de uno en uno sin saber cuántos faltan.
  return mensajes.join(" · ");
}

/**
 * Código de error (`error`) de un cuerpo de respuesta, plano o anidado.
 * @param {any} data
 * @returns {string|null}
 */
export function codigoError(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.error === "string") return data.error;
  if (data.detail && typeof data.detail === "object" && typeof data.detail.error === "string") {
    return data.detail.error;
  }
  return null;
}

/**
 * Propiedad a la que el backend atribuye un 422 de metadatos (`field`, ruta con
 * puntos: `a.b`, `lista.1`), en el sobre plano o en el anidado. `null` si el
 * fallo no es atribuible a una propiedad concreta.
 * @param {any} data
 * @returns {string|null}
 */
export function campoError(data) {
  if (!data || typeof data !== "object") return null;
  const campo =
    typeof data.field === "string"
      ? data.field
      : data.detail && typeof data.detail === "object" && typeof data.detail.field === "string"
        ? data.detail.field
        : null;
  return campo || null;
}

/**
 * Códigos de los actos de trámite (ADR-028) con su mensaje para la persona.
 * Se consultan ANTES que el `detail` del backend: el texto ya viene en español
 * pero habla del sistema, y aquí hay que decir qué hacer. `no_active_step` es a
 * propósito ambiguo —«no hay paso» y «no es suyo» son el mismo 404 sin oráculo—.
 * @type {Record<string,string>}
 */
export const MENSAJES_ACTO_TRAMITE = {
  radicado_terminal: "El radicado está cerrado o anulado y ya no admite actos de trámite.",
  no_active_step: "Este trámite no está a su cargo: no tiene paso activo o lo posee otra persona.",
  no_flow:
    "No se encontró el flujo de este radicado: no existe, no tiene pasos o está por encima de su nivel de acceso.",
  step_not_found:
    "Ese paso ya no está abierto o no está a su cargo. Actualice la bandeja para ver el estado actual.",
  radicado_not_found: "No se encontró el radicado, o está por encima de su nivel de acceso.",
  status_managed_by_workflow:
    "El estado del radicado lo mueve el flujo de trámite y no se edita a mano. Use «Marcar en trámite» o «Finalizar trámite».",
};

/**
 * Mensaje legible de un cuerpo de respuesta. **Nunca devuelve un objeto**: ese
 * es justo el fallo que motivó este módulo — un `[object Object]` en un aviso
 * de error deja a la persona sin saber qué pasó ni cómo arreglarlo.
 * @param {any} data
 * @param {string} porDefecto
 * @returns {string}
 */
export function mensajeError(data, porDefecto) {
  if (typeof data === "string" && data.trim()) return data;
  if (data && typeof data === "object") {
    const delActo = MENSAJES_ACTO_TRAMITE[codigoError(data) ?? ""];
    if (delActo) return delActo;
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    const validacion = mensajeDeValidacion(data.detail);
    if (validacion) return validacion;
    if (data.detail && typeof data.detail === "object" && typeof data.detail.detail === "string") {
      return data.detail.detail;
    }
    const codigo = codigoError(data);
    if (codigo) return codigo;
  }
  return porDefecto;
}
