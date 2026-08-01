import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function gatewayGet(path, token, tenant, extraHeaders = {}) {
  const res = await fetch(`${API_V1}${path}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
      ...extraHeaders
    }
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
      status: res.status
    }));
    throw {
      code: body.error ?? "upstream_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status
    };
  }
  return res.json();
}
async function load({ cookies, parent }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const [ubicacionesResult, prestamosActivosResult, prestamosVencidosResult] = await Promise.allSettled([
    // Faceta 1: Ubicaciones raíz (sin parent_id → el backend devuelve las de nivel raíz)
    gatewayGet("/ubicaciones?activo=true", token, tenant),
    // Faceta 2: Préstamos activos
    gatewayGet("/prestamos?estado=prestado", token, tenant),
    // Faceta 3: Préstamos vencidos
    gatewayGet("/prestamos?estado=vencido", token, tenant)
  ]);
  function settle(result) {
    if (result.status === "fulfilled") {
      return { data: result.value, error: null };
    }
    return {
      data: null,
      error: {
        code: result.reason?.code ?? "unknown_error",
        detail: result.reason?.detail ?? "Error desconocido",
        status: result.reason?.status ?? 500
      }
    };
  }
  return {
    facetas: {
      ubicaciones: settle(ubicacionesResult),
      prestamosActivos: settle(prestamosActivosResult),
      prestamosVencidos: settle(prestamosVencidosResult)
    }
    // token, tenant y userId NO se exponen: las acciones cliente usan /archivo-fisico/api/* (D-05)
  };
}
export {
  load
};
