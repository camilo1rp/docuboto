// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * rag-proxies-server.test.js — proxy SSR de antecedentes en radicación
 * (D-05, ADR-011, E21 Inc.2).
 *
 * Cubre: guard 401, passthrough de errores del gateway, y resolución honesta de
 * `doc_class` vía `GET /documents/by-tracking/{tracking}` con `encodeURIComponent`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as antecedentesPost } from "./antecedentes/+server.js";

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

describe("radicar/api/rag/antecedentes POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await antecedentesPost({ locals: {}, request: jsonRequest({ query: "algo" }) });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("propaga el error del gateway sin resolver doc_class si /antecedentes falla", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async (calledUrl) => {
      expect(calledUrl).toBe("http://app:8000/api/v1/knowledge/antecedentes");
      return new Response(
        JSON.stringify({
          error: "antecedente_source_not_found",
          detail: "no encontrado",
          status: 404,
        }),
        { status: 404 },
      );
    });
    globalThis.fetch = fetchMock;
    try {
      const res = await antecedentesPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "asunto de prueba" }),
      });
      expect(res.status).toBe(404);
      expect(fetchMock).toHaveBeenCalledTimes(1); // no intenta by-tracking
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("resuelve doc_class por precedente vía by-tracking (encodeURIComponent) y agrega sugerencias", async () => {
    const originalFetch = globalThis.fetch;
    const trickyRef = "2024/ICETEX-E-001234 ../etc";
    const fetchMock = vi.fn(async (calledUrl) => {
      if (calledUrl === "http://app:8000/api/v1/knowledge/antecedentes") {
        return new Response(
          JSON.stringify({
            antecedentes: [
              { source_type: "radicado", source_ref: trickyRef, snippet: "s1", score: 0.9 },
              {
                source_type: "radicado",
                source_ref: "2024-ICETEX-E-000002",
                snippet: "s2",
                score: 0.8,
              },
            ],
            advisory: "aviso",
          }),
          { status: 200 },
        );
      }
      // Debe llegar SIEMPRE escapado — nunca la referencia cruda.
      expect(calledUrl).not.toContain(trickyRef);
      if (
        calledUrl ===
        `http://app:8000/api/v1/documents/by-tracking/${encodeURIComponent(trickyRef)}`
      ) {
        return new Response(JSON.stringify({ doc_class: "CDP" }), { status: 200 });
      }
      if (calledUrl === "http://app:8000/api/v1/documents/by-tracking/2024-ICETEX-E-000002") {
        return new Response(JSON.stringify({ doc_class: "CDP" }), { status: 200 });
      }
      throw new Error(`URL inesperada: ${calledUrl}`);
    });
    globalThis.fetch = fetchMock;
    try {
      const res = await antecedentesPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "asunto de prueba" }),
      });
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.suggestions).toEqual([
        { doc_class: "CDP", count: 2, best_score: 0.9, example_ref: trickyRef },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un precedente que no resuelve (404 no-read-up) no rompe la agregación", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(async (calledUrl) => {
      if (calledUrl === "http://app:8000/api/v1/knowledge/antecedentes") {
        return new Response(
          JSON.stringify({
            antecedentes: [
              {
                source_type: "radicado",
                source_ref: "2024-ICETEX-E-000009",
                snippet: "s",
                score: 0.5,
              },
            ],
            advisory: "aviso",
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ error: "document_not_found" }), { status: 404 });
    });
    globalThis.fetch = fetchMock;
    try {
      const res = await antecedentesPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "asunto de prueba" }),
      });
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.suggestions).toEqual([]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
