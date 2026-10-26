// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * /admin/preservacion/api/sellos-archivo — Proxy SSR del listado de sellos de
 * archivo (C-6, ADR-042): `GET /api/v1/signature/admin/sellos-archivo`.
 *
 * Reenvía `situacion` (repetible, lista cerrada), `dias`, `page` y `size`
 * (tope 100: el backend responde 422 por encima) y devuelve `X-Total-Count`.
 * USUA_PERM_ADMIN ≥3 en el backend (403 sin él): se propaga tal cual y la UI
 * lo muestra como «Sin permiso», nunca como «no hay sellos».
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const SITUACIONES = new Set([
  "por_vencer",
  "vencido",
  "sin_revocacion",
  "pendiente_worm",
  "rechazado",
]);

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const params = new URLSearchParams();
  for (const s of url.searchParams.getAll("situacion")) {
    if (SITUACIONES.has(s)) params.append("situacion", s);
  }
  const dias = url.searchParams.get("dias");
  if (dias && /^\d{1,5}$/.test(dias)) params.set("dias", dias);
  const page = Math.max(1, Number(url.searchParams.get("page")) || 1);
  const size = Math.min(100, Math.max(1, Number(url.searchParams.get("size")) || 20));
  params.set("page", String(page));
  params.set("size", String(size));

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/admin/sellos-archivo?${params}`, { headers });
    const data = await res.json().catch(() => ({}));
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : undefined,
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar los sellos de archivo." },
      { status: 503 },
    );
  }
}
