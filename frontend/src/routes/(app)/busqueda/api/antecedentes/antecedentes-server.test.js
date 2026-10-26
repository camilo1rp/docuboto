// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * antecedentes-server.test.js — proxy SSR de antecedentes (D-05, Fase 6).
 *
 * Cubre: guard 401, XOR query/radicado_ref (allowlist + validación local antes
 * de tocar el gateway), top_k acotado, y relay opaco del 404 anti-oráculo.
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

describe("busqueda/api/antecedentes POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({ locals: {}, request: jsonRequest({ query: "contrato" }) });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rechaza cuando NO se envía ni query ni radicado_ref, sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({ locals: SESSION_LOCALS, request: jsonRequest({}) });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rechaza cuando se envían AMBOS query y radicado_ref (XOR), sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "convenio", radicado_ref: "2026-ICETEX-E-000001" }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("modo query: allowlist envía solo query/top_k al gateway", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ antecedentes: [], advisory: "adv" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "convenio", top_k: 999, source_type: "otro" }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/knowledge/antecedentes");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");
      const sentBody = JSON.parse(opts.body);
      expect(sentBody).toEqual({ query: "convenio", top_k: 20 });
      expect(sentBody.source_type).toBeUndefined();
      expect(sentBody.radicado_ref).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("modo radicado_ref: allowlist envía solo radicado_ref/top_k al gateway", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ antecedentes: [], advisory: "adv" }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ radicado_ref: "2026-ICETEX-E-000001" }),
      });
      const [, opts] = fetchMock.mock.calls[0];
      const sentBody = JSON.parse(opts.body);
      expect(sentBody).toEqual({ radicado_ref: "2026-ICETEX-E-000001", top_k: 5 });
      expect(sentBody.query).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("relaya el 404 anti-oráculo del backend sin enriquecerlo", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: "antecedente_source_not_found",
            detail: "No se encontró un antecedente indexado para esa referencia",
            status: 404,
          }),
          { status: 404 },
        ),
    );
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        request: jsonRequest({ radicado_ref: "2026-ICETEX-E-999999" }),
      });
      expect(res.status).toBe(404);
      const body = await res.json();
      expect(body.error).toBe("antecedente_source_not_found");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("distingue fallo de red de 'sin antecedentes' (503, no 200 vacío)", async () => {
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
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
