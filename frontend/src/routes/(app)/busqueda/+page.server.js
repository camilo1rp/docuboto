// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * busqueda/+page.server.js — Carga SSR de resultados de búsqueda avanzada (T-11,
 * RF-UI-03) y de las pestañas semántica/antecedentes (Fase 6, cierre desfase
 * API↔UI E21).
 *
 * Tres pestañas resueltas por `?tab=` (exacta|semantica|antecedentes), igual
 * patrón que el conmutador de vista de /bandeja (`vista=` en la URL): cada
 * pestaña es bookmarkable/enlazable por sí misma, y solo se llama al backend
 * de la pestaña activa.
 *
 * Endpoints reales:
 *   - Exacta:       GET  /api/v1/search — q (≥2 chars), doc_type, status,
 *     date_from, date_to, dest_dept_code, ubicacion_dept_code, page, size.
 *   - Conocimiento (`semantica`): POST /api/v1/knowledge/search — { query, top_k } →
 *     `list[SearchHit]` ({ id, source_type, source_ref, texto, metadata, score }).
 *   - Antecedentes:  POST /api/v1/knowledge/antecedentes — { query|radicado_ref,
 *     top_k } (XOR) → { antecedentes: [{source_type, source_ref, snippet, score}],
 *     advisory }.
 *
 * Las tres aplican clearance por `X-User-Id` (no read-up) — SIEMPRE inyectado
 * server-side, nunca desde el body del cliente.
 *
 * PATRÓN (coherente con bandeja):
 *   - Esta carga SSR maneja el caso inicial y deep-links (URL bookmarkable).
 *   - Las búsquedas interactivas usan goto() para actualizar la URL, lo que
 *     vuelve a ejecutar este load con los nuevos params (sin recarga completa del
 *     navegador — SvelteKit navigation).
 *   - La paginación de Exacta usa /busqueda/api (proxy local); semántica/
 *     antecedentes no paginan (top_k acotado), pero sí usan sus propios proxies
 *     (/busqueda/api/semantica, /busqueda/api/antecedentes, /busqueda/api/
 *     by-tracking/[trackingNumber]) para re-ejecutar la búsqueda desde el
 *     cliente sin una navegación SSR completa (misma razón que la paginación).
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { enteroDeLaUrl } from "$lib/utils/urlParams.js";
import { mensajeError } from "$lib/utils/apiError.js";
import { mensajeConocimiento, tabsBusquedaPermitidas } from "$lib/utils/asistenteAcceso.js";

// Las pestañas válidas se calculan por usuario (C-1, ADR-037): ver `tabsBusquedaPermitidas`.

/**
 * Carga SSR de la pestaña de conocimiento (`?tab=semantica`): POST /api/v1/knowledge/search.
 * @param {{token: string, tenant: string, userId: string, query: string, topK: number}} args
 */
async function loadSemantica({ token, tenant, userId, query, topK }) {
  if (query.length < 1) return { results: null, error: null };
  try {
    const res = await fetch(`${API_V1}/knowledge/search`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify({ query, top_k: topK }),
    });
    if (res.ok) {
      const data = await res.json();
      return { results: Array.isArray(data) ? data : [], error: null };
    }
    const body = await res.json().catch(() => ({}));
    return { results: null, error: mensajeConocimiento(body, `Error ${res.status}`) };
  } catch {
    return { results: null, error: "No se pudo conectar al servicio de conocimiento." };
  }
}

/**
 * Carga SSR de la pestaña Antecedentes: POST /api/v1/knowledge/antecedentes.
 * @param {{token: string, tenant: string, userId: string, query: string, radicadoRef: string, topK: number}} args
 */
