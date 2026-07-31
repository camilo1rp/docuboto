// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * workflowRuleConditions.js — Catálogo de campos/operadores del motor de
 * reglas de enrutamiento (E05, `workflow-service/app/schemas/rules.py`
 * `_FIELDS`/`_OPS` + `app/services/rules_engine.py`) y coerción de sus
 * condiciones crudas del formulario al tipo que espera el backend.
 *
 * Fuente única compartida entre `/admin/flujos/reglas/+page.svelte` (render
 * de las filas de condición) y sus proxies `api/+server.js`/`api/[id]/+server.js`
 * (allowlist server-side: NUNCA se reenvía la fila del cliente tal cual).
 *
 * El modelo real de una condición es `{field, op, value}` — `field` y `op`
 * son enums cerrados (Literal en el backend); `value` es `Any` en el schema,
 * pero `rules_engine.py` solo sabe interpretarlo como: lista (op "in", admite
 * también un string separado por comas), número (gt/lt/gte/lte vía
 * `_to_float`), o string (resto). Esta función replica esa coerción para que
 * el admin no tenga que adivinar el tipo.
 */

/** @type {Array<{value: string, label: string}>} */
export const RULE_FIELD_OPTIONS = [
  { value: "doc_type", label: "Tipo de documento (E/S/I)" },
  { value: "doc_class", label: "Clase documental" },
  { value: "dest_dept", label: "Dependencia destino" },
  { value: "origin_dept", label: "Dependencia origen" },
  { value: "subject", label: "Asunto" },
  { value: "sender_name", label: "Nombre del remitente" },
  { value: "sender_entity", label: "Entidad remitente" },
  { value: "pages", label: "Número de páginas" },
];

/** @type {Array<{value: string, label: string}>} */
export const RULE_OP_OPTIONS = [
  { value: "eq", label: "Es igual a" },
  { value: "ne", label: "Es distinto de" },
  { value: "contains", label: "Contiene" },
  { value: "not_contains", label: "No contiene" },
  { value: "in", label: "Está entre (lista separada por comas)" },
  { value: "gt", label: "Mayor que" },
  { value: "lt", label: "Menor que" },
  { value: "gte", label: "Mayor o igual que" },
  { value: "lte", label: "Menor o igual que" },
  { value: "regex", label: "Coincide con expresión regular" },
];

// Exportados para que `/admin/flujos/reglas/+page.svelte` pueda validar cada
// fila ANTES de enviarla (ver `buildConditionsPayload` más abajo: por diseño
// descarta en silencio lo que no reconoce, así que el llamador debe rechazar
// primero lo inválido en vez de dejar que se filtre sin aviso).
export const FIELD_VALUES = new Set(RULE_FIELD_OPTIONS.map((o) => o.value));
export const OP_VALUES = new Set(RULE_OP_OPTIONS.map((o) => o.value));
const NUMERIC_OPS = new Set(["gt", "lt", "gte", "lte"]);

/**
 * Coerciona el `value` crudo (siempre string en el formulario) al tipo que
 * `rules_engine.py` espera según el operador.
 * @param {string} op
 * @param {string} rawValue
 * @returns {string | number | string[]}
 */
export function coerceConditionValue(op, rawValue) {
  const trimmed = typeof rawValue === "string" ? rawValue.trim() : "";
  if (op === "in") {
    return trimmed
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  }
  if (NUMERIC_OPS.has(op)) {
    const n = Number(trimmed);
    return Number.isNaN(n) ? trimmed : n;
  }
  return trimmed;
}

/**
 * Allowlist + coerción de una lista de condiciones crudas del formulario (o
 * del body de un cliente potencialmente malicioso) a la forma exacta que
 * espera `WorkflowRuleCreate.conditions` — SOLO `{field, op, value}`, y solo
 * si `field`/`op` pertenecen a los enums cerrados del backend. Filas
 * incompletas o con field/op fuera de catálogo se descartan en silencio.
 *
 * IMPORTANTE: esta función es la última barrera server-side (anti
 * mass-assignment), no el lugar donde avisar al usuario — para cuando la
 * descarta ya es tarde para pedirle que corrija. `saveRule` en
 * `/admin/flujos/reglas/+page.svelte` valida cada fila ANTES de llegar aquí
 * y bloquea el envío si alguna tiene `field`/`op` fuera de catálogo o
 * `value` vacío, precisamente para que este filtro silencioso nunca se
 * ejerza sobre una fila que el administrador creía haber guardado.
 * @param {Array<{field?: string, op?: string, value?: string}>} rawConditions
 * @returns {Array<{field: string, op: string, value: string|number|string[]}>}
 */
export function buildConditionsPayload(rawConditions) {
  if (!Array.isArray(rawConditions)) return [];
  return rawConditions
    .filter(
      (c) =>
        c &&
        typeof c.field === "string" &&
        FIELD_VALUES.has(c.field) &&
        typeof c.op === "string" &&
        OP_VALUES.has(c.op) &&
        typeof c.value === "string" &&
        c.value.trim() !== "",
    )
    .map((c) => ({
      field: c.field,
      op: c.op,
      value: coerceConditionValue(c.op, c.value),
    }));
}
