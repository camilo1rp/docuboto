// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil — Carga SSR del estado del segundo factor (TOTP) y de la
 * credencial de firma personal PKI (Fase 3, cierre de desfase API↔UI).
 *
 * Facetas EN PARALELO con Promise.allSettled (mismo patrón que
 * `expedientes/[id]/+page.server.js`, T-10): el fallo de una no tumba la
 * otra — cada una expone su propio estado de error.
 *
 *   1. TOTP         → GET /api/v1/auth/me/totp          (NUNCA el secreto)
 *   2. Firma (PKI)  → GET /api/v1/auth/me/signing-key    (NUNCA la clave privada)
 *
 * El contexto de dependencia activa (`user.context`/`user.dependencias`) YA
 * lo carga `(app)/+layout.server.js` (lo necesita el conmutador del topbar
 * en cualquier pantalla) — SvelteKit lo fusiona automáticamente en
 * `data.user`; esta ruta no lo vuelve a pedir.
 *
 * Identidad y permisos efectivos: se muestran de `data.user` (ya resuelto
 * por el layout desde `/auth/me`) — sección de solo lectura, sin llamada
 * adicional.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/**
 * @param {string} path
 * @param {string} token
 * @param {string} tenant
 * @param {Record<string,string>} extraHeaders
 * @param {typeof fetch} fetchFn
 */
async function gatewayGet(path, token, tenant, extraHeaders, fetchFn) {
  const res = await fetchFn(`${API_V1}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
      ...extraHeaders,
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw {
      code: body.error ?? "upstream_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }
  return body;
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";
  const extraHeaders = userId ? { "X-User-Id": userId } : {};

  const [totpResult, signingKeyResult] = await Promise.allSettled([
    gatewayGet("/auth/me/totp", token, tenant, extraHeaders, fetch),
    gatewayGet("/auth/me/signing-key", token, tenant, extraHeaders, fetch),
  ]);

  const totpStatus =
    totpResult.status === "fulfilled" ? totpResult.value : { estado: "no_configurado" };
  const totpError =
    totpResult.status === "rejected"
      ? (totpResult.reason?.detail ?? "No se pudo consultar el estado del segundo factor.")
      : null;

  const signingKeyStatus =
    signingKeyResult.status === "fulfilled" ? signingKeyResult.value : { estado: "no_configurado" };
  const signingKeyError =
    signingKeyResult.status === "rejected"
      ? (signingKeyResult.reason?.detail ??
        "No se pudo consultar el estado de la credencial de firma.")
      : null;

  return { totpStatus, totpError, signingKeyStatus, signingKeyError };
}
