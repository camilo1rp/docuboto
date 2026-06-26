// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/storage — Endpoint de servidor para subida de archivos (D-05, ADR-011).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del cliente.
 * El userId se obtiene del claim `sub` del JWT en event.locals (no del cliente).
 * El checksum SHA-256 lo calcula el backend (RF-DIG-02).
 *
 * POST → sube un archivo al storage-service vía el gateway.
 *   Body: multipart/form-data con campo `file`
 *   Respuesta: FileResponse (id, filename, sha256, ...)
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let formData;
  try {
    formData = await request.formData();
  } catch {
    return json({ error: "invalid_form", detail: "FormData inválido." }, { status: 400 });
  }

  const file = formData.get("file");
  if (!file) {
    return json({ error: "missing_file", detail: "Campo 'file' requerido." }, { status: 400 });
  }

  // Re-construir FormData para reenviar al gateway con los headers de autenticación
  const upstreamForm = new FormData();
  upstreamForm.append("file", file);

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/storage/upload`, {
      method: "POST",
      headers,
      body: upstreamForm,
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de almacenamiento." },
      { status: 503 },
    );
  }
}
