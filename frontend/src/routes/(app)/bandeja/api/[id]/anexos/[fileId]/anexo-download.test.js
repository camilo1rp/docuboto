// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const RAD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const FILE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const SESSION = {
  accessToken: "tok-abc",
  user: { tenant_slug: "demo", sub: "33333333-3333-4333-8333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("bandeja anexo download", () => {
  it("sin token no llama al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await GET({ locals: {}, params: { id: RAD, fileId: FILE } });
    expect(res.status).toBe(401);
  });

  it("un fileId que no es UUID no sale", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debía llamarse");
    });
    const res = await GET({ locals: SESSION, params: { id: RAD, fileId: "../otro" } });
    expect(res.status).toBe(400);
  });

  it("pide la descarga mediada y conserva el 404 neutro", async () => {
    const fetchMock = vi.fn(async () => new Response("no", { status: 404 }));
    globalThis.fetch = fetchMock;
    const res = await GET({ locals: SESSION, params: { id: RAD, fileId: FILE } });
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("not_found");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe(`http://api-gateway:8080/api/v1/documents/${RAD}/anexos/${FILE}/download`);
    expect(opts.headers["X-User-Id"]).toBe(SESSION.user.sub);
  });
});
