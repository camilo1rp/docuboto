// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/prestamos — Proxy SSR listado de préstamos por estado (D-05, E17).
 *
 * GET → param estado (prestado|devuelto|vencido), reenvía a GET /prestamos?estado=
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ESTADOS_VALIDOS = ["prestado", "devuelto", "vencido"];

export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const params = new URLSearchParams();
  const estado = url.searchParams.get("estado") ?? "";
  if (estado && ESTADOS_VALIDOS.includes(estado)) params.set("estado", estado);

  // Paginación: se reenvía la que pida el cliente, con `size=100` —el tope del
  // backend— por defecto. `devuelto` es el histórico entero y antes llegaba sin
  // `LIMIT`; el `X-Total-Count` que ahora emite el servicio es lo que permite a
  // la pantalla decir «100 de 4.320» en vez de enseñar una ventana con cara de
  // lista completa.
  const page = url.searchParams.get("page");
  params.set("page", page && /^\d+$/.test(page) ? page : "1");
  const size = url.searchParams.get("size");
  params.set("size", size && /^\d+$/.test(size) ? size : "100");

  const qs = params.toString();

  try {
    const res = await fetch(`${API_V1}/prestamos${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los préstamos." },
      { status: 503 },
    );
  }
}
