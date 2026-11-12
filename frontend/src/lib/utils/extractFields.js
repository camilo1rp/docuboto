// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { resolveSchemaControl } from "./schemaField.js";

/** Tope del proveedor para un archivo de preparación (100 MB). */
export const TOPE_INGESTA_BYTES = 104857600;

/** Intentos de POST extract: el índice puede adelantarse al texto, o el plazo cortarse. */
export const EXTRACT_INTENTOS = 3;

/** Espera base entre reintentos, en ms. Cada intento duplica esta espera. */
export const EXTRACT_ESPERA_MS = 800;

/**
 * Plazo de UN intento de extracto, en ms. Tiene que superar el del servidor
 * (`PLAZO_EXTRACT_S` = 90 s en `clients/ppi.py`) para que llegue su error y no
 * el del navegador: así la persona ve el mensaje del proveedor y no un
 * «connection_error» indistinguible de una caída de red.
 */
export const EXTRACT_PLAZO_MS = 95_000;

/**
 * Presupuesto TOTAL de la extracción, en ms. Sin él, reintentar un plazo
 * agotado multiplica la espera: 90 s + espera + 90 s + espera + 90 s ≈ 271 s
 * de rueda girando, y cada intento abandonado deja una petición huérfana viva
 * en el servidor. Agotado el presupuesto no se reintenta aunque queden intentos.
 */
export const EXTRACT_PLAZO_TOTAL_MS = 110_000;

/**
 * ¿Vale la pena reintentar este fallo?
 *
 * Solo si es probable que se resuelva solo y rápido:
 *
 * - `409`: el índice del proveedor va por detrás del texto; esperar y repetir
 *   es exactamente la respuesta correcta.
 * - `503`/`504` de infraestructura (`upstream_unavailable`, `connection_error`):
 *   un parpadeo; el reintento es barato porque el fallo fue rápido.
 *
 * NO se reintenta un plazo agotado (`extract_timeout`, o `proveedor`, que es
 * como el router traduce el plazo del proveedor): repetirlo no lo acelera, lo
 * triplica, y es justo el caso que el presupuesto total existe para acotar.
 *
 * @param {number} status
 * @param {string|null} [error] código `error` del cuerpo
 */
export function extractoSeReintenta(status, error) {
  if (status === 409) return true;
  if (error === "extract_timeout" || error === "proveedor") return false;
  return status === 503 || status === 504;
}

/**
 * Clave que viaja a extract → campo del formulario. Extract pide hechos del
 * documento (`details`); el radicado los guarda en observaciones.
 */
export const EXTRACT_A_FORMULARIO = Object.freeze({
  details: "observations",
});

/** @param {string} extractKey */
export function claveFormulario(extractKey) {
  return EXTRACT_A_FORMULARIO[extractKey] ?? extractKey;
}

/**
 * @typedef {{ value: string, label: string }} Opcion
 * @typedef {{ key: string, kind: 'choice', label: string, choices: Opcion[] } | { key: string, kind: 'text'|'number'|'date', label: string }} CampoExtracto
 */

/**
 * Quita la opción vacía («— seleccionar —») que el formulario usa como
 * placeholder: el contrato de extract exige al menos una opción real.
 * @param {Opcion[] | undefined} options
 * @returns {Opcion[]}
 */
export function opcionesCerradas(options) {
  return (options ?? []).filter((o) => typeof o?.value === "string" && o.value !== "");
}

/**
 * @param {string} key
 * @param {string} label
 * @param {Opcion[]} options
 * @returns {CampoExtracto | null}
 */
function choice(key, label, options) {
  const choices = opcionesCerradas(options);
  if (!choices.length) return null;
  return { key, kind: "choice", label, choices };
}

/**
 * Campos fijos de Tipo y Metadatos. Actor search y DIVIPOLA no entran:
 * no son una lista cerrada en la página.
 *
 * @param {{
 *   direccion: Opcion[],
 *   tipos: Opcion[],
 *   niveles: Opcion[],
 *   dependencias: Opcion[],
 * }} catalogos
 * @returns {CampoExtracto[]}
 */
export function camposBase(catalogos) {
  return /** @type {CampoExtracto[]} */ (
    [
      choice("direction", "Tipo de radicado", catalogos.direccion),
      choice("doc_class", "Tipo documental", catalogos.tipos),
      choice("nivel_seguridad", "Nivel de seguridad", catalogos.niveles),
      choice("origin_dept_code", "Dependencia origen", catalogos.dependencias),
      choice("dest_dept_code", "Dependencia destino", catalogos.dependencias),
      { key: "subject", kind: "text", label: "Asunto" },
      { key: "sender_name", kind: "text", label: "Remitente" },
      { key: "sender_entity", kind: "text", label: "Entidad" },
      { key: "pages", kind: "number", label: "Folios" },
      { key: "response_days", kind: "number", label: "Plazo" },
      {
        key: "details",
        kind: "text",
        label: "Hechos y detalles que constan en el documento",
      },
    ].filter(Boolean)
  );
}

/**
 * Campos de la plantilla del tipo documental. Un `select` (enum o boolean)
 * viaja como choice; el resto, tipado y sin opciones.
 *
 * @param {Record<string, any> | undefined | null} properties
 * @returns {CampoExtracto[]}
 */
export function camposPlantilla(properties) {
  if (!properties || typeof properties !== "object") return [];
  const fields = [];
  for (const [key, schema] of Object.entries(properties)) {
    const campo = campoDesdeSchema(key, schema);
    if (campo) fields.push(campo);
  }
  return fields;
}

/**
 * @param {string} key
 * @param {any} schema
 * @returns {CampoExtracto | null}
 */
export function campoDesdeSchema(key, schema) {
  const control = resolveSchemaControl(schema);
  if (!control || control.kind === "array" || control.kind === "object") return null;
  const label = typeof schema?.title === "string" && schema.title ? schema.title : key;
  if (control.kind === "select") {
    return choice(key, label, control.options ?? []);
  }
  if (control.kind === "date") return { key, kind: "date", label };
  if (control.kind === "number") return { key, kind: "number", label };
  return { key, kind: "text", label };
}

/**
 * Conserva solo claves pedidas. En un choice, un valor fuera de la lista
 * se descarta (queda vacío). Un campo ausente o nulo no se marca.
 *
 * @param {Record<string, unknown> | undefined | null} values
 * @param {CampoExtracto[]} fields
 * @returns {{ applied: Record<string, string>, marked: string[] }}
 */
export function aplicarExtracto(values, fields) {
  /** @type {Record<string, string>} */
  const applied = {};
  /** @type {string[]} */
  const marked = [];
  const porClave = new Map(fields.map((f) => [f.key, f]));
  for (const [key, raw] of Object.entries(values ?? {})) {
    const field = porClave.get(key);
    if (!field) continue;
    if (raw === null || raw === undefined || raw === "") continue;
    const asString = String(raw);
    if (field.kind === "choice") {
      const permitido = field.choices.some((c) => c.value === asString);
      if (!permitido) continue;
    }
    const formKey = claveFormulario(key);
    applied[formKey] = asString;
    marked.push(formKey);
  }
  return { applied, marked };
}
