// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi, afterEach } from "vitest";
import { POST } from "./+server.js";

const JOB_ID = "11111111-1111-1111-1111-111111111111";
const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => vi.restoreAllMocks());

describe("lotes/documentos/[jobId]/reprocesar", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await POST({ locals: {}, params: { jobId: JOB_ID } });
    expect(res.status).toBe(401);
  });

  it("400 con jobId no-UUID, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await POST({ locals: LOCALS, params: { jobId: "../../x" } });
    expect(res.status).toBe(400);
  });

  it("POST al endpoint exacto, con token, tenant y usuario, sin cuerpo", async () => {
    let url, init;
    globalThis.fetch = vi.fn(async (u, i) => {
      url = u;
      init = i;
      return new Response(
        JSON.stringify({ job_id: JOB_ID, status: "pending", items_a_procesar: 3 }),
        {
          status: 202,
        },
      );
    });
    const res = await POST({ locals: LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(202);
    expect(url).toMatch(new RegExp(`/batch/documents/${JOB_ID}/reprocesar$`));
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(init.headers.Authorization).toBe("Bearer tok-abc");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(init.headers["X-User-Id"]).toBe(LOCALS.user.sub);
  });

  it.each([404, 409])("reenvía el %s del backend con su cuerpo", async (status) => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "lote_en_proceso" }), { status }),
    );
    const res = await POST({ locals: LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(status);
    expect((await res.json()).error).toBe("lote_en_proceso");
  });

  it("503 connection_error ante fallo de red", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await POST({ locals: LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
