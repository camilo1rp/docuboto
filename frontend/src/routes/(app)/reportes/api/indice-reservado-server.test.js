// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * indice-reservado-server.test.js — proxies SSR del índice de información
 * clasificada y reservada (E08, Ley 1712/2014 art. 20, D-05, Fase 4 cierre
 * desfase API↔UI).
 *
 * Cubre: guard 401 en ambos endpoints, headers propagados (Authorization,
 * X-Tenant-Slug, X-User-Id), y pass-through del blob CSV con su
 * Content-Disposition.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as indiceGet } from "./indice-reservado/+server.js";
import { GET as indiceCsvGet } from "./indice-reservado/csv/+server.js";

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

describe("reportes/api/indice-reservado GET", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await indiceGet({ locals: {} });
      expect(res.status).toBe(401);
      expect((await res.json()).error).toBe("unauthenticated");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("con sesión: pide GET /reports/indice-reservado con Authorization/X-Tenant-Slug/X-User-Id", async () => {
    const originalFetch = globalThis.fetch;
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            total: 1,
            items: [
              {
                numero_radicado: "2026-ICETEX-E-000001",
                tipo_radicado: "E",
                nivel_seguridad: 2,
                clasificacion: "reservada",
                excepcion_ley_1712: "Ley 1712/2014 art. 19",
                fundamento_juridico: "Sujeto a investigación",
                causal_reserva: "Investigación en curso",
                plazo_reserva_meses: 12,
                fecha_clasificacion: "2026-01-01T00:00:00Z",
                fecha_radicacion: "2026-01-01T00:00:00Z",
              },
            ],
          }),
          { status: 200 },
        ),
    );
    globalThis.fetch = fetchMock;
    try {
      const res = await indiceGet({ locals: SESSION_LOCALS });
      expect(res.status).toBe(200);
      const [calledUrl, opts] = fetchMock.mock.calls[0];
      expect(calledUrl).toBe("http://api-gateway:8080/api/v1/reports/indice-reservado");
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("33333333-3333-3333-3333-333333333333");
      const body = await res.json();
      expect(body.total).toBe(1);
      // Content-free por diseño: el proxy no debe inventar ni exponer un
      // campo de asunto/contenido que el backend no envía.
      expect(body.items[0].subject).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("403 del backend (sin PERM_RECLASIFICAR): se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", detail: "sin permiso" }), {
          status: 403,
        }),
    );
    try {
      const res = await indiceGet({ locals: SESSION_LOCALS });
      expect(res.status).toBe(403);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("reportes/api/indice-reservado/csv GET", () => {
  it("sin token: 401 unauthenticated, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await indiceCsvGet({ locals: {} });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("hace pass-through del blob CSV preservando Content-Disposition del gateway", async () => {
    const originalFetch = globalThis.fetch;
    const csvBody = "numero_radicado,tipo_radicado\r\n2026-ICETEX-E-000001,E\r\n";
    globalThis.fetch = vi.fn(
      async () =>
        new Response(csvBody, {
          status: 200,
          headers: {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=indice-reservado.csv",
          },
        }),
    );
    try {
      const res = await indiceCsvGet({ locals: SESSION_LOCALS });
      expect(res.status).toBe(200);
      expect(res.headers.get("Content-Disposition")).toBe(
        "attachment; filename=indice-reservado.csv",
      );
      const text = await res.text();
      expect(text).toBe(csvBody);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("error del backend: reenvía el cuerpo de error, no un CSV vacío disfrazado de éxito", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "forbidden" }), { status: 403 }),
    );
    try {
      const res = await indiceCsvGet({ locals: SESSION_LOCALS });
      expect(res.status).toBe(403);
      const body = await res.json();
      expect(body.error).toBe("forbidden");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
