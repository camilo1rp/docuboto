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
 *   03 Alertas               — derivado del campo `due_date` (RF-RAD-04) de los
 *                              radicados pendientes (ver nota de alcance abajo)
 *   04 Favoritos             — resuelto en el cliente (favorites.js + navItems.js),
 *                              no requiere carga server-side.
 *
 * Las métricas agregadas del tenant (E09, GET /reports/radicados) se conservan
 * pero bajan a una sección secundaria colapsable (progressive disclosure).
 *
 * NOTA DE ALCANCE — módulo 03 "Alertas":
 * No existe hoy un endpoint dedicado de alertas/vencimientos transversal
 * (firmas + transferencias + respuesta de radicados). Se usa el dato más
 * cercano que SÍ es real: `due_date` de los radicados abiertos del tenant
 * (fecha límite de respuesta, RF-RAD-04, ya expuesta por /documents). Cuando
 * exista un endpoint de alertas dedicado (agregando también vencimientos de
 * `/signature/pending` y `/transferencias`), sustituir este cálculo por esa
 * llamada. Ver seguimiento en agent-memory (sveltekit-developer).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

// Estados que representan un radicado "abierto" (no cerrado) — requieren acción.
const OPEN_STATUSES = ["distributed", "in_progress"];

const PENDIENTES_SCAN_SIZE = 20; // por-estado; suficiente para priorizar sin paginar
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
    throw new Error(body.detail ?? `Error ${res.status}`);
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
  // Módulo 01 + 03: radicados abiertos (una llamada por estado soportado
  // por el backend — /documents no acepta status[] múltiple, RF-UI-03).
  // ---------------------------------------------------------------------
  const openStatusFetches = OPEN_STATUSES.map((status) => {
    const params = new URLSearchParams({
      status,
      page: "1",
      size: String(PENDIENTES_SCAN_SIZE),
    });
    return fetchDocuments(fetch, token, tenant, params);
  });

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
      throw new Error(body.detail ?? `Error ${res.status}`);
    }
    return res.json();
  })();

  const [openResults, recentResult, summaryResult] = await Promise.all([
    Promise.allSettled(openStatusFetches),
    Promise.allSettled([recentFetch]),
    Promise.allSettled([summaryFetch]),
  ]);

  // ---- Radicados pendientes (módulo 01) --------------------------------
  const fulfilledOpen = openResults.filter(
    /** @returns {r is PromiseFulfilledResult<{items: any[], total: number}>} */
    (r) => r.status === "fulfilled",
  );
  const firstRejectedOpen = openResults.find((r) => r.status === "rejected");
  const openError =
    fulfilledOpen.length === 0 && firstRejectedOpen
      ? (firstRejectedOpen.reason?.message ?? "No se pudo conectar al servidor de documentos.")
      : null;

  const openDocuments = fulfilledOpen.flatMap((r) => r.value.items);
  const pendientesTotal = fulfilledOpen.reduce((acc, r) => acc + r.value.total, 0);

  const pendientesItems = [...openDocuments]
    .sort((a, b) => (b.registered_at ?? "").localeCompare(a.registered_at ?? ""))
    .slice(0, WIDGET_LIMIT);

  // ---- Alertas (módulo 03) — derivadas de due_date de radicados abiertos ----
  const now = Date.now();
  const alertasItems = openDocuments
    .filter((doc) => !!doc.due_date)
    .map((doc) => {
      const dueMs = new Date(doc.due_date).getTime();
      const diasRestantes = Number.isFinite(dueMs)
        ? Math.ceil((dueMs - now) / (1000 * 60 * 60 * 24))
        : null;
      return { ...doc, diasRestantes };
    })
    .filter((doc) => doc.diasRestantes !== null)
    .sort((a, b) => a.diasRestantes - b.diasRestantes)
    .slice(0, WIDGET_LIMIT);

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
    alertas: { items: alertasItems, error: openError },
    recientes: { items: recientesItems, error: recientesError },
    summary,
    summaryError,
  };
}
