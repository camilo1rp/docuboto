// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * schemaField.js — Mapeo de una propiedad de JSON Schema (Draft 2020-12) a un
 * control de UI, y coerción de su valor crudo (string de FormData) al tipo
 * declarado.
 *
 * Compartido entre `SchemaField.svelte` (render del campo dinámico en el
 * cliente) y `expedientes/+page.server.js#actions.create` (construcción del
 * `metadata` server-authoritative, allowlist campo a campo desde el schema —
 * T-12 Form Builder de creación de expediente). Una sola fuente de verdad
 * para la tabla de mapeo evita que ambos lados diverjan.
 *
 * Ver contrato orfeo-architect §2 para la tabla completa. Resumen:
 *   string (sin enum/format)  -> text
 *   string + enum             -> select
 *   string + format:date      -> date
 *   string + format:email     -> email
 *   string + format:date-time -> text (FormField no tiene datetime-local;
 *                                       no se pierde la hora, solo el widget)
 *   number / integer          -> number
 *   boolean                   -> select Sí/No (FormField no tiene checkbox)
 *
 * NO soportado (resolveSchemaControl devuelve null — fail-safe, el campo no
 * se renderiza ni se envía): object, array, oneOf/anyOf/allOf, $ref, o un
 * `type` que no se reduce a uno de los escalares de arriba.
 */

const NULLABLE = "null";

/**
 * `type` puede ser un string o un array (p. ej. `["string","null"]`, muy
 * común para campos opcionales). Se toma el primer tipo distinto de "null".
 * @param {any} schema
 * @returns {string|null}
 */
function normalizeType(schema) {
  let type = schema?.type;
  if (Array.isArray(type)) {
    type = type.find((t) => t !== NULLABLE) ?? null;
  }
  return type ?? null;
}

/**
 * Deriva el control de UI para una propiedad del `json_schema` de la
 * plantilla de metadatos de expediente.
 * @param {any} schema - la propiedad, es decir `json_schema.properties[key]`
 * @returns {{kind: 'text'|'email'|'date'|'number'|'select', options?: Array<{value: string, label: string}>}|null}
 *   `null` si el constructo no es soportado por este formulario (fail-safe).
 */
export function resolveSchemaControl(schema) {
  if (!schema || typeof schema !== "object") return null;
  // Constructos compuestos/no escalares: no soportados en este incremento.
  if (schema.$ref || schema.oneOf || schema.anyOf || schema.allOf) return null;

  const type = normalizeType(schema);
  if (type === "object" || type === "array") return null;

  // Enum (con o SIN `type` explícito): un conjunto cerrado de valores es un
  // <select>, independientemente de que el schema declare `type`. JSON Schema
  // admite `{enum:[...]}` sin `type` (patrón habitual) -- sin este caso el
  // campo desaparecería en silencio y, si era `required`, el backend
  // respondería 422 sin que el usuario tuviera dónde corregirlo. La coerción
  // sigue guiada por `type` (`coerceSchemaValue`), así que un enum numérico
  // conserva su tipo al enviarse.
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return {
      kind: "select",
      options: schema.enum.map((v) => ({ value: String(v), label: String(v) })),
    };
  }

  if (type === "string") {
    if (schema.format === "date") return { kind: "date" };
    if (schema.format === "email") return { kind: "email" };
    // Incluye format:date-time y string sin format — FormField no tiene un
    // control datetime-local dedicado; se muestra como texto (con hint en el
    // llamador) en vez de perder la parte de hora.
    return { kind: "text" };
  }

  if (type === "number" || type === "integer") return { kind: "number" };

  if (type === "boolean") {
    return {
      kind: "select",
      options: [
        { value: "true", label: "Sí" },
        { value: "false", label: "No" },
      ],
    };
  }

  // `type` ausente/desconocido (p. ej. solo `const`, o un tipo custom): no
  // soportado — mejor omitir el campo que adivinar mal.
  return null;
}

/**
 * Coerciona un valor crudo de FormData (siempre string) al tipo declarado
 * por la propiedad del schema.
 * @param {any} schema - la propiedad, es decir `json_schema.properties[key]`
 * @param {string} raw
 * @returns {unknown} el valor coercionado, o `undefined` si debe omitirse
 *   (vacío, constructo no soportado, o coerción inválida — p. ej. "abc" para
 *   `number`). `undefined` le dice al llamador que NO asigne la clave, para
 *   que el backend exija los `required` en vez de que el front envíe basura.
 */
export function coerceSchemaValue(schema, raw) {
  if (raw === "" || raw === undefined || raw === null) return undefined;

  const control = resolveSchemaControl(schema);
  if (!control) return undefined;

  const type = normalizeType(schema);

  if (type === "number" || type === "integer") {
    const n = Number(raw);
    return Number.isNaN(n) ? undefined : n;
  }

  if (type === "boolean") {
    if (raw === "true") return true;
    if (raw === "false") return false;
    return undefined;
  }

  // string / enum / date / email / date-time: tal cual (el <input date> ya
  // entrega ISO YYYY-MM-DD).
  return raw;
}
