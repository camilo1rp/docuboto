// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * publicacion-server.test.js — proxy SSR del acto de publicación de oficio
 * (Ley 1712/2014 art. 12, D-05).
 *
 * Cubre: guard 401, id escapado, opacidad del 404 no-read-up, allowlist de los
 * dos únicos campos del acto, fundamento obligatorio en AMBOS sentidos, y el
 * recorte de la respuesta del detalle (el drawer no recibe el radicado entero).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET, POST } from "./+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchOnce(body, status = 200) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

function forbidFetch(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

function jsonRequest(body) {
  return { json: async () => body };
}

describe("bandeja/api/[id]/publicacion GET", () => {
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

  it("escapa el id e inyecta la identidad desde locals, nunca del cliente", async () => {
    const originalFetch = globalThis.fetch;
    const idTorcido = "abc def/../etc";
    const fetchMock = mockFetchOnce({ tracking_number: "T", publicable: false });
    globalThis.fetch = fetchMock;
    try {
      await GET({ locals: SESSION_LOCALS, params: { id: idTorcido } });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe(
        `http://api-gateway:8080/api/v1/documents/${encodeURIComponent(idTorcido)}`,
      );
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("recorta la respuesta: el drawer no recibe asunto ni anexos del radicado", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce({
      tracking_number: "2026-ICETEX-E-001234",
      nivel_seguridad: 1,
      status: "registered",
      publicable: true,
      publicado_at: "2026-08-12T10:30:00Z",
      subject: "Asunto que no debe salir de aquí",
      anexos: [{ filename: "carta.pdf" }],
      observations: "Notas internas",
    });
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      const body = await res.json();
      expect(body).toEqual({
        tracking_number: "2026-ICETEX-E-001234",
        nivel_seguridad: 1,
        status: "registered",
        publicable: true,
        publicado_at: "2026-08-12T10:30:00Z",
      });
      expect(body.subject).toBeUndefined();
      expect(body.anexos).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 del backend: 404 opaco propio, sin distinguir inexistente de sin acceso", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce(
      { detail: { error: "document_not_found", detail: "id concreto filtrable" } },
      404,
    );
    try {
      const res = await GET({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(404);
      const body = await res.json();
      expect(body.error).toBe("not_found");
      expect(JSON.stringify(body)).not.toContain("id concreto filtrable");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/publicacion POST", () => {
  it("sin token: 401, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({
        locals: {},
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: true, fundamento: "x" }),
      });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("sin fundamento: 422 antes de tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch("no debe llamarse sin fundamento");
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: true, fundamento: "   " }),
      });
      expect(res.status).toBe(422);
      expect((await res.json()).detail).toMatch(/fundamento/i);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("el fundamento también es obligatorio al RETIRAR: retirar hay que poder explicarlo", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch("no debe llamarse sin fundamento");
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: false, fundamento: "" }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("sin `publicar` booleano: 422 — el sentido del acto no se adivina", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ fundamento: "Numeral 4.2" }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo viajan `publicar` y `fundamento`, aunque el cliente mande más", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = mockFetchOnce({ tracking_number: "T", publicable: true });
    globalThis.fetch = fetchMock;
    try {
      await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({
          publicar: true,
          fundamento: "  Numeral 4.2 del esquema  ",
          nivel_seguridad: 1,
          publicado_por: "44444444-4444-4444-4444-444444444444",
          tenant_slug: "otra-entidad",
        }),
      });
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe(`http://api-gateway:8080/api/v1/documents/${RAD_ID}/publicacion`);
      expect(JSON.parse(opts.body)).toEqual({
        publicar: true,
        fundamento: "Numeral 4.2 del esquema",
      });
      // La entidad y el autor los pone el servidor desde la sesión, nunca el body.
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("fundamento por encima del máximo: 422 sin tocar el gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: true, fundamento: "x".repeat(501) }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 del backend (reservado, anulado o inexistente): 404 opaco, sin decir cuál", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce({ detail: { error: "document_not_found" } }, 404);
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: true, fundamento: "Numeral 4.2" }),
      });
      expect(res.status).toBe(404);
      expect((await res.json()).error).toBe("not_found");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("caída del gateway: 503 explícito, nunca un éxito inventado", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: jsonRequest({ publicar: true, fundamento: "Numeral 4.2" }),
      });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("connection_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
