// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** /admin/terceros/api — cola de fichas provisionales y acto de conciliación (C17). */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({ API_V1: "http://gw/api/v1" }));

const { GET, POST } = await import("./api/+server.js");

const ID = "11111111-1111-4111-8111-111111111111";
const DESTINO = "22222222-2222-4222-8222-222222222222";
const locals = {
  accessToken: "tok",
  user: { tenant_slug: "demo", sub: "kc-sub-1" },
};

function evGet(query, respuesta, l = locals) {
  return {
    locals: l,
    url: new URL(`http://x/admin/terceros/api?${query}`),
    fetch: vi.fn().mockResolvedValue(respuesta),
  };
}

function evPost(query, cuerpo, respuesta = { status: 200, json: async () => ({ ok: true }) }) {
  return {
    locals,
    url: new URL(`http://x/admin/terceros/api?${query}`),
    request: { json: async () => cuerpo },
    fetch: vi.fn().mockResolvedValue(respuesta),
  };
}

const cabecera = (valor) => ({ get: (k) => (k.toLowerCase() === "x-total-count" ? valor : null) });

describe("GET ?accion=por_conciliar", () => {
  it("pide la página al gateway y reenvía X-Total-Count", async () => {
    const ev = evGet("accion=por_conciliar&page=2&size=20", {
      status: 200,
      headers: cabecera("45"),
      json: async () => [{ id: ID }],
    });
    const res = await GET(ev);
    expect(String(ev.fetch.mock.calls[0][0])).toBe(
      "http://gw/api/v1/terceros/por-conciliar?page=2&size=20",
    );
    expect(ev.fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(ev.fetch.mock.calls[0][1].headers["X-Tenant-Slug"]).toBe("demo");
    expect(res.headers.get("X-Total-Count")).toBe("45");
    expect(await res.json()).toEqual([{ id: ID }]);
  });

  it("acota page y size: un valor basura no viaja al gateway", async () => {
    const ev = evGet("accion=por_conciliar&page=-4&size=9999", {
      status: 200,
      headers: cabecera(null),
      json: async () => [],
    });
    await GET(ev);
    expect(String(ev.fetch.mock.calls[0][0])).toBe(
      "http://gw/api/v1/terceros/por-conciliar?page=1&size=100",
    );
  });

  it("sin sesión da 401 y no llama al gateway", async () => {
    const ev = evGet("accion=por_conciliar", { status: 200 }, { user: null });
    const res = await GET(ev);
    expect(res.status).toBe(401);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it("un 403 del backend (PERM_RADI < 3) llega tal cual", async () => {
    const ev = evGet("accion=por_conciliar", {
      status: 403,
      headers: cabecera(null),
      json: async () => ({ detail: { error: "permission_denied" } }),
    });
    expect((await GET(ev)).status).toBe(403);
  });
});

describe("POST ?accion=conciliar", () => {
  it("reenvía accion, destino_id y motivo (y nada más) a /terceros/{id}/conciliar", async () => {
    const ev = evPost(`accion=conciliar&id=${ID}`, {
      accion: "vincular",
      destino_id: DESTINO,
      motivo: "mismo ciudadano",
      conciliada_por: "otro-usuario",
      identidad: "confirmada",
    });
    const res = await POST(ev);
    expect(res.status).toBe(200);
    const [url, init] = ev.fetch.mock.calls[0];
    expect(String(url)).toBe(`http://gw/api/v1/terceros/${ID}/conciliar`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      accion: "vincular",
      destino_id: DESTINO,
      motivo: "mismo ciudadano",
    });
    expect(init.headers["X-User-Id"]).toBe("kc-sub-1");
  });

  it("pasa `medio_verificacion` en su allowlist y lo omite si no viene (descartar)", async () => {
    const con = evPost(`accion=conciliar&id=${ID}`, {
      accion: "confirmar",
      medio_verificacion: "otro",
      motivo: "explicación suficiente",
    });
    await POST(con);
    expect(JSON.parse(con.fetch.mock.calls[0][1].body)).toEqual({
      accion: "confirmar",
      destino_id: null,
      motivo: "explicación suficiente",
      medio_verificacion: "otro",
    });
    const sin = evPost(`accion=conciliar&id=${ID}`, {
      accion: "descartar",
      motivo: "x".repeat(12),
    });
    await POST(sin);
    expect("medio_verificacion" in JSON.parse(sin.fetch.mock.calls[0][1].body)).toBe(false);
  });

  it("un medio fuera de los cuatro admitidos da 400 sin llamar al gateway", async () => {
    const ev = evPost(`accion=conciliar&id=${ID}`, {
      accion: "confirmar",
      medio_verificacion: "telepatia",
    });
    expect((await POST(ev)).status).toBe(400);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["acción desconocida", `accion=conciliar&id=${ID}`, { accion: "borrar" }],
    [
      "id de ruta inválido",
      "accion=conciliar&id=../x",
      { accion: "descartar", motivo: "x".repeat(12) },
    ],
    [
      "destino inválido",
      `accion=conciliar&id=${ID}`,
      { accion: "vincular", destino_id: "no-uuid" },
    ],
  ])("%s -> 400 sin llamar al gateway", async (_n, query, cuerpo) => {
    const ev = evPost(query, cuerpo);
    const res = await POST(ev);
    expect(res.status).toBe(400);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it("un 409 tercero_ya_conciliado llega con su código y estado", async () => {
    const ev = evPost(
      `accion=conciliar&id=${ID}`,
      { accion: "confirmar" },
      { status: 409, json: async () => ({ detail: { error: "tercero_ya_conciliado" } }) },
    );
    const res = await POST(ev);
    expect(res.status).toBe(409);
    expect((await res.json()).detail.error).toBe("tercero_ya_conciliado");
  });

  it("sin sesión da 401", async () => {
    const ev = evPost(`accion=conciliar&id=${ID}`, { accion: "confirmar" });
    ev.locals = { user: null };
    expect((await POST(ev)).status).toBe(401);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it("la fusión sigue funcionando por el mismo POST", async () => {
    const ev = evPost(`accion=fusionar&id=${ID}`, { absorbe_id: DESTINO });
    await POST(ev);
    expect(String(ev.fetch.mock.calls[0][0])).toBe(`http://gw/api/v1/terceros/${ID}/fusionar`);
  });
});
