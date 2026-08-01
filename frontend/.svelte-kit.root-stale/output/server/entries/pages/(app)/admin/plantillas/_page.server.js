import { C as COOKIE_ACCESS_TOKEN } from "../../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
import { a as attachSafeCuerpo } from "../../../../../chunks/sanitizeHtml.js";
async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  let plantillas = [];
  let loadError = null;
  try {
    const res = await fetch(`${API_V1}/plantillas`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      if (Array.isArray(raw)) {
        raw.forEach(attachSafeCuerpo);
        plantillas = raw;
      }
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de plantillas.";
  }
  return {
    plantillas,
    loadError
    // token y tenant NO se exponen: las llamadas cliente usan /admin/plantillas/api/* (D-05)
  };
}
export {
  load
};
