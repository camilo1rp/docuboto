// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * ADR-032: devolver, bandeja y pendientes hablan de dependencia por CÓDIGO.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

import { POST as devolverPost } from "./[id]/devolver/+server.js";
import { GET as inboxGet } from "./inbox/+server.js";
import { GET as pendingGet } from "./pending/+server.js";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "u-1" } };
const ID = "11111111-1111-1111-1111-111111111111";
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("devolver POST", () => {
  it("reenvía to_dept_code entero y no el nombre", async () => {
    let body;
    globalThis.fetch = vi.fn(async (_u, init) => {
      body = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "e1" }), { status: 201 });
    });
    const res = await devolverPost({
      locals: LOCALS,
      params: { id: ID },
      request: { json: async () => ({ to_dept_code: "7", to_dept: "Jurídica", causal: "ERR" }) },
    });
    expect(res.status).toBe(201);
    expect(body.to_dept_code).toBe(7);
    expect(body.to_dept).toBeUndefined();
  });
  it("sin código válido: 422 y no llama al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamarse");
    });
    const res = await devolverPost({
      locals: LOCALS,
      params: { id: ID },
      request: { json: async () => ({ to_dept: "Jurídica", causal: "ERR" }) },
    });
    expect(res.status).toBe(422);
  });
});

describe.each([
  ["inbox", inboxGet],
  ["pending", pendingGet],
])("%s GET", (_n, handler) => {
  it("filtra por dept_code y descarta el nombre libre", async () => {
    let u;
    globalThis.fetch = vi.fn(async (input) => {
      u = String(input);
      return new Response("[]", { status: 200, headers: { "X-Total-Count": "0" } });
    });
    await handler({
      locals: LOCALS,
      url: new URL("http://x/y?dept_code=7&dept=Jur%C3%ADdica"),
    });
    expect(u).toContain("dept_code=7");
    expect(u).not.toContain("dept=");
    await handler({ locals: LOCALS, url: new URL("http://x/y?dept_code=Jur%C3%ADdica") });
    expect(u).not.toContain("dept_code");
  });
});
