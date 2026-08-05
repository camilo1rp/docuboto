// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * metadata-templates-server.test.js — proxy BFF de plantillas de metadatos
 * de expediente (D-05, T-12 Form Builder).
 *
 * Cubre lo que bffProxyGuards.test.js NO cubre (ese archivo solo prueba el
 * guard 401 genérico): la allowlist estricta del ÚNICO query param
 * (trd_serie_id, forma UUID) y el passthrough de headers/status.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const url = (path) => new URL(`http://localhost${path}`);

describe("expedientes/api/metadata-templates GET — guard de sesión (D-05)", () => {
  it("sin locals.accessToken: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    try {
      const res = await GET({
        locals: {},
        url: url(
          "/expedientes/api/metadata-templates?trd_serie_id=11111111-1111-1111-1111-111111111111",
        ),
      });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("expedientes/api/metadata-templates GET — allowlist de trd_serie_id", () => {
  it("sin trd_serie_id: 400 invalid_param, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con param inválido");
    });
    try {
      const res = await GET({
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
        url: url("/expedientes/api/metadata-templates"),
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("invalid_param");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([
    ["no-es-un-uuid"],
    ["11111111-1111-1111-1111-11111111111"], // 11 chars en el último grupo
    ["'; DROP TABLE expedientes; --"],
    ["11111111111111111111111111111111"], // sin guiones
  ])("trd_serie_id=%s (no es UUID): 400 invalid_param, sin llamar al gateway", async (bad) => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con param inválido");
    });
    try {
      const res = await GET({
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
        url: url(`/expedientes/api/metadata-templates?trd_serie_id=${encodeURIComponent(bad)}`),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("trd_serie_id UUID válido: reenvía al gateway con Authorization + X-Tenant-Slug", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = input;
      capturedHeaders = init.headers;
      return { ok: true, status: 200, json: async () => [{ id: "tpl-1", activo: true }] };
    });
    try {
      const res = await GET({
        locals: { accessToken: "tok-abc", user: { tenant_slug: "icetex" } },
        url: url(
          "/expedientes/api/metadata-templates?trd_serie_id=11111111-1111-1111-1111-111111111111",
        ),
      });
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body).toEqual([{ id: "tpl-1", activo: true }]);
      expect(capturedUrl).toContain("trd_serie_id=11111111-1111-1111-1111-111111111111");
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un UUID en mayúsculas también es válido (regex case-insensitive)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => [] }));
    try {
      const res = await GET({
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
        url: url(
          "/expedientes/api/metadata-templates?trd_serie_id=AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
        ),
      });
      expect(res.status).toBe(200);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("otros query params fuera de la allowlist se ignoran (no se reenvían)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = input;
      return { ok: true, status: 200, json: async () => [] };
    });
    try {
      await GET({
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
        url: url(
          "/expedientes/api/metadata-templates?trd_serie_id=11111111-1111-1111-1111-111111111111&extra=inyeccion",
        ),
      });
      expect(capturedUrl).not.toContain("extra=");
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
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
        url: url(
          "/expedientes/api/metadata-templates?trd_serie_id=11111111-1111-1111-1111-111111111111",
        ),
      });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
