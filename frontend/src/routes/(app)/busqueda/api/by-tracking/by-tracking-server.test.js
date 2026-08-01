// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * by-tracking-server.test.js — proxy SSR de resolución de radicado por número
 * (D-05, Fase 6). Contra document-service (NO knowledge-service).
 *
 * Cubre: guard 401, `encodeURIComponent` del tracking_number en la URL del
 * gateway, allowlist de SALIDA (no reenvía el documento completo), y relay
 * opaco del 404 no-read-up.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./[trackingNumber]/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

function forbidFetch() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse");
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("busqueda/api/by-tracking/[trackingNumber] GET", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await GET({ locals: {}, params: { trackingNumber: "2026-ICETEX-E-000001" } });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("escapa el tracking_number en la URL del gateway (encodeURIComponent)", async () => {
    const originalFetch = globalThis.fetch;
    const trackingConSlash = "2026/ICETEX-E-000001";
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            id: "1",
            tracking_number: trackingConSlash,
            doc_type: "E",
            subject: "Asunto",
            status: "registered",
            registered_at: "2026-01-01T00:00:00Z",
            metadata: { secreto: "no debería salir" },
            anexos: [{ id: "x" }],
          }),
          { status: 200 },
        ),
    );
    globalThis.fetch = fetchMock;
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { trackingNumber: trackingConSlash },
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe(
        `http://api-gateway:8080/api/v1/documents/by-tracking/${encodeURIComponent(trackingConSlash)}`,
      );
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");

      const body = await res.json();
      expect(body.tracking_number).toBe(trackingConSlash);
      // Allowlist de salida: no reenvía metadata/anexos/observations.
      expect(body.metadata).toBeUndefined();
      expect(body.anexos).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 (no-read-up: no existe o clearance insuficiente) se relaya opaco", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "document_not_found" }), { status: 404 }),
    );
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { trackingNumber: "2026-ICETEX-E-000002" },
      });
      expect(res.status).toBe(404);
      const body = await res.json();
      expect(body.error).toBe("not_found");
      expect(body.detail).not.toContain("2026-ICETEX-E-000002");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("falta el parámetro de ruta: 422 sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { trackingNumber: "" } });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("distingue fallo de red de 'no encontrado' (503, no 404)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { trackingNumber: "2026-ICETEX-E-000001" },
      });
      expect(res.status).toBe(503);
      const body = await res.json();
      expect(body.error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
