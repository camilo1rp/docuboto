// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * archivo-fisico/api/ubicaciones/[id] — Proxy SSR de la corrección de una
 * ubicación (PATCH /api/v1/ubicaciones/{id}).
 *
 * Existe porque faltaba entera la corrección: `tipo_archivo` es opcional al
 * crear y decide qué transferencias admite la ubicación (Ley 594/2000 art. 23,
 * ciclo vital). Una topografía cargada sin ese dato dejaba el archivo físico
 * sin poder transferir y sin forma de arreglarlo desde el sistema.
 *
 * Allowlist estricta: solo viajan los campos que el backend admite corregir.
 * `codigo` y `parent_id` NO están, y no por olvido — el código es la referencia
 * impresa en la signatura de cada unidad, y el padre movería de golpe todo lo
 * que cuelga de la ubicación.
 *
 * Gate real: `USUA_PERM_EXPEDIENTE` con `crud >= 3`
 * (`services/archive-service/app/routers/fisico.py`).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const TIPOS_ARCHIVO = ["gestion", "central", "historico"];

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, request, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof body?.nombre === "string" && body.nombre.trim()) payload.nombre = body.nombre.trim();
  if (typeof body?.tipo_archivo === "string" && TIPOS_ARCHIVO.includes(body.tipo_archivo)) {
    payload.tipo_archivo = body.tipo_archivo;
  }
  if (body?.capacidad_ml !== undefined && body.capacidad_ml !== null && body.capacidad_ml !== "") {
    const n = Number(body.capacidad_ml);
    if (Number.isFinite(n) && n >= 0) payload.capacidad_ml = n;
  }
  if (typeof body?.activo === "boolean") payload.activo = body.activo;

  if (Object.keys(payload).length === 0) {
    return json(
      { error: "sin_cambios", detail: "No se indicó ningún campo que corregir." },
      { status: 422 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/ubicaciones/${encodeURIComponent(params.id)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servicio de archivo." },
      { status: 502 },
    );
  }
}
