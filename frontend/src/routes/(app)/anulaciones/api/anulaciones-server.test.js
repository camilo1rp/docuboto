// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anulaciones-server.test.js — proxies BFF de la cola de anulaciones (D-05,
 * ADR-011, Fase 0 cierre desfase API↔UI).
 *
 * Cubre lo que `bffProxyGuards.test.js` NO cubre (ese archivo solo prueba el
 * guard 401 genérico):
 *   - allowlist de id (formato UUID) antes de tocar el gateway.
 *   - `encodeURIComponent` del id en la URL hacia el gateway.
 *   - `aprobar`/`rechazar`: motivo OBLIGATORIO (allowlist anti mass-assignment:
 *     solo se reenvía `observacion`, nunca el body íntegro del cliente).
 *   - `buscar`: mapea `data.hits` (no `data.items`/`data.results` — esa es la
 *     forma real de `GET /search`, distinta del proxy hermano `/busqueda/api`).
 *   - Authorization/X-Tenant-Slug/X-User-Id se inyectan desde locals, nunca
 *     del cliente.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as estadoGet } from "./[id]/+server.js";
import { POST as aprobarPost } from "./[id]/aprobar/+server.js";
import { POST as rechazarPost } from "./[id]/rechazar/+server.js";
import { GET as buscarGet } from "./buscar/+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const url = (path) => new URL(`http://localhost${path}`);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("anulaciones/api/[id] GET — allowlist UUID", () => {
  it.each([["no-es-un-uuid"], ["'; DROP TABLE radicados; --"], ["../../etc/passwd"], [""]])(
    "id=%s inválido: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con id inválido");
      });
      try {
        const res = await estadoGet({ locals: SESSION_LOCALS, params: { id: bad } });
        expect(res.status).toBe(400);
        expect((await res.json()).error).toBe("validation_error");
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it("id válido: reenvía Authorization/X-Tenant-Slug/X-User-Id y encodeURIComponent en la URL", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify({ id: "a-1", estado: "solicitada" }), { status: 200 });
    });
    try {
      const res = await estadoGet({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/documents/${encodeURIComponent(RAD_ID)}/anulacion`);
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 upstream (sin anulación / sobre-clearance) se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "not_found", detail: "..." }), { status: 404 }),
    );
    try {
      const res = await estadoGet({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(404);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

function decisionSuite(name, handler) {
  describe(`anulaciones/api/[id]/${name} POST`, () => {
    it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con id inválido");
      });
      try {
        const res = await handler({
          locals: SESSION_LOCALS,
          params: { id: "no-es-un-uuid" },
          request: { json: async () => ({ motivo: "porque sí" }) },
        });
        expect(res.status).toBe(400);
        expect((await res.json()).error).toBe("validation_error");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });

    it.each([[undefined], [""], ["   "], [null]])(
      "motivo=%j ausente/vacío: 422 validation_error, sin llamar al gateway (motivo obligatorio)",
      async (motivo) => {
        const originalFetch = globalThis.fetch;
        globalThis.fetch = vi.fn(() => {
          throw new Error("fetch no debió invocarse sin motivo");
        });
        try {
          const res = await handler({
            locals: SESSION_LOCALS,
            params: { id: RAD_ID },
            request: { json: async () => ({ motivo }) },
          });
          expect(res.status).toBe(422);
          expect((await res.json()).error).toBe("validation_error");
        } finally {
          globalThis.fetch = originalFetch;
        }
      },
    );

    it("motivo > 2000 caracteres: 422 validation_error, sin llamar al gateway (M9: se rechaza, no se recorta)", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con motivo demasiado largo");
      });
      try {
        const res = await handler({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: { json: async () => ({ motivo: "a".repeat(2001) }) },
        });
        expect(res.status).toBe(422);
        expect((await res.json()).error).toBe("validation_error");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });

    it("motivo de exactamente 2000 caracteres: se acepta (no dispara el 422 del límite)", async () => {
      const originalFetch = globalThis.fetch;
      let capturedBody;
      globalThis.fetch = vi.fn(async (input, init) => {
        capturedBody = JSON.parse(init.body);
        return new Response(JSON.stringify({ id: "a-1", estado: "aprobada" }), { status: 200 });
      });
      try {
        const res = await handler({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: { json: async () => ({ motivo: "a".repeat(2000) }) },
        });
        expect(res.status).toBe(200);
        expect(capturedBody.observacion).toHaveLength(2000);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });

    it("allowlist: solo se reenvía `observacion` derivado de `motivo` — nunca el body íntegro (anti mass-assignment)", async () => {
      const originalFetch = globalThis.fetch;
      let capturedUrl;
      let capturedBody;
      let capturedHeaders;
      globalThis.fetch = vi.fn(async (input, init) => {
        capturedUrl = String(input);
        capturedBody = JSON.parse(init.body);
        capturedHeaders = init.headers;
        return new Response(JSON.stringify({ id: "a-1", estado: "aprobada" }), { status: 200 });
      });
      try {
        const res = await handler({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: {
            json: async () => ({
              motivo: "  Motivo legítimo  ",
              // Campos ajenos que un cliente malicioso podría intentar colar:
              decidido_por: "otro-usuario-id",
              estado: "forzado",
              radicado_id: "otro-radicado",
            }),
          },
        });
        expect(res.status).toBe(200);
        expect(capturedUrl).toContain(`/documents/${encodeURIComponent(RAD_ID)}/anulacion/${name}`);
        expect(capturedBody).toEqual({ observacion: "Motivo legítimo" });
        expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
        expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
        expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
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
        const res = await handler({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: { json: async () => ({ motivo: "motivo válido" }) },
        });
        expect(res.status).toBe(503);
        expect((await res.json()).error).toBe("connection_error");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
}

decisionSuite("aprobar", aprobarPost);
decisionSuite("rechazar", rechazarPost);

describe("anulaciones/api/buscar GET", () => {
  it("sin `q` (o menor a 2 caracteres): 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin q válido");
    });
    try {
      const res = await buscarGet({ locals: SESSION_LOCALS, url: url("/anulaciones/api/buscar") });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("mapea `data.hits`/`data.total` de GET /search (no `items`/`results`)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(
        JSON.stringify({
          query: "contrato",
          total: 2,
          hits: [
            { id: "a-1", tracking_number: "2026-ICETEX-E-000001", subject: "Contrato 1" },
            { id: "a-2", tracking_number: "2026-ICETEX-E-000002", subject: "Contrato 2" },
          ],
          page: 1,
          size: 20,
        }),
        { status: 200 },
      );
    });
    try {
      const res = await buscarGet({
        locals: SESSION_LOCALS,
        url: url("/anulaciones/api/buscar?q=contrato"),
      });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain("/search?");
      expect(capturedUrl).toContain("q=contrato");
      const body = await res.json();
      expect(body.total).toBe(2);
      expect(body.results).toHaveLength(2);
      expect(body.results[0].tracking_number).toBe("2026-ICETEX-E-000001");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
