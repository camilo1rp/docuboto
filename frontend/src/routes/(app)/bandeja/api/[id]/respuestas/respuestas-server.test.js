// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * respuestas-server.test.js — proxy SSR de lectura de respuestas vinculadas
 * (D-05, ADR-011, F6 frontend). Alimenta la sección "Respuestas" del drawer de
 * detalle en bandeja/+page.svelte.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function forbidFetch(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

describe("bandeja/api/[id]/respuestas GET", () => {
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

  it("escapa el id con encodeURIComponent e inyecta Authorization/X-Tenant-Slug/X-User-Id desde locals", async () => {
    const originalFetch = globalThis.fetch;
    const idConRuta = "abc def/../etc";
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    globalThis.fetch = fetchMock;
    try {
      await GET({ locals: SESSION_LOCALS, params: { id: idConRuta } });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe(
        `http://api-gateway:8080/api/v1/documents/${encodeURIComponent(idConRuta)}/respuestas`,
      );
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("pasa el listado tal cual del gateway", async () => {
    const originalFetch = globalThis.fetch;
    const body = [
      {
        id: "44444444-4444-4444-4444-444444444444",
        tracking_number: "2026-ICETEX-S-000010",
        doc_type: "S",
        subject: "RE: asunto",
        status: "registered",
        registered_at: "2026-07-20T10:00:00Z",
      },
    ];
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual(body);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("caída del gateway: 503 connection_error, no lanza", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
