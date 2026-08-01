import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
import { a as attachSafeCuerpo } from "../../../../chunks/sanitizeHtml.js";
async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  const estadoFilter = url.searchParams.get("estado") ?? "";
  const borradoresParams = new URLSearchParams();
  if (estadoFilter) borradoresParams.set("estado", estadoFilter);
  const [borradoresRes, plantillasRes] = await Promise.allSettled([
    fetch(`${API_V1}/borradores?${borradoresParams.toString()}`, { headers }),
    fetch(`${API_V1}/plantillas?activo=true`, { headers })
  ]);
  let borradores = [];
  let loadError = null;
  if (borradoresRes.status === "fulfilled" && borradoresRes.value.ok) {
    const raw = await borradoresRes.value.json().catch(() => []);
    if (Array.isArray(raw)) {
      raw.forEach(attachSafeCuerpo);
      borradores = raw;
    }
  } else if (borradoresRes.status === "fulfilled") {
    const body = await borradoresRes.value.json().catch(() => ({}));
    loadError = body.detail ?? `Error ${borradoresRes.value.status}`;
  } else {
    loadError = "No se pudo conectar al servidor de borradores.";
  }
  let plantillas = [];
  if (plantillasRes.status === "fulfilled" && plantillasRes.value.ok) {
    const raw = await plantillasRes.value.json().catch(() => []);
    if (Array.isArray(raw)) {
      raw.forEach(attachSafeCuerpo);
      plantillas = raw;
    }
  }
  return {
    borradores,
    plantillas,
    loadError,
    initialEstado: estadoFilter
    // token y tenant NO se exponen: las llamadas cliente usan /borradores/api/* (D-05)
  };
}
export {
  load
};
