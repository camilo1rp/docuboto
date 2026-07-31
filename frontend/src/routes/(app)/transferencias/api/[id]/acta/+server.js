// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta — Proxy SSR del acta de entrega CONGELADA
 * (E12 H-I, D-05) — `GET /transferencias/{id}/acta?verify=`.
 *
 * `verify=true` dispara en el backend el cómputo ON-DEMAND del bloque
 * `firma.valida` (descarga del XML firmado + signature-service /verify) —
 * costoso, así que solo se reenvía cuando el cliente lo pide explícitamente
 * (botón "Verificar sello"), nunca en la carga inicial de la página.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;
  const verify = url.searchParams.get("verify") === "true";

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(id)}/acta${verify ? "?verify=true" : ""}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener el acta de entrega." },
      { status: 503 },
    );
  }
}
