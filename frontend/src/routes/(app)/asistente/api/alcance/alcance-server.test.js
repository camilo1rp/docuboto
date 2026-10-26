// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * alcance-server.test.js — proxy SSR del marco de alcance (D-05).
 *
 * El punto que se fija: un fallo al consultar el marco NO puede devolver una
 * lista vacía. Un panel de capacidades en blanco por un error de red es
 * indistinguible de un usuario sin permisos, y esa confusión manda a la persona
 * a pedirle al administrador algo que ya tiene.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("asistente/api/alcance", () => {
  it("sin token: 401, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamarse");
    });
    try {
      const res = await GET({ locals: {} });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("inyecta la identidad desde locals, nunca del cliente", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ puede_hacer: [] }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    try {
      await GET({ locals: SESSION_LOCALS });
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe("http://api-gateway:8080/api/v1/assistant/alcance");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("devuelve el marco tal cual cuando el backend responde", async () => {
    const originalFetch = globalThis.fetch;
    const marco = {
      puede_hacer: [{ operacion: "Radicar un documento", herramienta: "radicar_documento" }],
      no_puede: [{ operacion: "Enviar", via_alternativa: "Trasladar a correspondencia" }],
    };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(marco), { status: 200 }));
    try {
      const res = await GET({ locals: SESSION_LOCALS });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual(marco);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un fallo del backend se dice, NO se devuelve un marco vacío", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response("", { status: 503 }));
    try {
      const res = await GET({ locals: SESSION_LOCALS });
      expect(res.status).toBe(503);
      const body = await res.json();
      expect(body.error).toBe("alcance_unavailable");
      expect(body.puede_hacer).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("una caída de red tampoco se disfraza de 'no puedes nada'", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    try {
      const res = await GET({ locals: SESSION_LOCALS });
      expect(res.status).toBe(503);
      expect((await res.json()).error).toBe("alcance_unavailable");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
