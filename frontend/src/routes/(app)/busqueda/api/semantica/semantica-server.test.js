// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * semantica-server.test.js — proxy SSR de búsqueda semántica (D-05, Fase 6).
 *
 * Cubre: guard 401, allowlist campo a campo (`acl` nunca sale del cliente),
 * top_k acotado, y normalización de `list[SearchHit]` a `{ results }`.
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

function forbidFetch() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse");
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("busqueda/api/semantica POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({ locals: {}, request: jsonRequest({ query: "contrato" }) });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rechaza query vacío sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({ locals: SESSION_LOCALS, request: jsonRequest({ query: "  " }) });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: `acl` del cliente nunca llega al gateway; top_k se acota a 50", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    globalThis.fetch = fetchMock;
    try {
      await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({
          query: "renovación de convenio",
          top_k: 9999,
          acl: { nivel_seguridad: 1 }, // intento de fijar su propio filtro ACL
        }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/knowledge/search");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");
      const sentBody = JSON.parse(opts.body);
      expect(sentBody).toEqual({ query: "renovación de convenio", top_k: 50 });
      expect(sentBody.acl).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("normaliza list[SearchHit] a { results }", async () => {
    const originalFetch = globalThis.fetch;
    const hits = [
      {
        id: "11111111-1111-1111-1111-111111111111",
        source_type: "radicado",
        source_ref: "2026-ICETEX-E-000001",
        texto: "Solicitud de renovación de convenio",
        metadata: {},
        score: 0.82,
      },
    ];
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(hits), { status: 200 }));
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "convenio" }),
      });
      const body = await res.json();
      expect(res.status).toBe(200);
      expect(body.results).toHaveLength(1);
      expect(body.results[0].score).toBe(0.82);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("distingue fallo de red de 'sin resultados' (503, no [])", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "convenio" }),
      });
      expect(res.status).toBe(503);
      const body = await res.json();
      expect(body.error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("propaga el status de error del gateway sin encubrirlo como 200 vacío", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "server_error" }), { status: 500 }),
    );
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "convenio" }),
      });
      expect(res.status).toBe(500);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
