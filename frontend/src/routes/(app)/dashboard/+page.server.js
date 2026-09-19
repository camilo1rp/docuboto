// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dashboard/+page.server.js — Carga SSR del panel principal (T-07, RF-UI-06).
 *
 * REDISEÑO (Sistema de Diseño §10): el tablero deja de ser un panel de
 * métricas agregadas y pasa a mostrar los cuatro módulos personales fijos:
 *   01 Radicados pendientes  — GET /api/v1/documents?status=distributed|in_progress
 *   02 Actividad reciente    — GET /api/v1/documents (sin filtro, más recientes)
 *   03 Alertas               — GET /api/v1/documents ordenado por `due_date`
 *                              ascendente (RF-RAD-04), consulta PROPIA
 *   04 Favoritos             — resuelto en el cliente (favorites.js + navItems.js),
 *                              no requiere carga server-side.
 *
 * Las métricas agregadas del tenant (E09, GET /reports/radicados) se conservan
 * pero bajan a una sección secundaria colapsable (progressive disclosure).
 *
 * NOTA DE ALCANCE — módulo 03 "Alertas":
 * Cubre el vencimiento del PLAZO DE RESPUESTA de un radicado (RF-RAD-04), no
 * todos los vencimientos del sistema: los de firmas y transferencias tienen sus
 * propias puertas. No se agregan aquí a propósito — el gateway es un proxy por
 * prefijo, sin lógica de negocio, y un agregador cross-service tendría que
 * repartir entre dos fuentes el gate de clearance y el recuento.
 *
 * Lo que sí cambió, y era un defecto: las alertas se derivaban de la MISMA
 * ventana que los pendientes —los 20 más recientes por estado—, así que el
 * radicado que llevaba más tiempo vencido, justo el que importa, era invisible
 * por construcción: se radicó antes, luego salía después. Ahora son una consulta
 * propia ordenada por plazo, que es lo que el backend no sabía hacer hasta
 * ahora.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

// Estados que representan un radicado "abierto" (no cerrado) — requieren acción.
const OPEN_STATUSES = ["distributed", "in_progress"];

const WIDGET_LIMIT = 5; // ítems visibles por módulo en el tablero

/**
 * @param {typeof fetch} fetchFn
 * @param {string} token
 * @param {string} tenant
 * @param {URLSearchParams} params
 */
