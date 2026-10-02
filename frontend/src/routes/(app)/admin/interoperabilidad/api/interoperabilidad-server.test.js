// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * interoperabilidad-server.test.js — Proxies /admin/interoperabilidad/api/*
 * (D-05, E11 RF-INT-01/RF-INT-05, Fase 8).
 *
 * Cubre: guard 401 en cada handler, allowlist campo a campo (export, batch
 * documentos/expedientes), encodeURIComponent + validación de UUID en los
 * [jobId], streaming/paso de cabeceras en la descarga binaria de exportar, y
 * la distinción "no se pudo consultar" vs "aún sin resultados" del sondeo.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as exportar } from "./exportar/+server.js";
import { POST as importar } from "./importar/+server.js";
import { POST as crearLoteDocs } from "./lotes/documentos/+server.js";
import { GET as statusLoteDocs } from "./lotes/documentos/[jobId]/+server.js";
import { POST as crearLoteExps } from "./lotes/expedientes/+server.js";
import { GET as statusLoteExps } from "./lotes/expedientes/[jobId]/+server.js";

const JOB_ID = "11111111-1111-1111-1111-111111111111";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// exportar
// ---------------------------------------------------------------------------
describe("exportar", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await exportar({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist campo a campo: descarta campos ajenos y sanea doc_type inválido", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_url, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(new Blob(["zip-bytes"]), {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="orpyca-export-abc.zip"',
          "X-Export-Count": "5",
          "X-Export-Excluded": "1",
        },
      });
    });
    const res = await exportar({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          comentario: "motivo",
          year: 2024,
          doc_type: "Z", // inválido: se descarta, no se reenvía
          admin_override: true, // ajeno: nunca debe llegar al gateway
        }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ comentario: "motivo", year: 2024 });
  });

  it("pasa Content-Type/Content-Disposition sin bufferizar y sanea el filename hostil", async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(new Blob(["zip-bytes"]), {
        status: 200,
        headers: {
          // filename con caracteres fuera de la allowlist (comillas/ruta) — debe descartarse.
          "Content-Disposition": 'attachment; filename="../../etc/passwd\\".zip"',
          "X-Export-Count": "2",
          "X-Export-Excluded": "0",
        },
      });
    });
    const res = await exportar({ locals: SESSION_LOCALS, request: { json: async () => ({}) } });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/zip");
    // Nombre hostil rechazado -> fallback genérico, nunca reenviado tal cual.
    expect(res.headers.get("Content-Disposition")).toBe('attachment; filename="orpyca-export.zip"');
    expect(res.headers.get("X-OrpycaMCP-Export-Count")).toBe("2");
    expect(res.headers.get("X-OrpycaMCP-Export-Excluded")).toBe("0");
  });

  it("reenvía el body como stream (mismo objeto Response.body upstream)", async () => {
    const upstreamRes = new Response(new Blob(["contenido-zip"]), {
      status: 200,
      headers: { "Content-Disposition": 'attachment; filename="export.zip"' },
    });
    globalThis.fetch = vi.fn(async () => upstreamRes);
    const res = await exportar({ locals: SESSION_LOCALS, request: { json: async () => ({}) } });
    expect(res.body).toBe(upstreamRes.body);
  });
});

// ---------------------------------------------------------------------------
// importar
// ---------------------------------------------------------------------------
describe("importar", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await importar({
      locals: {},
      request: { headers: new Headers({ "content-type": "application/zip" }), body: null },
    });
    expect(res.status).toBe(401);
  });

  it("400 si el Content-Type no es application/zip, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await importar({
      locals: SESSION_LOCALS,
      request: { headers: new Headers({ "content-type": "application/json" }), body: null },
    });
    expect(res.status).toBe(400);
  });

  it("422 reenvía el detail EXACTO del backend (qué archivo/checksum falló)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: "paquete_invalido",
            detail: "checksum del binario anexos/x/foo.pdf NO coincide — import abortado",
            status: 422,
          }),
          { status: 422 },
        ),
    );
    const res = await importar({
      locals: SESSION_LOCALS,
      request: {
        headers: new Headers({ "content-type": "application/zip" }),
        body: new Uint8Array([0x50, 0x4b]),
      },
    });
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.detail).toBe("checksum del binario anexos/x/foo.pdf NO coincide — import abortado");
  });

  it("200 reenvía el resumen tal cual (importados, omitidos, antecedentes_no_resueltos)", async () => {
    const summary = {
      import_uuid: "u-1",
      importados: 3,
      omitidos_existentes: 1,
      omitidos_clearance: 0,
      relaciones_reconstruidas: 1,
      antecedentes_no_resueltos: ["2024-ICETEX-E-000999"],
    };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(summary), { status: 200 }));
    const res = await importar({
      locals: SESSION_LOCALS,
      request: {
        headers: new Headers({ "content-type": "application/zip" }),
        body: new Uint8Array([0x50, 0x4b]),
      },
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(summary);
  });
});

