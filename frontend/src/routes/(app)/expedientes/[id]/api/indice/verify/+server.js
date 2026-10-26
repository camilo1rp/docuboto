// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/indice/verify — Proxy SSR: verifica la integridad y
 * la firma del índice electrónico (D-05, ADR-011, Fase 2, E06/E15).
 *
 * GET /api/v1/expedientes/{id}/indice/verify?version → `IndiceVerifyResponse`
 * (archive-service): { version, valido, huella_indice_ok, documentos[],
 * firma, fecha_verificacion }. Contrasta el fixity del XML almacenado y de
 * cada documento referenciado contra la firma XAdES-B.
 *
 * GET → resultado de la verificación (para la versión vigente o una versión
 *   histórica concreta).
 *   Parámetros: version (entero, opcional — vigente si se omite)
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const rawVersion = url.searchParams.get("version");
  const qs = new URLSearchParams();
  if (rawVersion && /^\d+$/.test(rawVersion)) qs.set("version", rawVersion);
  const versionParam = qs.toString() ? `?${qs}` : "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/expedientes/${encodeURIComponent(id)}/indice/verify${versionParam}`,
      { headers },
    );

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo verificar el índice." },
      { status: 503 },
    );
  }
}
