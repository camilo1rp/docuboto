import { j as json } from "../../../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { documentId, fileId } = params;
  if (!documentId || !UUID_RE.test(documentId)) {
    return json(
      { error: "validation_error", detail: "El identificador del radicado no es válido." },
      { status: 400 }
    );
  }
  if (!fileId || !UUID_RE.test(fileId)) {
    return json(
      { error: "validation_error", detail: "El identificador del anexo no es válido." },
      { status: 400 }
    );
  }
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  let res;
  try {
    res = await fetch(`${API_V1}/documents/${documentId}/anexos/${fileId}/download`, {
      headers
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el anexo." },
      { status: 503 }
    );
  }
  if (!res.ok) {
    if (res.status === 404) {
      return json(
        { error: "not_found", detail: "El anexo no está disponible." },
        { status: 404 }
      );
    }
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`
    }));
    return json(body, { status: res.status });
  }
  const outHeaders = new Headers();
  const contentType = res.headers.get("content-type");
  const contentDisposition = res.headers.get("content-disposition");
  const contentLength = res.headers.get("content-length");
  outHeaders.set("Content-Type", contentType ?? "application/octet-stream");
  if (contentDisposition) outHeaders.set("Content-Disposition", contentDisposition);
  if (contentLength) outHeaders.set("Content-Length", contentLength);
  return new Response(res.body, { status: 200, headers: outHeaders });
}
export {
  GET
};