async function loadAntecedentes({ token, tenant, userId, query, radicadoRef, topK }) {
  if (query.length < 1 && radicadoRef.length < 1) return { data: null, error: null };
  const payload =
    query.length > 0 ? { query, top_k: topK } : { radicado_ref: radicadoRef, top_k: topK };
  try {
    const res = await fetch(`${API_V1}/knowledge/antecedentes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      return { data: await res.json(), error: null };
    }
    const body = await res.json().catch(() => ({}));
    return { data: null, error: mensajeConocimiento(body, `Error ${res.status}`) };
  } catch {
    return { data: null, error: "No se pudo conectar al servicio de conocimiento." };
  }
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  // X-User-Id requerido por el endpoint de búsqueda para aplicar clearance (RF-SEG-08)
  const userId = user?.sub ?? "";

  const rawTab = url.searchParams.get("tab") ?? "exacta";
  // Pestaña no permitida (entidad sin asistente o sin PERM_CONOCIMIENTO) cae a `exacta` y,
  // al no ser la activa, NO se llama a knowledge. El backend re-valida de todos modos.
  const tabsPermitidas = tabsBusquedaPermitidas(user);
  const tab = tabsPermitidas.includes(rawTab) ? rawTab : "exacta";

  // ---------------------------------------------------------------------
  // Pestaña Semántica — solo se resuelve si es la pestaña activa.
  // ---------------------------------------------------------------------
  const sq = url.searchParams.get("sq") ?? "";
  const stopk = enteroDeLaUrl(url, "stopk", { min: 1, max: 50, porDefecto: 5 });
  let semantica = { results: null, error: null };
  if (tab === "semantica") {
    semantica = await loadSemantica({ token, tenant, userId, query: sq.trim(), topK: stopk });
  }

  // ---------------------------------------------------------------------
  // Pestaña Antecedentes — solo se resuelve si es la pestaña activa.
  // ---------------------------------------------------------------------
  const aq = url.searchParams.get("aq") ?? "";
  const atr = url.searchParams.get("atr") ?? "";
  const atopk = enteroDeLaUrl(url, "atopk", { min: 1, max: 20, porDefecto: 5 });
  let antecedentes = { data: null, error: null };
  if (tab === "antecedentes") {
    antecedentes = await loadAntecedentes({
      token,
      tenant,
      userId,
      query: aq.trim(),
      radicadoRef: atr.trim(),
      topK: atopk,
    });
  }

  // Leer filtros de la URL (deep-link friendly)
  const q = url.searchParams.get("q") ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  // dest_dept_code: aceptar solo enteros positivos; descartar texto libre (422)
  const rawDeptCode = url.searchParams.get("dest_dept_code") ?? "";
  const destDeptCode =
    rawDeptCode !== "" && /^\d+$/.test(rawDeptCode.trim())
      ? String(Math.floor(Number(rawDeptCode)))
      : "";
  // ubicacion_dept_code (ADR-029): dónde está AHORA el radicado; mismo saneo que arriba
  const rawUbicCode = url.searchParams.get("ubicacion_dept_code") ?? "";
  const ubicacionDeptCode =
    rawUbicCode !== "" && /^\d+$/.test(rawUbicCode.trim())
      ? String(Math.floor(Number(rawUbicCode)))
      : "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  let results = [];
  let total = 0;
  let loadError = null;

  // Solo llamar al backend de Exacta si es la pestaña activa y hay ≥2 caracteres
  // de query (evita gastar una llamada al gateway resolviendo una pestaña que
  // ni siquiera se va a renderizar).
  if (tab === "exacta" && q.length >= 2) {
    const params = new URLSearchParams({ q, page: String(page), size: String(size) });
    if (docType) params.set("doc_type", docType);
    if (status) params.set("status", status);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (destDeptCode) params.set("dest_dept_code", destDeptCode);
    if (ubicacionDeptCode) params.set("ubicacion_dept_code", ubicacionDeptCode);

    try {
      const res = await fetch(`${API_V1}/search?${params.toString()}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
          "X-User-Id": userId,
        },
      });

      if (res.ok) {
        const data = await res.json();
        // SearchResponse devuelve { query, total, hits, page, size }; `hits` es el
        // nombre real del campo en app/schemas/search.py. Se mantienen `items`/`results`
        // como respaldo por si otro endpoint reutiliza este normalizador.
        if (Array.isArray(data)) {
          results = data;
          total = Number(res.headers.get("X-Total-Count") ?? data.length);
        } else {
          results = data.hits ?? data.items ?? data.results ?? [];
          total = data.total ?? Number(res.headers.get("X-Total-Count") ?? 0);
        }
      } else {
        const body = await res.json().catch(() => ({}));
        loadError = mensajeError(body, `Error ${res.status}`);
      }
    } catch {
      loadError = "No se pudo conectar al servidor de búsqueda.";
    }
  }

  // Catálogo de dependencias activas para el filtro «Ubicación actual». Un fallo
  // degrada a lista vacía: el filtro queda en «Cualquiera», la búsqueda sigue.
  /** @type {Array<{codigo:number, nombre:string}>} */
  let dependencias = [];
  try {
    const depRes = await fetch(`${API_V1}/dependencias?activo=true`, {
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });
    if (depRes.ok) {
      const lista = await depRes.json().catch(() => []);
      dependencias = Array.isArray(lista) ? lista : [];
    }
  } catch {
    dependencias = [];
  }

  return {
    tab,
    tabsPermitidas,
    results,
    dependencias,
    total,
    loadError,
    // Filtros activos para inicializar el formulario en el cliente
    initialFilters: {
      q,
      docType,
      status,
      dateFrom,
      dateTo,
      destDeptCode,
      ubicacionDeptCode,
      page,
    },

    // Pestaña Semántica
    semanticaResults: semantica.results,
    semanticaError: semantica.error,
    semanticaInitial: { sq, stopk },

    // Pestaña Antecedentes
    antecedentesData: antecedentes.data,
    antecedentesError: antecedentes.error,
    antecedentesInitial: { aq, atr, atopk },

    // token y user NO se exponen: las llamadas interactivas usan los proxies de
    // /busqueda/api (D-05)
  };
}
