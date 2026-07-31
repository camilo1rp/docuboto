// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/sign-xml — Proxy SSR del sellado institucional/nativo directo
 * (E06, ADR-016, D-05) — `POST /api/v1/signature/sign-xml`.
 *
 * A diferencia de `/firmas/api/[id]/sign` (que firma el turno ACTIVO de una
 * cadena ya creada), esta ruta firma un `payload_xml` arbitrario de una sola
 * vez, sin cadena ni segundo factor — el backend la gatea solo con
 * PERM_FIRMA. El firmante se toma del JWT (RF-FIR-11); nunca del body.
 *
 * POST { payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version? }
 *   → FirmaResponse (201)
 *
 * El front NUNCA compone criptografía: solo reenvía el `payload_xml` que el
 * llamador ya tiene (p. ej. el índice electrónico serializado por otra
 * pantalla) — este proxy es infraestructura reutilizable, no expone un
 * formulario de "pegue su XML" en /firmas (ver CLAUDE.md: la clave privada y
 * el acto criptográfico viven en servidor).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
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

  const { payload_xml, objeto_tipo, objeto_id, formato, indice_id, version } = body ?? {};

  if (!payload_xml || typeof payload_xml !== "string" || !payload_xml.trim()) {
    return json(
      { error: "validation_error", detail: "payload_xml es obligatorio." },
      { status: 422 },
    );
  }
  if (!objeto_tipo || typeof objeto_tipo !== "string" || !objeto_tipo.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_tipo es obligatorio." },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { payload_xml, objeto_tipo };
  if (objeto_id) payload.objeto_id = String(objeto_id);
  if (formato) payload.formato = String(formato);
  if (indice_id) payload.indice_id = String(indice_id);
  if (version !== undefined && version !== null) payload.version = Number(version);

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/sign-xml`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
