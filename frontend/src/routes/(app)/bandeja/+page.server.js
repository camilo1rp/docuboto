// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja/+page.server.js — Carga SSR de la bandeja (T-08/T-Fase1, RF-UI-03/RF-FLU-03).
 *
 * Endpoint real: GET /api/v1/documents?doc_type=&status=&page=&size=
 *   - Filtros soportados por el backend: doc_type (E|S|I), status, page, size
 *   - Paginación vía X-Total-Count (header de respuesta)
 *   - Filtros de fecha y dependencia disponibles en /api/v1/search (T-11)
 *
 * La carga inicial es SSR (page=1, sin filtros). Los cambios de filtro, paginación
 * y acciones de operador (avanzar estado, ver detalle) usan el endpoint local
 * /bandeja/api[/id] que lee la cookie httpOnly en el servidor (D-05, ADR-011).
 *
 * `vista` (T-Fase1, tablero de distribución): conmutador resuelto EN SSR por
 * query param — `?vista=tablero` (default) | `lista` | `pendientes`. No hay
 * ruta `/workflows` separada: una sola bandeja con vistas (decisión de
 * arquitectura, ver el prompt de la tarea). `pendientes` solo es alcanzable
 * en el cliente si el usuario tiene USUA_PERM_ADMIN o PERM_TRAMITAR — la UI la
 * oculta, pero como con todo en este proyecto, el backend re-valida.
 *
 * `dependencias`: catálogo de dependencias activas (mismo patrón que
 * `radicar/+page.server.js`), usado por WorkflowBoard para poblar los
 * selects de destino de AsignarModal/TrasladarModal.
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { attachSafeBody } from "$lib/server/sanitizeHtml.js";
import { mensajeError } from "$lib/utils/apiError.js";

const VALID_VISTAS = new Set(["tablero", "lista", "pendientes"]);

// Nota: el token se lee aquí solo para la carga SSR inicial.
// Las llamadas cliente-side usan /bandeja/api[/id] que leen la cookie en el servidor.

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  const vistaParam = url.searchParams.get("vista") ?? "tablero";
  const vista = VALID_VISTAS.has(vistaParam) ? vistaParam : "tablero";

  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  // Leer filtros iniciales de query params (deep-link friendly, vista Lista)
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);

  let documents = [];
  let total = 0;
  let loadError = null;

  // La vista Tablero (default) no necesita el listado de /documents — solo la
  // vista Lista lo usa. Se carga siempre igual (barato, ya paginado a 20) para
  // no complicar el cambio de vista en cliente sin recargar.
  try {
    const res = await fetch(`${API_V1}/documents?${params.toString()}`, { headers });

    if (res.ok) {
      total = Number(res.headers.get("X-Total-Count") ?? 0);
      documents = await res.json();
      // T-12b — defensa durable en el punto de render: el cuerpo de Salida/Interno es
      // HTML (editor TipTap). Se sanea AQUÍ (server-side) sea cual sea su origen
      // (este BFF, asistente MCP, datos legacy) y se expone como `observations_html`
      // listo para {@html} en el drawer. Cierra la trampa de XSS señalada en la revisión.
      if (Array.isArray(documents)) documents.forEach(attachSafeBody);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de documentos.";
  }

  // Dependencias activas (mismo endpoint que radicar/+page.server.js) — solo
  // hace falta para el Tablero, pero es barato y evita un segundo viaje al
  // cambiar de vista en cliente.
  // Se pide el catálogo COMPLETO: las inactivas no se ofrecen en los selectores
  // pero sí hacen falta para pintar el nombre actual de un paso antiguo (ADR-032).
  let dependenciasTodas = [];
  try {
    const depRes = await fetch(`${API_V1}/dependencias`, { headers });
    if (depRes.ok) dependenciasTodas = await depRes.json().catch(() => []);
  } catch {
    dependenciasTodas = [];
  }
  if (!Array.isArray(dependenciasTodas)) dependenciasTodas = [];
  const dependencias = dependenciasTodas.filter((d) => d?.activo !== false);

  // Causales activas (catálogo del tenant, migración tenant 008). Devolver y
  // anular exigen una: se sirven aquí para que el modal ofrezca el catálogo real
  // en vez de pedir el código de memoria. Un fallo de carga NO rompe la bandeja
  // —degrada a lista vacía— y el modal lo dice en vez de fingir que no hay.
  let causales = [];
  try {
    const causRes = await fetch(`${API_V1}/catalogos/causales?activo=true`, { headers });
    if (causRes.ok) causales = await causRes.json().catch(() => []);
  } catch {
    causales = [];
  }

  // Catálogo CERRADO de causales de reserva (Ley 1712 arts. 18-19, ADR-039) con el
  // umbral de segunda persona y el plazo máximo. Es la ley, no un dato de la
  // entidad: el modal de reclasificar ofrece SOLO estas causales (el texto libre lo
  // rechaza el backend con 422). Un fallo degrada a `null` y el modal lo dice:
  // sin catálogo no se puede clasificar ni reservar, y no se finge lo contrario.
  /** @type {{nivel_doble_firma: number, plazo_max_meses: number, causales: any[]} | null} */
  let catalogoReserva = null;
  try {
    const resRes = await fetch(`${API_V1}/documents/causales-reserva`, { headers });
    if (resRes.ok) {
      const c = await resRes.json().catch(() => null);
      if (c && Array.isArray(c.causales)) catalogoReserva = c;
    }
  } catch {
    catalogoReserva = null;
  }

  return {
    documents,
    total,
    loadError,
    vista,
    dependencias,
    dependenciasTodas,
    causales,
    catalogoReserva,
    // Filtros activos al cargar (para deep-link)
    initialFilters: { docType, status, page },
    // token y tenant NO se exponen: las llamadas cliente usan /bandeja/api (D-05)
  };
}
