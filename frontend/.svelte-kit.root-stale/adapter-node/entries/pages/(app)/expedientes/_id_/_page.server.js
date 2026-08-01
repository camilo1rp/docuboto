import { e as error } from "../../../../../chunks/index.js";
import { C as COOKIE_ACCESS_TOKEN } from "../../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
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
async function load({ params, cookies, parent }) {
  const { id } = params;
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";
  if (!id) {
    error(400, { message: "ID de expediente requerido" });
  }
  const [resumenResult, indiceResult, eventosResult, ubicacionResult] = await Promise.allSettled([
    // Faceta 1: Resumen (expediente + radicados[])
    gatewayGet(`/expedientes/${id}`, token, tenant, userId ? { "X-User-Id": userId } : {}),
    // Faceta 2: Índice electrónico E15
    gatewayGet(`/expedientes/${id}/indice`, token, tenant),
    // Faceta 3: Eventos / hoja de ruta (E02 §9); X-User-Id acota por clearance
    gatewayGet(`/expedientes/${id}/eventos`, token, tenant, userId ? { "X-User-Id": userId } : {}),
    // Faceta 4: Ubicación física E17
    gatewayGet(`/expedientes/${id}/unidades`, token, tenant)
  ]);
  if (resumenResult.status === "rejected") {
    const err = resumenResult.reason;
    if (err?.status === 404) {
      error(404, { message: `Expediente ${id} no encontrado` });
    }
    if (err?.status === 403) {
      error(403, { message: "Sin permisos para ver este expediente" });
    }
    error(500, { message: err?.detail ?? "Error al cargar el expediente" });
  }
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
    expediente: resumenResult.value,
    facetas: {
      indice: settle(indiceResult),
      eventos: settle(eventosResult),
      ubicacion: settle(ubicacionResult)
    }
    // token, tenant y userId NO se exponen: las acciones cliente usan /expedientes/[id]/api/* (D-05)
  };
}
export {
  load
};
