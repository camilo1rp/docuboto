// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /busqueda/api/by-tracking/[trackingNumber] — Proxy SSR de resolución de
 * radicado por número (D-05, Fase 6 E21↔UI).
 *
 * Endpoint real: GET /api/v1/documents/by-tracking/{tracking_number}
 *   (services/document-service/app/routers/documents.py:211, contra
 *   document-service, NO knowledge-service).
 *
 * Uso: la pestaña "Antecedentes" ofrece dos modos de búsqueda —por texto o por
 * un radicado pivote (`radicado_ref`)—; este proxy es el paso de VERIFICACIÓN
 * (¿a qué radicado corresponde el número que tecleó el operador?) antes de
 * lanzar `/busqueda/api/antecedentes` con ese `radicado_ref`. No sustituye la
 * llamada a antecedentes, solo confirma el asunto/tipo del documento pivote.
 *
 * El backend colapsa "no existe" y "clearance insuficiente" en el MISMO 404
 * (no-read-up, RF-SEG-08) — este proxy relaya ese 404 genérico sin enriquecerlo
 * y NO reenvía el documento completo: solo los campos necesarios para que el
 * operador confirme que es el radicado correcto (allowlist de salida).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const trackingNumber = (params.trackingNumber ?? "").trim();

  if (!trackingNumber) {
    return json(
      { error: "validation_error", detail: "Falta el número de radicado." },
      { status: 422 },
    );
  }

  try {
    const res = await fetch(
      `${API_V1}/documents/by-tracking/${encodeURIComponent(trackingNumber)}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
          "X-User-Id": userId,
        },
      },
    );

    if (res.status === 404) {
      return json(
        { error: "not_found", detail: "No se encontró ese radicado.", status: 404 },
        { status: 404 },
      );
    }

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return json(data, { status: res.status });
    }

    // Allowlist de SALIDA: solo lo necesario para que el operador confirme el
    // pivote — no se reenvía el documento completo (metadata/anexos/observations).
    return json({
      tracking_number: data.tracking_number,
      doc_type: data.doc_type,
      subject: data.subject,
      status: data.status,
      registered_at: data.registered_at,
      dest_dept: data.dest_dept ?? null,
      sender_name: data.sender_name ?? null,
      sender_entity: data.sender_entity ?? null,
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}
