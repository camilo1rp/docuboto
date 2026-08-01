import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  const [depRes, trdRes, tiposRes] = await Promise.allSettled([
    fetch(`${API_V1}/dependencias?activo=true`, { headers }),
    fetch(`${API_V1}/trd?is_active=true&size=100`, { headers }),
    fetch(`${API_V1}/tipos-documentales?activo=true`, { headers })
  ]);
  let dependencias = [];
  if (depRes.status === "fulfilled" && depRes.value.ok) {
    dependencias = await depRes.value.json().catch(() => []);
  }
  let trdSeries = [];
  if (trdRes.status === "fulfilled" && trdRes.value.ok) {
    trdSeries = await trdRes.value.json().catch(() => []);
  }
  let tiposDocumentales = [];
  if (tiposRes.status === "fulfilled" && tiposRes.value.ok) {
    tiposDocumentales = await tiposRes.value.json().catch(() => []);
  }
  return {
    user,
    dependencias,
    trdSeries,
    tiposDocumentales
    // token y tenant NO se exponen: las llamadas cliente usan /radicar/api/* (D-05)
  };
}
export {
  load
};