// ---------------------------------------------------------------------------
// lotes/documentos
// ---------------------------------------------------------------------------
describe("lotes/documentos (crear)", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await crearLoteDocs({
      locals: {},
      request: { json: async () => ({ documents: [] }) },
    });
    expect(res.status).toBe(401);
  });

  it("400 sin documents (0 ítems), sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await crearLoteDocs({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ documents: [] }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist campo a campo: descarta campos ajenos por ítem", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (_url, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({ job_id: JOB_ID, job_type: "documents", status: "pending" }),
        {
          status: 202,
        },
      );
    });
    const res = await crearLoteDocs({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          documents: [
            { doc_type: "E", subject: "Asunto 1", status: "registered", tenant_code: "OTRO" },
          ],
        }),
      },
    });
    expect(res.status).toBe(202);
    expect(capturedBody.documents).toEqual([{ doc_type: "E", subject: "Asunto 1" }]);
  });
});

describe("lotes/documentos/[jobId] (sondeo)", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await statusLoteDocs({ locals: {}, params: { jobId: JOB_ID } });
    expect(res.status).toBe(401);
  });

  it("400 con jobId no-UUID, sin llamar al gateway (sin encodeURIComponent de basura)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("encodeURIComponent en la URL upstream", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
    });
    await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(capturedUrl).toContain(encodeURIComponent(JOB_ID));
  });

  it("envía X-User-Id al backend y 401 si la sesión no trae identidad", async () => {
    let headers;
    globalThis.fetch = vi.fn(async (_u, init) => {
      headers = init.headers;
      return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
    });
    await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(headers["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    const res = await statusLoteDocs({
      locals: { accessToken: "t", user: { tenant_slug: "icetex" } },
      params: { jobId: JOB_ID },
    });
    expect(res.status).toBe(401);
  });

  it("reenvía el 404 anidado de un lote ajeno sin alterarlo", async () => {
    const cuerpo = {
      detail: {
        error: "lote_no_encontrado",
        detail: "No existe el trabajo solicitado",
        status: 404,
      },
    };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(cuerpo), { status: 404 }));
    const res = await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual(cuerpo);
  });

  it("503 ante un fallo de red: nunca sintetiza un estado con contadores en 0", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.error).toBe("connection_error");
    // No debe traer processed_items/total_items — sería un job sintético.
    expect(body.processed_items).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// lotes/expedientes
// ---------------------------------------------------------------------------
describe("lotes/expedientes (crear)", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await crearLoteExps({
      locals: {},
      request: { json: async () => ({ expedientes: [] }) },
    });
    expect(res.status).toBe(401);
  });

  it("400 sin nombre en algún ítem, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await crearLoteExps({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ expedientes: [{ name: "" }] }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: `trd_serie_id` no-UUID se descarta; una referencia ilegible CORTA el lote", async () => {
    // Este test cambió de expectativa a propósito. Antes comprobaba que una
    // referencia de radicado ilegible se **descartaba en silencio** y el lote
    // seguía: el expediente se creaba sin ese documento y la pantalla decía
    // «Completado · 0 con error». Descartar en silencio era la mitad callada
    // del defecto que se corrigió aquí — una referencia que no se puede
    // resolver detiene el lote y se nombra en la respuesta.
    //
    // `trd_serie_id` sigue descartándose: es un campo opcional de
    // clasificación, no una pieza del contenido del expediente.
    globalThis.fetch = vi.fn(async (url) => {
      if (String(url).includes("/by-tracking/")) {
        return new Response(JSON.stringify({}), { status: 404 });
      }
      return new Response(
        JSON.stringify({ job_id: JOB_ID, job_type: "expedientes", status: "pending" }),
        { status: 202 },
      );
    });
    const res = await crearLoteExps({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          expedientes: [
            {
              name: "Expediente 1",
              trd_serie_id: "no-es-uuid",
              radicado_ids: ["no-es-uuid"],
            },
          ],
        }),
      },
    });
    expect(res.status).toBe(400);
    const cuerpo = await res.json();
    expect(cuerpo.error).toBe("radicado_no_resuelto");
  });

  it("un UUID válido de radicado se reenvía sin consultar la API", async () => {
    // Control positivo del camino que ya funcionaba: quien manda el UUID
    // directamente (integraciones, MCP) no paga una resolución de más.
    let capturedBody;
    globalThis.fetch = vi.fn(async (url, init) => {
      if (String(url).includes("/by-tracking/")) throw new Error("no debía resolverse un UUID");
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({ job_id: JOB_ID, job_type: "expedientes", status: "pending" }),
        { status: 202 },
      );
    });
    const res = await crearLoteExps({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          expedientes: [{ name: "Expediente 1", radicado_ids: [JOB_ID] }],
        }),
      },
    });
    expect(res.status).toBe(202);
    expect(capturedBody.expedientes[0].radicado_ids).toEqual([JOB_ID]);
  });
});

