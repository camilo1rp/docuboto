// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id] — Proxy SSR de detalle de una transferencia
 * documental (D-05, E12) — `GET /transferencias/{id}`.
 *
 * Gemelo de lectura de `[id]/fuid`, `[id]/acta`, `[id]/acta/verificacion` —
 * usado por la subruta de detalle `/transferencias/[id]` para refrescar el
 * encabezado (estado, responsables) tras una acción de escritura sin volver
 * a disparar `load()` completo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  try {
    const res = await fetch(`${API_V1}/transferencias/${encodeURIComponent(id)}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener la transferencia." },
      { status: 503 },
    );
  }
}
