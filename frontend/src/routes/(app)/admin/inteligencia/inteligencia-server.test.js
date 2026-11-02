// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Los proxies dejan pasar solo el acto. El token sale de la sesión. */
import { describe, it, expect, afterEach, vi } from "vitest";
import { PUT as putProveedor } from "./api/+server.js";
import { PUT as putNivel } from "./api/niveles/+server.js";
import { POST as postRetiro } from "./api/retiro/+server.js";

const SESSION = {
  accessToken: "tok-abc",
  user: { tenant_slug: "demo", sub: "33333333-3333-4333-8333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function cuerpo(body) {
  return { json: async () => body };
}

describe("proxy de inteligencia", () => {
  it("sin token no llama al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await putProveedor({ locals: {}, request: cuerpo({ provider: "local" }) });
    expect(res.status).toBe(401);
  });

  it("el cambio de proveedor no reenvía campos de más", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    globalThis.fetch = fetchMock;
    await putProveedor({
      locals: SESSION,
      request: cuerpo({
        provider: "ppi",
        transmission_statement: "  Example Provider  ",
        slug: "otra",
        send_metadata: "sí",
      }),
    });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api-gateway:8080/api/v1/config/intelligence");
    expect(opts.method).toBe("PUT");
    expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    expect(opts.headers["X-Tenant-Slug"]).toBe("demo");
    expect(JSON.parse(opts.body)).toEqual({
      provider: "ppi",
      transmission_statement: "Example Provider",
    });
  });

  it("un nivel que no es 2 ni 3 no sale", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await putNivel({
      locals: SESSION,
      request: cuerpo({ level: 1, enabled: false }),
    });
    expect(res.status).toBe(422);
  });

  it("deshabilitar un nivel no manda declaración vacía", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    globalThis.fetch = fetchMock;
    await putNivel({
      locals: SESSION,
      request: cuerpo({ level: 2, enabled: false, statement: "   ", extra: true }),
    });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api-gateway:8080/api/v1/config/intelligence/levels");
    expect(JSON.parse(opts.body)).toEqual({ level: 2, enabled: false });
  });

  it("el retiro no lleva cuerpo", async () => {
    const fetchMock = vi.fn(async () => new Response('{"purge_ref":"p-1"}', { status: 200 }));
    globalThis.fetch = fetchMock;
    await postRetiro({ locals: SESSION, request: cuerpo({ levels: [1] }) });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api-gateway:8080/api/v1/config/intelligence/withdraw");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBeUndefined();
  });
});
