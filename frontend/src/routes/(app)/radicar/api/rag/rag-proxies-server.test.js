// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * rag-proxies-server.test.js — proxies SSR de ayuda con RAG en radicación
 * (D-05, ADR-011, E21 Inc.2/3, F6 frontend).
 *
 * Cubre: guard 401, allowlist campo a campo (anti mass-assignment: `query`/`top_k`
 * son los únicos campos que viajan al gateway, cualquier otro se descarta), 422 si
 * la consulta es demasiado corta (espejo del `min_length` del backend), passthrough
 * de errores/degradación 503 (regla dura #3: el fallo del servicio de conocimiento
 * nunca debe romper la radicación), y — para `antecedentes` — resolución honesta de
 * `doc_class` vía `GET /documents/by-tracking/{tracking}` con `encodeURIComponent`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as suggestPost } from "./suggest/+server.js";
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

describe("radicar/api/rag/suggest POST", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await suggestPost({ locals: {}, request: jsonRequest({ query: "algo" }) });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("query demasiado corta: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await suggestPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({ query: "a" }),
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: descarta cualquier campo fuera de query/top_k y fija top_k por defecto", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            answer: "texto",
            citations: [],
            proveedor: "disabled",
            modelo: null,
            confianza: 0,
            suggestion_id: null,
            advisory: "aviso",
          }),
          { status: 200 },
        ),
    );
    globalThis.fetch = fetchMock;
    try {
      await suggestPost({
        locals: SESSION_LOCALS,
        request: jsonRequest({
          query: "asunto del radicado",
          top_k: 999, // fuera de rango — debe caer al default
          suggestion_id: "forjado", // no debe viajar
          confianza: 1.0, // no debe viajar
        }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/knowledge/rag");
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ query: "asunto del radicado", top_k: 5 });
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("caída del servicio de conocimiento: 503 connection_error, no lanza (degradación honesta)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await suggestPost({
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
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/knowledge/antecedentes");
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
      if (calledUrl === "http://api-gateway:8080/api/v1/knowledge/antecedentes") {
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
        `http://api-gateway:8080/api/v1/documents/by-tracking/${encodeURIComponent(trickyRef)}`
      ) {
        return new Response(JSON.stringify({ doc_class: "CDP" }), { status: 200 });
      }
      if (
        calledUrl === "http://api-gateway:8080/api/v1/documents/by-tracking/2024-ICETEX-E-000002"
      ) {
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
      if (calledUrl === "http://api-gateway:8080/api/v1/knowledge/antecedentes") {
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
