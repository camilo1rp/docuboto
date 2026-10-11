// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { formatearFecha } from "./fecha.js";

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
 *   array de escalares        -> lista repetible (un control por elemento)
 *   object de UN nivel        -> grupo con un control por propiedad
 *
 * Los compuestos viajan como UN string JSON de valores CRUDOS (strings): así
 * encajan en el mismo canal que los escalares (FormData `meta_<clave>` en
 * expedientes, `extraMetadata[clave]` en /radicar) y `coerceSchemaValue` los
 * tipa de una vez, elemento a elemento, contra el schema.
 *
 * NO soportado (resolveSchemaControl devuelve null — fail-safe, el campo no
 * se renderiza ni se envía): array de objetos/arrays, object anidado dentro de
 * un object, un object con una propiedad `required` no soportada,
 * oneOf/anyOf/allOf, $ref, o un `type` que no se reduce a lo anterior.
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
 * @typedef {{kind: 'text'|'email'|'date'|'number'|'select', options?: Array<{value: string, label: string}>}} ScalarControl
 * @typedef {{kind: 'array', item: ScalarControl}} ArrayControl
 * @typedef {{kind: 'object', properties: Array<[string, any]>, required: string[]}} ObjectControl
 */

/**
 * Deriva el control de UI para una propiedad del `json_schema` de la
 * plantilla de metadatos de expediente.
 * @param {any} schema - la propiedad, es decir `json_schema.properties[key]`
 * @param {number} [depth] - niveles de object ya recorridos (0 = raíz). Un
 *   object solo se admite en depth 0: «de un nivel».
 * @returns {ScalarControl|ArrayControl|ObjectControl|null}
 *   `null` si el constructo no es soportado por este formulario (fail-safe).
 */
