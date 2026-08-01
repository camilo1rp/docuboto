import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function load({ cookies, parent }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
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
  const [transferenciasResult] = await Promise.allSettled([
    (async () => {
      const res = await fetch(`${API_V1}/transferencias`, {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant
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
    })()
  ]);
  return {
    transferencias: settle(transferenciasResult)
    // token y tenant NO se exponen: las acciones usan /transferencias/api/* (D-05)
  };
}
export {
  load
};