async function fetchDocuments(fetchFn, token, tenant, params) {
  const res = await fetchFn(`${API_V1}/documents?${params.toString()}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(mensajeError(body, `Error ${res.status}`));
  }

  const total = Number(res.headers.get("X-Total-Count") ?? 0);
  const items = await res.json();
  return { items: Array.isArray(items) ? items : [], total };
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  // Heredar user del layout — en particular tenant_slug y verificación de sesión.
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  // Espejo del RBAC (ADR-013/020): el desplegable "Métricas del tenant" solo se
  // ofrece si el usuario tiene el permiso que ya gatea la entrada de menú
  // equivalente ("Reportes", navItems.js) — la UI no debe pedir algo que el
  // gateway rechazaría con 403.
  const canSeeSummary = (user?.permissions ?? []).includes("SGD_PERM_ESTADISTICA");

  // ---------------------------------------------------------------------
  // Módulo 01: radicados pendientes. UNA sola llamada con `status` repetido.
  //
  // Antes era una por estado, porque el backend no aceptaba varios. Sumar dos
  // ventanas de 20 no da las 40 más recientes —da 20 de cada una— y el total
  // salía de sumar dos `X-Total-Count`, que solo es correcto mientras los
  // estados sean disjuntos. Ahora el total lo cuenta la base.
  // ---------------------------------------------------------------------
  const pendientesParams = new URLSearchParams({ page: "1", size: String(WIDGET_LIMIT) });
  for (const estado of OPEN_STATUSES) pendientesParams.append("status", estado);
  const pendientesFetch = fetchDocuments(fetch, token, tenant, pendientesParams);

  // ---------------------------------------------------------------------
  // Módulo 03: alertas. Consulta PROPIA ordenada por plazo ascendente.
  //
  // No se derivan de los pendientes: esa ventana trae los más RECIENTES, y lo
  // que una alerta busca es lo contrario — lo que lleva más tiempo esperando.
  // `order_dir=asc` pone delante las fechas pasadas, es decir, lo ya vencido.
  // ---------------------------------------------------------------------
  const alertasParams = new URLSearchParams({
    page: "1",
    size: String(WIDGET_LIMIT),
    order_by: "due_date",
    order_dir: "asc",
  });
  for (const estado of OPEN_STATUSES) alertasParams.append("status", estado);
  const alertasFetch = fetchDocuments(fetch, token, tenant, alertasParams);

  // ---------------------------------------------------------------------
  // Módulo 02: actividad reciente (sin filtro de estado — continuidad del
  // trabajo, incluye radicados ya cerrados si son lo último tocado).
  // ---------------------------------------------------------------------
  const recentParams = new URLSearchParams({ page: "1", size: String(WIDGET_LIMIT) });
  const recentFetch = fetchDocuments(fetch, token, tenant, recentParams);

  // ---------------------------------------------------------------------
  // Sección secundaria colapsable: métricas agregadas (E09)
  // ---------------------------------------------------------------------
  const summaryFetch = (async () => {
    if (!canSeeSummary) return null;
    const res = await fetch(`${API_V1}/reports/radicados`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(mensajeError(body, `Error ${res.status}`));
    }
    return res.json();
  })();

  const [pendientesResult, alertasResult, recentResult, summaryResult] = await Promise.all([
    Promise.allSettled([pendientesFetch]),
    Promise.allSettled([alertasFetch]),
    Promise.allSettled([recentFetch]),
    Promise.allSettled([summaryFetch]),
  ]);

  // ---- Radicados pendientes (módulo 01) --------------------------------
  const pendientesSettled = pendientesResult[0];
  const pendientesOk = pendientesSettled.status === "fulfilled";
  const openError = pendientesOk
    ? null
    : (pendientesSettled.reason?.message ?? "No se pudo conectar al servidor de documentos.");

  const pendientesItems = pendientesOk ? pendientesSettled.value.items : [];
  const pendientesTotal = pendientesOk ? pendientesSettled.value.total : 0;

  // ---- Alertas (módulo 03) ----------------------------------------------
  // El orden lo da el servidor; aquí solo se traducen los días que faltan. Un
  // radicado sin `due_date` no llega —el `NULLS LAST` del backend los manda al
  // final y nunca entran en esta ventana—, pero el filtro se conserva porque
  // esta vista no debe depender de esa garantía para no mostrar «NaN días».
  const alertasSettled = alertasResult[0];
  const now = Date.now();
  const alertasItems = (alertasSettled.status === "fulfilled" ? alertasSettled.value.items : [])
    .filter((doc) => !!doc.due_date)
    .map((doc) => {
      const dueMs = new Date(doc.due_date).getTime();
      const diasRestantes = Number.isFinite(dueMs)
        ? Math.ceil((dueMs - now) / (1000 * 60 * 60 * 24))
        : null;
      return { ...doc, diasRestantes };
    })
    .filter((doc) => doc.diasRestantes !== null);

  const alertasError =
    alertasSettled.status === "rejected"
      ? (alertasSettled.reason?.message ?? "No se pudo conectar al servidor de documentos.")
      : null;

  // ---- Actividad reciente (módulo 02) -----------------------------------
  const recentSettled = recentResult[0];
  const recientesItems = recentSettled.status === "fulfilled" ? recentSettled.value.items : [];
  const recientesError =
    recentSettled.status === "rejected"
      ? (recentSettled.reason?.message ?? "No se pudo conectar al servidor de documentos.")
      : null;

  // ---- Métricas agregadas (sección secundaria) --------------------------
  const summarySettled = summaryResult[0];
  const summary = summarySettled.status === "fulfilled" ? summarySettled.value : null;
  const summaryError =
    summarySettled.status === "rejected"
      ? (summarySettled.reason?.message ?? "No se pudo conectar al servidor de reportes.")
      : null;

  return {
    pendientes: { items: pendientesItems, total: pendientesTotal, error: openError },
    alertas: { items: alertasItems, error: alertasError },
    recientes: { items: recientesItems, error: recientesError },
    summary,
    summaryError,
  };
}