export function resolveSchemaControl(schema, depth = 0) {
  if (!schema || typeof schema !== "object") return null;
  // Composición y referencias: no soportadas.
  if (schema.$ref || schema.oneOf || schema.anyOf || schema.allOf) return null;

  const type = normalizeType(schema);

  if (type === "array") {
    const items = schema.items;
    if (!items || typeof items !== "object" || Array.isArray(items)) return null;
    const item = resolveSchemaControl(items, depth + 1);
    // Solo lista de ESCALARES: una lista de objetos o de listas no cabe en un
    // control repetible sin construir un editor de tablas.
    if (!item || item.kind === "array" || item.kind === "object") return null;
    return { kind: "array", item };
  }

  if (type === "object") {
    if (depth >= 1) return null;
    const props = schema.properties;
    if (!props || typeof props !== "object") return null;
    const required = Array.isArray(schema.required) ? schema.required : [];
    /** @type {Array<[string, any]>} */
    const soportadas = [];
    for (const [k, s] of Object.entries(props)) {
      if (resolveSchemaControl(s, depth + 1)) soportadas.push([k, s]);
      // Una propiedad obligatoria que no se puede pintar haría imposible
      // crear el registro: mejor declarar todo el object no soportado (y que
      // el aviso del formulario lo cuente) que ofrecer un grupo que siempre
      // acaba en 422.
      else if (required.includes(k)) return null;
    }
    if (soportadas.length === 0) return null;
    return { kind: "object", properties: soportadas, required };
  }

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

  if (control.kind === "array") return coerceList(schema, raw);
  if (control.kind === "object") return coerceGroup(schema, control, raw);

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

/**
 * Un compuesto llega como string JSON (o ya estructurado, desde un llamador
 * que lo pase así). Un JSON ilegible se OMITE en vez de enviarse: el backend
 * exige los `required` y nombra la propiedad que falta.
 * @param {unknown} raw
 */
function parseCompound(raw) {
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/**
 * Lista de escalares: coerciona cada elemento contra `items`, descarta los
 * vacíos/inválidos y omite la lista entera si no queda ninguno.
 * @param {any} schema
 * @param {unknown} raw
 * @returns {unknown[]|undefined}
 */
function coerceList(schema, raw) {
  const lista = parseCompound(raw);
  if (!Array.isArray(lista)) return undefined;
  const salida = lista
    .map((v) => coerceSchemaValue(schema.items, v === null || v === undefined ? "" : String(v)))
    .filter((v) => v !== undefined);
  return salida.length ? salida : undefined;
}

/**
 * Object de un nivel: solo las propiedades que el schema declara y el
 * formulario soporta (allowlist), cada una coercionada a su tipo.
 * @param {any} schema
 * @param {ObjectControl} control
 * @param {unknown} raw
 * @returns {Record<string, unknown>|undefined}
 */
function coerceGroup(schema, control, raw) {
  const grupo = parseCompound(raw);
  if (!grupo || typeof grupo !== "object" || Array.isArray(grupo)) return undefined;
  /** @type {Record<string, unknown>} */
  const salida = {};
  for (const [k, propSchema] of control.properties) {
    const v = /** @type {Record<string, unknown>} */ (grupo)[k];
    if (v === undefined || v === null) continue;
    // Un array anidado ya viene como lista; un escalar, como string.
    const coerced = coerceSchemaValue(
      propSchema,
      Array.isArray(v) || typeof v === "object" ? JSON.stringify(v) : String(v),
    );
    if (coerced !== undefined) salida[k] = coerced;
  }
  return Object.keys(salida).length ? salida : undefined;
}

/**
 * Ubica el error de un 422 (`field` = `a.b`) en el control que lo causó.
 *
 * @param {string|null|undefined} field - ruta con puntos del backend
 * @param {string} mensaje
 * @param {string[]} claves - propiedades de primer nivel que el formulario pinta
 * @returns {{key: string, error: string, subErrors: Record<string, string>}|null}
 *   `null` si no hay ruta o la propiedad no está en pantalla: el llamador deja
 *   entonces el mensaje en el aviso general, que es lo honesto.
 */
export function ubicarErrorDeCampo(field, mensaje, claves) {
  if (!field) return null;
  const [key, ...resto] = field.split(".");
  if (!claves.includes(key)) return null;
  // `lista.1` apunta a un elemento: el control es la lista entera.
  const interior = resto[0] !== undefined && !/^\d+$/.test(resto[0]) ? resto[0] : null;
  return { key, error: mensaje, subErrors: interior ? { [interior]: mensaje } : {} };
}

// ---------------------------------------------------------------------------
// Presentación de SOLO LECTURA (AREA-12-05, B6-25): el mismo schema que dibuja
// los controles decide cómo se lee el valor ya guardado.
// ---------------------------------------------------------------------------

/**
 * @typedef {{kind: 'text', text: string}
 *   | {kind: 'code', text: string}
 *   | {kind: 'list', items: ValorPresentado[]}
 *   | {kind: 'group', entries: Array<{key: string, label: string, valor: ValorPresentado}>}} ValorPresentado
 */

const PROFUNDIDAD_MAX = 4;

/** @param {unknown} v */
const esObjetoPlano = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/**
 * Etiqueta de una opción de `enum` (`enumNames` paralelo) o de `oneOf` con
 * `const` + `title`; si el schema no la declara, el valor tal cual.
 * @param {any} schema
 * @param {unknown} valor
 * @returns {string|null}
 */
function etiquetaDeOpcion(schema, valor) {
  if (Array.isArray(schema?.enum) && Array.isArray(schema?.enumNames)) {
    const i = schema.enum.indexOf(valor);
    if (i >= 0 && typeof schema.enumNames[i] === "string") return schema.enumNames[i];
  }
  if (Array.isArray(schema?.oneOf)) {
    const o = schema.oneOf.find((x) => x && x.const === valor);
    if (o && typeof o.title === "string") return o.title;
  }
  return null;
}

/**
 * Descripción presentable de un valor de metadatos, guiada por su schema.
 *
 * Nunca devuelve `[object Object]`: un object se convierte en grupo (con los
 * `title` interiores), una lista en lista, y lo que no tiene lectura natural
 * (profundidad excesiva) en JSON compacto. Los valores NO se interpretan como
 * HTML: el llamador los pinta como texto.
 *
 * @param {any} schema - la propiedad del schema (puede ser `undefined`)
 * @param {unknown} valor
 * @param {number} [profundidad]
 * @returns {ValorPresentado}
 */
export function formatearValorSchema(schema, valor, profundidad = 0) {
  if (valor === null || valor === undefined || valor === "") return { kind: "text", text: "—" };
  if (typeof valor === "boolean") return { kind: "text", text: valor ? "Sí" : "No" };

  if (Array.isArray(valor)) {
    if (profundidad >= PROFUNDIDAD_MAX) return { kind: "code", text: JSON.stringify(valor) };
    return {
      kind: "list",
      items: valor.map((x) => formatearValorSchema(schema?.items, x, profundidad + 1)),
    };
  }

  if (esObjetoPlano(valor)) {
    if (profundidad >= PROFUNDIDAD_MAX) return { kind: "code", text: JSON.stringify(valor) };
    const props = esObjetoPlano(schema?.properties) ? schema.properties : {};
    return {
      kind: "group",
      entries: Object.entries(valor).map(([k, v]) => ({
        key: k,
        label: typeof props[k]?.title === "string" && props[k].title ? props[k].title : k,
        valor: formatearValorSchema(props[k], v, profundidad + 1),
      })),
    };
  }

  const opcion = etiquetaDeOpcion(schema, valor);
  if (opcion !== null) return { kind: "text", text: opcion };
  if (schema?.format === "date" && typeof valor === "string") {
    return { kind: "text", text: formatearFecha(valor) };
  }
  return { kind: "text", text: String(valor) };
}

/**
 * Claves que el SISTEMA escribe en `metadata` (el backend las rechaza si las
 * manda el cliente). No son campos de la plantilla: se muestran aparte, con una
 * etiqueta legible, en el grupo «Datos del sistema». Mapa cerrado y único: si el
 * backend reserva otra clave, se añade aquí y en ningún otro sitio.
 * @type {Record<string, {label: string, code?: boolean}>}
 */
export const METADATA_RESERVADA = {
  from_borrador: { label: "Borrador de origen", code: true },
  canal: { label: "Canal de ingreso" },
  consentimiento: { label: "Consentimiento" },
};

/** @param {string} k */
export const esClaveReservada = (k) => Object.prototype.hasOwnProperty.call(METADATA_RESERVADA, k);

/**
 * Reparte los metadatos en tres grupos: los que la plantilla declara, los que
 * escribe el sistema (claves reservadas) y los que no se reconocen.
 *
 * Los no reconocidos NUNCA se ocultan: la plantilla vigente puede no ser la de la
 * captura (cambió de versión) y ni el expediente ni el radicado guardan cuál se
 * usó. Ocultarlos sería perder datos a la vista.
 *
 * @param {any} schema - `json_schema` completo (con `properties`), o null
 * @param {Record<string, unknown>|null|undefined} metadata
 * @returns {{declarados: Array<{key: string, label: string, valor: ValorPresentado}>,
 *   sistema: Array<{key: string, label: string, valor: ValorPresentado}>,
 *   otros: Array<{key: string, label: string, valor: ValorPresentado}>}}
 */
export function repartirMetadatos(schema, metadata) {
  const props = esObjetoPlano(schema?.properties) ? schema.properties : {};
  const declarados = [];
  const sistema = [];
  const otros = [];
  for (const [k, v] of Object.entries(esObjetoPlano(metadata) ? metadata : {})) {
    if (esClaveReservada(k)) {
      const def = METADATA_RESERVADA[k];
      const valor =
        def.code && v !== null && v !== undefined && v !== "" && typeof v !== "object"
          ? /** @type {ValorPresentado} */ ({ kind: "code", text: String(v) })
          : formatearValorSchema(undefined, v);
      sistema.push({ key: k, label: def.label, valor });
    } else if (Object.prototype.hasOwnProperty.call(props, k)) {
      const titulo = props[k]?.title;
      declarados.push({
        key: k,
        label: typeof titulo === "string" && titulo ? titulo : k,
        valor: formatearValorSchema(props[k], v),
      });
    } else {
      otros.push({ key: k, label: k, valor: formatearValorSchema(undefined, v) });
    }
  }
  return { declarados, sistema, otros };
}
