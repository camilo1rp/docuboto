// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tenencia.js — ¿Tengo YO este trámite? (ADR-028)
 *
 * El estado del radicado lo mueve el flujo, y los actos que lo mueven desde la
 * bandeja (marcar en trámite, finalizar) solo los admite el POSEEDOR del paso
 * activo. Poseedor = `assigned_to` es el usuario, o el paso es de una
 * dependencia SIN dueño (`assigned_to` nulo) a la que el usuario pertenece.
 *
 * Es MÁS ESTRECHO que ver el radicado en la bandeja: un miembro de la
 * dependencia VE lo asignado a un colega, pero no lo posee. Espejo de
 * `_holder_condition` (workflow-service); el backend re-valida y responde
 * `404 no_active_step` a un no-poseedor, sin distinguirlo de «no hay paso».
 *
 * La correlación con la dependencia es por NOMBRE (`flow_steps.to_dept` es
 * texto libre), igual que en el backend.
 */

const ACTIVOS = ["pending", "in_progress"];

/**
 * Nombres de las dependencias activas a las que pertenece el usuario.
 * @param {{context?: {available_depts?: Array<{depe_id:number,is_active?:boolean}>}|null,
 *          dependencias?: Array<{codigo:number,nombre:string}>}|null|undefined} user
 * @returns {string[]}
 */
export function nombresDeDependenciasDelUsuario(user) {
  const catalogo = user?.dependencias ?? [];
  return (user?.context?.available_depts ?? [])
    .filter((d) => d.is_active !== false)
    .map((d) => catalogo.find((c) => c.codigo === d.depe_id)?.nombre)
    .filter((n) => typeof n === "string" && n !== "");
}

/**
 * @param {import('$lib/types/api.js').FlowStepResponse|null|undefined} step
 * @param {string|null|undefined} userId  `auth_users.id` (store `userId`), NO el `sub`.
 * @param {string[]} dependencias  ver nombresDeDependenciasDelUsuario
 */
export function esPoseedor(step, userId, dependencias) {
  if (!step || !userId || !ACTIVOS.includes(step.status)) return false;
  if (step.assigned_to) return step.assigned_to === userId;
  return (dependencias ?? []).includes(step.to_dept);
}

/** «Marcar en trámite» solo tiene sentido sobre un paso aún sin leer. */
export function puedeMarcarEnTramite(step, userId, dependencias) {
  return step?.status === "pending" && esPoseedor(step, userId, dependencias);
}

/** «Finalizar trámite» cierra el paso activo (pendiente o en trámite). */
export function puedeFinalizar(step, userId, dependencias) {
  return esPoseedor(step, userId, dependencias);
}

/** «Devolver» depende de la tenencia del paso, no del estado del radicado. */
export function puedeDevolver(step, userId, dependencias) {
  return esPoseedor(step, userId, dependencias);
}

/**
 * «Revertir» (rollback) exige además que el paso siga `pending` (R19): con la
 * misma tenencia que «Devolver», incluido el paso sin dueño de mi dependencia.
 */
export function puedeRevertir(step, userId, dependencias) {
  return step?.status === "pending" && esPoseedor(step, userId, dependencias);
}

/**
 * «Asignar a flujo» sin paso activo (ADR-028 R16). Fail-closed: el backend
 * responde 404 `no_active_step` en cualquier otro caso.
 *  - con historial: quien poseía el último paso `completed` (radicado no terminal);
 *  - sin ningún paso: quien radicó (`registered_by` = `auth_users.id`, store
 *    `userId`, NO el `sub`). `registered_by` nulo (importado/ingesta): nadie.
 * @param {{step?: object|null, historial?: object[]|null, radicadoStatus?: string|null,
 *          registeredBy?: string|null, userId?: string|null,
 *          dependencias?: string[]}} a
 */
export function puedeAsignarAFlujo({
  step,
  historial,
  radicadoStatus,
  registeredBy,
  userId,
  dependencias,
}) {
  if (step || !userId || !radicadoStatus || TERMINALES.includes(radicadoStatus)) return false;
  if (!Array.isArray(historial)) return false;
  if (historial.length > 0)
    return poseiaUltimoPaso(historial, radicadoStatus, userId, dependencias ?? []);
  return !!registeredBy && registeredBy === userId;
}

/** Nivel CRUD mínimo de PERM_TRAMITAR para «Finalizar» (`min_crud=3`, ADR-028 R3). */
export const FINALIZAR_NIVEL_CRUD = 3;

/** Estados terminales del radicado: ya no admiten actos de trámite. */
const TERMINALES = ["closed", "anulado"];

/**
 * «Finalizar trámite» con el espejo COMPLETO del RBAC: PERM_TRAMITAR a nivel
 * ≥3 (nunca solo presencia: con nivel 2 el backend da 403) Y tenencia.
 * Tenencia = poseer el paso activo, o —sin paso activo— haber poseído el
 * último paso `completed` de un radicado no terminal (R12).
 * @param {{nivel?: number|null, step?: object|null, historial?: object[]|null,
 *          radicadoStatus?: string|null, userId?: string|null,
 *          dependencias?: string[]}} a
 */
export function puedeFinalizarTramite({
  nivel,
  step,
  historial,
  radicadoStatus,
  userId,
  dependencias,
}) {
  if ((nivel ?? 0) < FINALIZAR_NIVEL_CRUD) return false;
  if (step) return puedeFinalizar(step, userId, dependencias ?? []);
  return poseiaUltimoPaso(historial, radicadoStatus, userId, dependencias ?? []);
}

/**
 * ¿Poseía yo el último paso (ya `completed`) de un radicado no terminal?
 * Solo se evalúa SIN paso activo (el llamante ya lo comprobó). Espejo de
 * `ultimo_paso_si_lo_poseia`; `historial` = `steps[]` de `GET /history`.
 * @param {Array<{step_number:number,status:string,assigned_to?:string|null,to_dept?:string}>|null|undefined} historial
 * @param {string|null|undefined} radicadoStatus
 * @param {string|null|undefined} userId
 * @param {string[]} dependencias
 */
export function poseiaUltimoPaso(historial, radicadoStatus, userId, dependencias) {
  if (!userId || !Array.isArray(historial) || historial.length === 0) return false;
  if (!radicadoStatus || TERMINALES.includes(radicadoStatus)) return false;
  const ultimo = historial.reduce((a, b) => (b.step_number > a.step_number ? b : a));
  if (ultimo.status !== "completed") return false;
  if (ultimo.assigned_to) return ultimo.assigned_to === userId;
  return (dependencias ?? []).includes(ultimo.to_dept ?? "");
}

/** Motivos de cierre (`motivo_cierre`, ADR-028 R11), en el orden del selector. */
export const MOTIVOS_CIERRE = [
  { value: "respondido", label: "Respondido" },
  { value: "nrr", label: "No requiere respuesta (NRR)" },
  { value: "traslado_competencia", label: "Traslado por competencia" },
  { value: "otro", label: "Otro" },
];

/** @param {unknown} v */
export function esMotivoCierre(v) {
  return MOTIVOS_CIERRE.some((m) => m.value === v);
}

export const FINALIZAR_COMENTARIO_MIN = 5;
export const FINALIZAR_COMENTARIO_MAX = 500;
