// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/api/metadata-templates — Proxy BFF de solo-lectura (D-05).
 *
 * Único proxy bajo `expedientes/` (el listado en sí es SSR-puro, ver
 * +page.server.js). Se usa desde el Modal de creación de expediente (T-12
 * Form Builder) cuando el usuario elige una serie TRD: trae la(s) plantilla(s)
 * de metadatos de la serie para renderizar los campos dinámicos del
 * `json_schema` ANTES de enviar el form (estado local del Modal — un `load`
 * SSR vía URL perdería ese estado, ver contrato orfeo-architect §1).
 *
 * El token se lee de `locals.accessToken` (cookie httpOnly) y nunca se
 * expone al JS del cliente.
 *
 * GET → lista las plantillas de metadatos de expediente de una serie TRD.
 *   Parámetros: trd_serie_id (UUID, único param aceptado — allowlist)
 *   Respuesta: ExpedienteTemplateResponse[] — el cliente elige `.activo`.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  // Allowlist estricta: el ÚNICO query param aceptado es trd_serie_id, y solo
  // si tiene forma de UUID — cualquier otra cosa es 400 SIN llamar al gateway.
  const trdSerieId = url.searchParams.get("trd_serie_id") ?? "";
  if (!UUID_RE.test(trdSerieId)) {
    return json(
      { error: "invalid_param", detail: "trd_serie_id debe ser un UUID válido." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(
      `${API_V1}/expediente-metadata/templates?trd_serie_id=${encodeURIComponent(trdSerieId)}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
      },
    );

    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo cargar la plantilla de metadatos de la serie.",
      },
      { status: 503 },
    );
  }
}
