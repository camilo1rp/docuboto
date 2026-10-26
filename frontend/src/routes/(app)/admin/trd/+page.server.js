// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/trd/+page.server.js — Carga SSR de series TRD/CCD y tipos documentales
 * (E04, Fase 5 cierre de desfase API↔UI).
 *
 * `?incluir_en_tramite=true` (C-4): sin él el backend sirve solo el instrumento
 * publicado (convalidada/migrada), sin acta del Comité ni autor, y la pantalla
 * dejaría de ver borradores y aprobadas. Exige USUA_PERM_TRD (403 sin él).
 *
 * Dos catálogos independientes de archive-service:
 *   - GET /trd (paginado, X-Total-Count) — series jerárquicas (parent_id).
 *     Se traen TODAS las páginas: la vista construye el árbol en cliente y una
 *     carga parcial rompe la jerarquía — una subserie cuyo padre quedó fuera no
 *     aparece en ninguna rama, así que el árbol no se ve incompleto, se ve
 *     distinto. Antes se pedía `size=200` «para traer el árbol completo de una
 *     vez», pero 200 ES EL TOPE del backend (`le=200`), no una holgura: con más
 *     de 200 series la vista se quedaba corta en silencio, con el
 *     `X-Total-Count` delante sin que nadie lo mirara. Si ni siquiera así cabe,
 *     `seriesIncompletas` lo dice en pantalla.
 *   - GET /tipos-documentales (sin paginar) — cuelgan de una serie o van sueltos.
 *
 * RBAC real (espejo, D-11): el backend gatea creación/edición con
 * USUA_PERM_TRD (no USUA_PERM_ADMIN) — ver services/archive-service/app/
 * routers/trd.py y tipos_documentales.py. USUA_PERM_ADMIN solo controla la
 * VISIBILIDAD de la sección /admin en el dashboard (admin/+page.svelte);
 * dentro de esta pantalla el botón "Nueva serie"/"Nuevo tipo" se gatea con
 * el permiso real que el backend exige, para no ofrecer un botón que el
 * backend rechazaría a un USUA_PERM_ADMIN sin USUA_PERM_TRD, ni ocultarlo a
 * un administrador de TRD sin USUA_PERM_ADMIN general.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";
import { traerListaCompleta } from "$lib/server/listaCompleta.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  const seriesCargadas = await traerListaCompleta(
    fetch,
    `${API_V1}/trd?incluir_en_tramite=true`,
    headers,
    mensajeError,
  );
  const series = seriesCargadas.items;
  const seriesError =
    seriesCargadas.error === "No se pudo conectar al servidor."
      ? "No se pudo conectar al servidor de TRD."
      : seriesCargadas.error;

  let tipos = [];
  let tiposError = null;
  try {
    const res = await fetch(`${API_V1}/tipos-documentales`, { headers });
    if (res.ok) {
      tipos = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      tiposError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    tiposError = "No se pudo conectar al servidor de tipos documentales.";
  }

  // Cola de revisión (ADR-025 D4/D6). Se carga aquí, no bajo demanda, porque
  // su razón de ser es que ALGUIEN la vea: son expedientes ya cerrados que el
  // backfill marcó y que sin pantalla quedaban marcados para siempre.
  let revision = [];
  let revisionError = null;
  try {
    const res = await fetch(`${API_V1}/trd/revision-pendiente?size=200`, { headers });
    if (res.ok) {
      revision = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      revisionError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    revisionError = "No se pudo conectar al servidor para consultar la cola de revisión.";
  }

  // Cola de vencimientos (RF-TRD-08). Mismo criterio que la de revisión: se
  // carga aquí porque su razón de ser es que ALGUIEN la vea. Se pide la fase
  // `central` —la que desemboca en la disposición final— porque es la que exige
  // una decisión; la fase `gestion` es una transferencia rutinaria y se ofrece
  // como filtro dentro de la pestaña.
  let vencidos = [];
  let vencidosError = null;
  try {
    const res = await fetch(`${API_V1}/trd/disposicion-vencida?fase=central&size=200`, {
      headers,
    });
    if (res.ok) {
      vencidos = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      vencidosError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    vencidosError = "No se pudo conectar al servidor para consultar los vencimientos.";
  }

  // Dependencias activas: alimentan el selector de productoras (por `codigo`,
  // ADR-032). Si falla, la pantalla sigue; solo el selector queda vacío y lo
  // dice (503 catalogo_dependencias_no_disponible o error de red).
  let dependencias = [];
  let dependenciasError = null;
  try {
    const res = await fetch(`${API_V1}/dependencias?activo=true`, { headers });
    if (res.ok) {
      const lista = await res.json().catch(() => []);
      dependencias = Array.isArray(lista) ? lista : [];
    } else {
      const body = await res.json().catch(() => ({}));
      dependenciasError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    dependenciasError = "No se pudo conectar al servidor de dependencias.";
  }

  return {
    dependencias,
    dependenciasError,
    series,
    seriesError,
    // La vista lo avisa: una lista corta con cara de completa es peor que un
    // error, porque nadie va a buscar lo que no sabe que falta.
    seriesIncompletas: seriesCargadas.incompleta,
    seriesTotal: seriesCargadas.total,
    tipos,
    tiposError,
    revision,
    revisionError,
    vencidos,
    vencidosError,
  };
}
