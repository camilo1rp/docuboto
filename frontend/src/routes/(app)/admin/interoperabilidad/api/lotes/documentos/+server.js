// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/documentos — Proxy SSR de carga masiva de
 * radicados (D-05, Fase 8).
 *
 * POST /api/v1/batch/documents — cuerpo {documents: BatchDocumentCreate[]}
 * (1-1000). Procesamiento asíncrono: responde 202 con job_id; el progreso se
 * consulta con GET .../[jobId]/status (sondeo desde el propio proxy, no
 * contra el gateway directamente).
 *
 * Gate real: `PERM_RADI` (`services/document-service/app/routers/batch.py`).
 * El gateway (`services/api-gateway/app/routers/proxy.py`) tiene registrado
 * `/api/v1/batch/documents` hacia document-service.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const DOC_TYPE_RE = /^[ESI]$/;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    return json(
      { error: "unauthenticated", detail: "Sesión sin identidad de usuario." },
      { status: 401 },
    );
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    return json({ error: "validation_error", detail: "Cuerpo JSON inválido." }, { status: 400 });
  }

  const rawDocuments = Array.isArray(body.documents) ? body.documents : [];
  if (rawDocuments.length < 1 || rawDocuments.length > 1000) {
    return json(
      { error: "validation_error", detail: "Debe incluir entre 1 y 1000 documentos." },
      { status: 400 },
    );
  }

  // Allowlist campo a campo por ítem — espejo exacto de BatchDocumentCreate.
  const documents = rawDocuments.map((d) => {
    /** @type {Record<string, unknown>} */
    const item = {
      doc_type: typeof d?.doc_type === "string" ? d.doc_type : "",
      subject: typeof d?.subject === "string" ? d.subject : "",
    };
    if (typeof d?.doc_class === "string" && d.doc_class.trim()) item.doc_class = d.doc_class.trim();
    if (typeof d?.dest_dept === "string" && d.dest_dept.trim()) item.dest_dept = d.dest_dept.trim();
    if (typeof d?.sender_name === "string" && d.sender_name.trim())
      item.sender_name = d.sender_name.trim();
    if (typeof d?.sender_entity === "string" && d.sender_entity.trim())
      item.sender_entity = d.sender_entity.trim();
    if (Number.isFinite(d?.pages)) item.pages = d.pages;
    if (typeof d?.observations === "string" && d.observations.trim())
      item.observations = d.observations.trim();
    return item;
  });

  const invalid = documents.find(
    (d) => !DOC_TYPE_RE.test(String(d.doc_type)) || !String(d.subject).trim(),
  );
  if (invalid) {
    return json(
      { error: "validation_error", detail: "Cada documento requiere doc_type (E/S/I) y subject." },
      { status: 400 },
    );
  }

  let res;
  try {
    res = await fetch(`${API_V1}/batch/documents`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify({ documents }),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de carga masiva." },
      { status: 503 },
    );
  }

  const data = await res.json().catch(() => ({
    error: "upstream_error",
    detail: `HTTP ${res.status}`,
    status: res.status,
  }));
  return json(data, { status: res.status });
}
