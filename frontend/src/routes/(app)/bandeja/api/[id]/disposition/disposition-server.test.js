// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * disposition-server.test.js — proxy SSR de disposición final del radicado
 * (RF-MET-08, D-05, Fase 4 cierre desfase API↔UI).
 *
 * Cubre: guard 401, allowlist campo a campo (anti mass-assignment), id
 * escapado con encodeURIComponent, opacidad del 404 no-read-up, y validación
 * de `accion`/fecha antes de tocar el gateway.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET, PATCH } from "./+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchOnce(body, status = 200, headers = {}) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers }));
}

function forbidFetch(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

function jsonRequest(body) {
  return { json: async () => body };
}

describe("bandeja/api/[id]/disposition GET", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await GET({ locals: {}, params: { id: RAD_ID } });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("escapa el id en la URL del gateway e inyecta Authorization/X-Tenant-Slug/X-User-Id", async () => {
    const originalFetch = globalThis.fetch;
    const idConEspacio = "abc def/../etc";
    const fetchMock = mockFetchOnce({ radicado_id: RAD_ID, disposition: {} });
    globalThis.fetch = fetchMock;
    try {
      await GET({ locals: SESSION_LOCALS, params: { id: idConEspacio } });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe(
        `http://api-gateway:8080/api/v1/documents/${encodeURIComponent(idConEspacio)}/disposition`,
      );
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 del backend (no-read-up): responde 404 opaco propio, no reenvía el detalle crudo", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce(
      { error: "document_not_found", detail: RAD_ID, status: 404 },
      404,
    );
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(404);
      const body = await res.json();
      expect(body.error).toBe("not_found");
      // No debe filtrar si el fallo es "no existe" vs "clearance insuficiente".
      expect(body.detail).not.toContain(RAD_ID);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/disposition PATCH", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await PATCH({
        locals: {},
        params: { id: RAD_ID },
        request: jsonRequest({ accion: "conservar" }),
      });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: descarta campos fuera del contrato (anti mass-assignment)", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = mockFetchOnce({ radicado_id: RAD_ID, disposition: { accion: "conservar" } });
    globalThis.fetch = fetchMock;
    try {
      await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({
          accion: "conservar",
          nivel_seguridad: 3, // campo AJENO al contrato de disposición — no debe pasar
          registered_by: "otro-usuario", // intento de mass-assignment de identidad
        }),
      });
      const [, opts] = fetchMock.mock.calls[0];
      const sentBody = JSON.parse(opts.body);
      expect(sentBody).toEqual({ accion: "conservar" });
      expect(sentBody.nivel_seguridad).toBeUndefined();
      expect(sentBody.registered_by).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rechaza una acción fuera del catálogo sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ accion: "borrar-todo" }),
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rechaza una fecha con formato inválido sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ retention_until: "31-12-2030" }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("body vacío (sin cambios): 422 sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({}),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 del backend: responde 404 opaco propio", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce({ error: "document_not_found" }, 404);
    try {
      const res = await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ accion: "eliminar" }),
      });
      expect(res.status).toBe(404);
      expect((await res.json()).error).toBe("not_found");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("payload válido completo: reenvía exactamente los 5 campos del contrato", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = mockFetchOnce({ radicado_id: RAD_ID, disposition: {} });
    globalThis.fetch = fetchMock;
    try {
      await PATCH({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({
          programa: "  Serie Correspondencia  ",
          retention_until: "2030-12-31",
          accion: "transferir",
          confirmado: true,
          marcado_eliminacion: false,
        }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(opts.method).toBe("PATCH");
      expect(calledUrl).toBe(`http://api-gateway:8080/api/v1/documents/${RAD_ID}/disposition`);
      expect(JSON.parse(opts.body)).toEqual({
        programa: "Serie Correspondencia",
        retention_until: "2030-12-31",
        accion: "transferir",
        confirmado: true,
        marcado_eliminacion: false,
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
