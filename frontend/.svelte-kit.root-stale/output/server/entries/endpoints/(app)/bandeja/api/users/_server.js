import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const sizeParam = Number(url.searchParams.get("size") ?? "100");
  const size = Number.isFinite(sizeParam) ? Math.min(Math.max(sizeParam, 1), 500) : 100;
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/auth/users/pickable?size=${size}`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return json(
        { error: err.error ?? "upstream_error", detail: err.detail ?? "No se pudo cargar la lista de usuarios." },
        { status: res.status }
      );
    }
    const list = await res.json().catch(() => []);
    const users = (Array.isArray(list) ? list : []).map((u) => ({
      id: u.id,
      username: u.username
    }));
    return json({ users }, { status: 200 });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar la lista de usuarios." },
      { status: 503 }
    );
  }
}
export {
  GET
};