describe("lotes/expedientes/[jobId] (sondeo)", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await statusLoteExps({ locals: {}, params: { jobId: JOB_ID } });
    expect(res.status).toBe(401);
  });

  it("503 ante un fallo de red del proxy propio (nunca gateway directo desde cliente)", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await statusLoteExps({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

// ---------------------------------------------------------------------------
// Contrato de URL upstream (revisión UX 2026-08-02, C2): fija la forma EXACTA
// de la ruta que cada proxy de sondeo pide al gateway. document-service y
// archive-service comparten el prefijo raíz `/api/v1/batch` con el mismo
// contrato `{job_id}/status`, distinguidos SOLO por el tipo de job anidado
// (`documents` vs `expedientes` — ver `services/api-gateway/app/routers/
// proxy.py`). Un renombrado silencioso de cualquiera de las dos rutas debe
// romper este test, no producir un 404 permanente en el panel de progreso.
// ---------------------------------------------------------------------------
describe("contrato de URL upstream — sondeo de lotes", () => {
  it("documentos: pide GET {API_V1}/batch/documents/{jobId}/status", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
    });
    await statusLoteDocs({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(capturedUrl).toMatch(new RegExp(`/api/v1/batch/documents/${JOB_ID}/status$`));
  });

  it("expedientes: pide GET {API_V1}/batch/expedientes/{jobId}/status", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify({ status: "processing" }), { status: 200 });
    });
    await statusLoteExps({ locals: SESSION_LOCALS, params: { jobId: JOB_ID } });
    expect(capturedUrl).toMatch(new RegExp(`/api/v1/batch/expedientes/${JOB_ID}/status$`));
  });

  it("crear lote documentos: POST a {API_V1}/batch/documents", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(
        JSON.stringify({ job_id: JOB_ID, job_type: "documents", status: "pending" }),
        { status: 202 },
      );
    });
    await crearLoteDocs({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ documents: [{ doc_type: "E", subject: "Asunto" }] }) },
    });
    expect(capturedUrl).toMatch(/\/api\/v1\/batch\/documents$/);
  });

  it("crear lote expedientes: POST a {API_V1}/batch/expedientes", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(
        JSON.stringify({ job_id: JOB_ID, job_type: "expedientes", status: "pending" }),
        { status: 202 },
      );
    });
    await crearLoteExps({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ expedientes: [{ name: "Expediente 1" }] }) },
    });
    expect(capturedUrl).toMatch(/\/api\/v1\/batch\/expedientes$/);
  });
});

describe("lote de expedientes — los números de radicado SÍ vinculan", () => {
  /**
   * El proxy manda `radicado_ids`, no solo `tracking_numbers`.
   *
   * Con la versión anterior, un lote con «2026-DEMO-E-000017» en el campo
   * «Números de radicado a vincular» creaba el expediente **vacío** y el panel
   * decía «Completado · 0 con error»: archive-service vincula por
   * `radicado_ids` y trata `tracking_numbers` como etiquetas paralelas.
   */
  const UUID_R = "11111111-2222-4333-8444-555555555555";

  function stubBackend() {
    return vi.fn(async (url, opciones) => {
      const u = String(url);
      if (u.includes("/by-tracking/")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ id: UUID_R, tracking_number: "2026-DEMO-E-000017" }),
        };
      }
      return {
        ok: true,
        status: 202,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({ job_id: "job-1", enviado: JSON.parse(opciones.body) }),
      };
    });
  }

  it("un número de radicado llega al backend como radicado_ids", async () => {
    const fn = stubBackend();
    vi.stubGlobal("fetch", fn);
    const { POST } = await import("./lotes/expedientes/+server.js");

    await POST({
      locals: { accessToken: "t", user: { tenant_slug: "demo", sub: "u1" } },
      request: new Request("http://x", {
        method: "POST",
        body: JSON.stringify({
          expedientes: [{ name: "Expediente X", tracking_numbers: ["2026-DEMO-E-000017"] }],
        }),
      }),
    });

    const llamadaAlLote = fn.mock.calls.find(([u]) => String(u).includes("/batch/expedientes"));
    expect(llamadaAlLote, "no se llamó al endpoint del lote").toBeTruthy();
    const enviado = JSON.parse(llamadaAlLote[1].body);
    expect(enviado.expedientes[0].radicado_ids).toEqual([UUID_R]);
    expect(enviado.expedientes[0].tracking_numbers).toEqual(["2026-DEMO-E-000017"]);
  });

  it("un número que no resuelve corta el lote con un error legible", async () => {
    // Gemelo: la corrección no puede consistir en vincular lo que se pueda y
    // callar el resto — un expediente al que le falta un documento es uno
    // equivocado, y «0 con error» lo daba por bueno.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("/by-tracking/"))
          return { ok: false, status: 404, json: async () => ({}) };
        throw new Error("no debería llegar al backend del lote");
      }),
    );
    const { POST } = await import("./lotes/expedientes/+server.js");

    const res = await POST({
      locals: { accessToken: "t", user: { tenant_slug: "demo", sub: "u1" } },
      request: new Request("http://x", {
        method: "POST",
        body: JSON.stringify({
          expedientes: [{ name: "Expediente X", tracking_numbers: ["2026-DEMO-E-999999"] }],
        }),
      }),
    });

    expect(res.status).toBe(400);
    const cuerpo = await res.json();
    expect(cuerpo.error).toBe("radicado_no_resuelto");
    expect(cuerpo.detail).toContain("2026-DEMO-E-999999");
  });
});
