// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El proxy de vencimiento reenvía fecha o días y el motivo. No reenvía `origen`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST } from "./+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";
const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonRequest(body) {
  return { json: async () => body };
}

describe("bandeja/api/[id]/due-date", () => {
  it("sin token responde 401 y no llama al gateway", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await POST({
        locals: {},
        params: { id: RAD_ID },
        request: jsonRequest({}),
      });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("con fecha y días no llama al gateway", async () => {
    const original = globalThis.fetch;
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ due_date: "2026-03-01" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      const res = await POST({
        locals: LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({
          due_date: "2026-03-01",
          motivo: "El término inicial estaba mal contado.",
          origen: "sugerencia",
          business_days: 30,
        }),
      });
      expect(res.status).toBe(422);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = original;
    }
  });

  it("con solo la fecha hace PUT y no manda origen", async () => {
    const original = globalThis.fetch;
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ due_date: "2026-03-01" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await POST({
        locals: LOCALS,
        params: { id: "abc def" },
        request: jsonRequest({
          due_date: "2026-03-01",
          motivo: "El término inicial estaba mal contado.",
          origen: "sugerencia",
        }),
      });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toContain(`/documents/${encodeURIComponent("abc def")}/due-date`);
      expect(opts.method).toBe("PUT");
      const cuerpo = JSON.parse(opts.body);
      expect(cuerpo).toEqual({
        due_date: "2026-03-01",
        motivo: "El término inicial estaba mal contado.",
      });
      expect(opts.headers["X-User-Id"]).toBe(LOCALS.user.sub);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("un 404 del backend sigue siendo opaco", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ detail: "secreto" }), { status: 404 }),
    );
    try {
      const res = await POST({
        locals: LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({
          business_days: 10,
          motivo: "Faltaba el plazo de la entrada.",
        }),
      });
      expect(res.status).toBe(404);
      const data = await res.json();
      expect(data.detail).not.toContain("secreto");
    } finally {
      globalThis.fetch = original;
    }
  });
});
