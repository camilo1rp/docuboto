// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** /radicacion/api/reto — proxy SSR del reto antirrobots: anónimo, sin caché, sin filtrar nada. */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { GET } = await import("./api/reto/+server.js");

const RETO = {
  algorithm: "SHA-256",
  challenge: "a".repeat(64),
  maxnumber: 100000,
  salt: "b".repeat(32) + "?expires=1&t=demo&v=1",
  signature: "c".repeat(64),
};

describe("GET /radicacion/api/reto", () => {
  it("pide el reto del tenant al gateway y lo devuelve con Cache-Control: no-store", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => RETO });
    const res = await GET({ fetch });
    expect(String(fetch.mock.calls[0][0])).toBe("http://gw/api/v1/public/demo/ventanilla/reto");
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(await res.json()).toEqual(RETO);
  });

  it("solo reenvía los cinco campos del reto (allowlist)", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ ...RETO, hmac_key: "x" }) });
    const cuerpo = await (await GET({ fetch })).json();
    expect(cuerpo).toEqual(RETO);
    expect(cuerpo.hmac_key).toBeUndefined();
  });

  it("no envía credenciales: es una ruta pública", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => RETO });
    await GET({ fetch });
    const init = fetch.mock.calls[0][1] ?? {};
    expect(JSON.stringify(init.headers ?? {})).not.toMatch(/authorization|x-tenant|x-user/i);
  });

  it("503 del backend -> antirrobots_no_disponible con presencial y correo, sin caché", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: "antirrobots_no_disponible" }),
    });
    const res = await GET({ fetch });
    expect(res.status).toBe(503);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const cuerpo = await res.json();
    expect(cuerpo.error).toBe("antirrobots_no_disponible");
    expect(cuerpo.detail).toMatch(/presencial/i);
    expect(cuerpo.detail).toMatch(/correo/i);
  });

  it("el gateway caído también da antirrobots_no_disponible", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await GET({ fetch });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("antirrobots_no_disponible");
  });

  it("otro error (400 slug inválido) conserva el código y su estado", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: { error: "invalid_tenant_slug" } }),
    });
    const res = await GET({ fetch });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("invalid_tenant_slug");
  });
});
