// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * rag-suggest-server.test.js — proxy SSR de ayuda con RAG para borradores (D-05,
 * ADR-011, E21 Inc.3, F6 frontend). Gemelo del de `/radicar/api/rag/suggest`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST } from "./+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

function jsonRequest(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function forbidFetch(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

describe("borradores/api/rag/suggest POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({ locals: {}, request: jsonRequest({ query: "algo" }) });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("cuerpo inválido: 400 invalid_body, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: {
          json: async () => {
            throw new Error("bad json");
          },
        },
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist campo a campo hacia /api/v1/knowledge/rag", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async (calledUrl, opts) => {
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/knowledge/rag");
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ query: "RE: contrato 123", top_k: 3 });
      return new Response(
        JSON.stringify({
          answer: null,
          citations: [],
          proveedor: "disabled",
          modelo: null,
          confianza: 0,
          suggestion_id: null,
          advisory: "aviso",
        }),
        { status: 200 },
      );
    });
    globalThis.fetch = fetchMock;
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "RE: contrato 123", top_k: 3, answer: "forjado" }),
      });
      expect(res.status).toBe(200);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("caída del servicio: 503 connection_error — el borrador sigue guardable (proxy separado)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "asunto suficientemente largo" }),
      });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
