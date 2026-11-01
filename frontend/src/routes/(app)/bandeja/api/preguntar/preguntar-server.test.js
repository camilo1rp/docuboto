// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El proxy fija el alcance al radicado y no deja pasar campos de más.
 * El token sale de la cookie, no del cliente.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST } from "./+server.js";
import { GET } from "./transmision/+server.js";

const RAD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
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

describe("bandeja/api/preguntar", () => {
  it("sin token no llama al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await POST({ locals: {}, request: cuerpo({ query: "plazo", record_ref: RAD }) });
    expect(res.status).toBe(401);
  });

  it("un radicado que no es UUID no sale", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await POST({
      locals: SESSION,
      request: cuerpo({ query: "plazo de respuesta", record_ref: "../otro" }),
    });
    expect(res.status).toBe(422);
  });

  it("manda query, record_ref y top_k, y nada más", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ answer: null }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    await POST({
      locals: SESSION,
      request: cuerpo({
        query: "  plazo de respuesta  ",
        record_ref: RAD,
        top_k: 50,
        acl: { nivel: 1 },
      }),
    });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api-gateway:8080/api/v1/knowledge/rag");
    expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    expect(opts.headers["X-Tenant-Slug"]).toBe("demo");
    expect(opts.headers["X-User-Id"]).toBe(SESSION.user.sub);
    expect(JSON.parse(opts.body)).toEqual({
      query: "plazo de respuesta",
      record_ref: RAD,
      top_k: 5,
    });
  });
});

describe("bandeja/api/preguntar/transmision", () => {
  it("reenvía la lectura de la ruta sin cuerpo", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ transmits: false }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    const res = await GET({ locals: SESSION });
    expect(res.status).toBe(200);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api-gateway:8080/api/v1/knowledge/transmission");
    expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    expect(opts.body).toBeUndefined();
  });
});
