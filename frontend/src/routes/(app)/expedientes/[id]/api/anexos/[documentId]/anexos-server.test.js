// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anexos-server.test.js — proxy BFF de listado de anexos por radicado,
 * mediado por document-service (D-05, D-02, RF-SEG-08, E07).
 *
 * Cubre lo que bffProxyGuards.test.js NO cubre (ese archivo solo prueba el
 * guard 401 genérico):
 *   - el access_token nunca llega al cliente: se lee de locals.accessToken
 *     y se inyecta como Authorization al gateway (nunca del cliente/query).
 *   - X-Tenant-Slug / X-User-Id se inyectan desde locals, nunca del body/query
 *     del cliente (D-05); sin X-User-Id, document-service degradaría el
 *     clearance a PUBLICA (fail-closed) — omitirlo ocultaría los anexos de
 *     radicados reservados/clasificados a actores autorizados.
 *   - allowlist UUID de documentId (400 antes de tocar el gateway).
 *   - 404 neutro (no-read-up, RF-SEG-08): el proxy no reconstruye la causal.
 *   - [] (200): radicado visible sin anexos — distinto del 404.
 *   - 503 ante fallo de red.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const DOC_ID = "11111111-1111-1111-1111-111111111111";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("expedientes/[id]/api/anexos/[documentId] GET — guard de sesión (D-05)", () => {
  it("sin locals.accessToken: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    try {
      const res = await GET({ locals: {}, params: { id: "exp-1", documentId: DOC_ID } });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("accessToken vacío también es tratado como sin sesión", async () => {
    const res = await GET({
      locals: { accessToken: "" },
      params: { id: "exp-1", documentId: DOC_ID },
    });
    expect(res.status).toBe(401);
  });
});

describe("expedientes/[id]/api/anexos/[documentId] GET — allowlist UUID", () => {
  it.each([
    ["no-es-un-uuid"],
    ["11111111-1111-1111-1111-11111111111"], // 11 chars en el último grupo
    ["'; DROP TABLE documents; --"],
    ["../../etc/passwd"],
    [""],
  ])("documentId=%s inválido: 400 validation_error, sin llamar al gateway", async (bad) => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con documentId inválido");
    });
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: bad },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un UUID en mayúsculas también es válido (regex case-insensitive)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID.toUpperCase() },
      });
      expect(res.status).toBe(200);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("expedientes/[id]/api/anexos/[documentId] GET — headers hacia el gateway", () => {
  it("reenvía Authorization + X-Tenant-Slug + X-User-Id desde locals (nunca del cliente)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    try {
      await GET({ locals: SESSION_LOCALS, params: { id: "exp-1", documentId: DOC_ID } });
      expect(capturedUrl).toContain(`/documents/${DOC_ID}/anexos`);
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("sin locals.user.sub, no envía X-User-Id (no inventa un actor)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init.headers;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    try {
      await GET({
        locals: { accessToken: "tok-abc", user: { tenant_slug: "icetex" } },
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(capturedHeaders["X-User-Id"]).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("expedientes/[id]/api/anexos/[documentId] GET — respuestas", () => {
  it("200 con lista de anexos: pasa el body tal cual", async () => {
    const originalFetch = globalThis.fetch;
    const anexos = [
      {
        id: "a1",
        file_id: "22222222-2222-2222-2222-222222222222",
        filename: "oficio.pdf",
        file_size: 1024,
        mime_type: "application/pdf",
        created_at: "2026-07-01T10:00:00Z",
        tipo_anexo: "principal",
        es_principal: true,
        folios: 3,
      },
    ];
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(anexos), { status: 200 }));
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual(anexos);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("200 con [] : radicado visible sin anexos (distinto del 404)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual([]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 del gateway (no-read-up): se devuelve un 404 neutro, sin reconstruir la causal", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "document_not_found", detail: "..." }), {
          status: 404,
        }),
    );
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(res.status).toBe(404);
      const body = await res.json();
      expect(body.error).toBe("not_found");
      // No debe filtrar el código/causal específico del backend (no-read-up opaco).
      expect(body.detail).not.toMatch(/document_not_found/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("otro error upstream (p.ej. 500) se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "internal_error", detail: "boom" }), {
          status: 500,
        }),
    );
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(res.status).toBe(500);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("fallo de red del gateway: 503 connection_error", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await GET({
        locals: SESSION_LOCALS,
        params: { id: "exp-1", documentId: DOC_ID },
      });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
