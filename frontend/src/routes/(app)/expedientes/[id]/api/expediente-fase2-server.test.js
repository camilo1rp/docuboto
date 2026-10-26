// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expediente-fase2-server.test.js — proxies BFF de la Fase 2 del cierre del
 * desfase API↔UI: vinculación de radicados a expedientes + ciclo de vida del
 * índice electrónico / preservación / acceso (D-05, ADR-011).
 *
 * Cubre para cada proxy nuevo: guard 401 (ANTES de leer body/params),
 * allowlist anti mass-assignment donde aplica, validación de UUID y
 * `encodeURIComponent` del id en la URL hacia el gateway.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

import { POST as metadataPost } from "./metadata/+server.js";
import { POST as radicadosPost } from "./radicados/+server.js";
import { POST as radicadosBatchPost } from "./radicados/batch/+server.js";
import { POST as radicadosUnlinkPost } from "./radicados/[rid]/+server.js";
import { GET as foliadoGet } from "./foliado/+server.js";
import { GET as exportGet } from "./export/+server.js";
import { GET as indiceVersionsGet } from "./indice/versions/+server.js";
import { GET as indiceVerifyGet } from "./indice/verify/+server.js";
import { POST as indiceFirmarPost } from "./indice/firmar/+server.js";
import { GET as historialAccesoGet } from "./historial-acceso/+server.js";
import { GET as rolesAutorizadosGet } from "./roles-autorizados/+server.js";
import { GET as aipGet } from "./aip/+server.js";
import { GET as preservacionEventosGet } from "./preservacion-eventos/+server.js";

const EXP_ID = "11111111-1111-1111-1111-111111111111";
const RAD_ID = "22222222-2222-2222-2222-222222222222";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const url = (path) => new URL(`http://localhost${path}`);

function requestWith(body) {
  return { json: async () => body };
}

function requestThatMustNotBeRead() {
  return {
    json: () => {
      throw new Error("request.json() no debió invocarse sin sesión");
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Guard 401 — sin locals.accessToken
// ---------------------------------------------------------------------------
describe("Fase 2 — proxies BFF — 401 sin locals.accessToken (D-05)", () => {
  it("metadata POST → 401 (guard antes de leer el body)", async () => {
    const res = await metadataPost({
      locals: {},
      params: { id: EXP_ID },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("radicados POST → 401 (guard antes de leer el body)", async () => {
    const res = await radicadosPost({
      locals: {},
      params: { id: EXP_ID },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("radicados/batch POST → 401 (guard antes de leer el body)", async () => {
    const res = await radicadosBatchPost({
      locals: {},
      params: { id: EXP_ID },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("radicados/[rid] POST (exclusión) → 401 (guard antes de leer el body)", async () => {
    const res = await radicadosUnlinkPost({
      locals: {},
      params: { id: EXP_ID, rid: RAD_ID },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("foliado GET → 401", async () => {
    const res = await foliadoGet({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("export GET → 401", async () => {
    const res = await exportGet({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("indice/versions GET → 401", async () => {
    const res = await indiceVersionsGet({
      locals: {},
      params: { id: EXP_ID },
      url: url("/expedientes/x/api/indice/versions"),
    });
    expect(res.status).toBe(401);
  });

  it("indice/verify GET → 401", async () => {
    const res = await indiceVerifyGet({
      locals: {},
      params: { id: EXP_ID },
      url: url("/expedientes/x/api/indice/verify"),
    });
    expect(res.status).toBe(401);
  });

  it("indice/firmar POST → 401", async () => {
    const res = await indiceFirmarPost({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("historial-acceso GET → 401", async () => {
    const res = await historialAccesoGet({
      locals: {},
      params: { id: EXP_ID },
      url: url("/expedientes/x/api/historial-acceso"),
    });
    expect(res.status).toBe(401);
  });

  it("roles-autorizados GET → 401", async () => {
    const res = await rolesAutorizadosGet({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("aip GET → 401", async () => {
    const res = await aipGet({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("preservacion-eventos GET → 401", async () => {
    const res = await preservacionEventosGet({ locals: {}, params: { id: EXP_ID } });
    expect(res.status).toBe(401);
  });
});

// ---------------------------------------------------------------------------
// metadata (PATCH del expediente)
// ---------------------------------------------------------------------------
describe("expedientes/[id]/api/metadata POST", () => {
  it("id de expediente inválido: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    try {
      const res = await metadataPost({
        locals: SESSION_LOCALS,
        params: { id: "no-es-un-uuid" },
        request: requestWith({ name: "x" }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("name vacío: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await metadataPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({ name: "   " }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("sin cambios: 422 validation_error (payload vacío)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await metadataPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({}),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo name/description/trd_serie_id, PATCH real hacia el gateway, encodeURIComponent del id", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl, capturedBody, capturedMethod, capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedMethod = init.method;
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: EXP_ID, name: "Nuevo nombre" }), { status: 200 });
    });
    try {
      const res = await metadataPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({
          name: "Nuevo nombre",
          description: "desc",
          trd_serie_id: null,
          // campos ajenos que un cliente malicioso podría colar:
          status: "closed",
          code: "OTRO-CODIGO",
        }),
      });
      expect(res.status).toBe(200);
      expect(capturedMethod).toBe("PATCH");
      expect(capturedUrl).toContain(`/expedientes/${encodeURIComponent(EXP_ID)}`);
      expect(capturedBody).toEqual({
        name: "Nuevo nombre",
        description: "desc",
        trd_serie_id: null,
      });
      expect(capturedBody.status).toBeUndefined();
      expect(capturedBody.code).toBeUndefined();
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("trd_serie_id inválido (no UUID): 422, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await metadataPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({ trd_serie_id: "no-es-uuid" }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// radicados (vincular uno) y radicados/batch (vincular en lote)
// ---------------------------------------------------------------------------
describe("expedientes/[id]/api/radicados POST (vincular uno)", () => {
  it("radicado_id inválido: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({ radicado_id: "no-es-uuid", tracking_number: "2026-E-1" }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("tracking_number ausente: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({ radicado_id: RAD_ID }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo radicado_id/tracking_number reenviados, X-User-Id inyectado desde locals", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl, capturedBody, capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(
        JSON.stringify({
          radicado_id: RAD_ID,
          tracking_number: "2026-E-1",
          added_at: "2026-01-01T00:00:00Z",
        }),
        { status: 201 },
      );
    });
    try {
      const res = await radicadosPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({
          radicado_id: RAD_ID,
          tracking_number: "2026-E-1",
          // campos ajenos:
          content_hash: "a".repeat(64),
          formato: "PDF",
        }),
      });
      expect(res.status).toBe(201);
      expect(capturedUrl).toContain(`/expedientes/${encodeURIComponent(EXP_ID)}/radicados`);
      expect(capturedUrl).not.toContain("/radicados/batch");
      expect(capturedBody).toEqual({ radicado_id: RAD_ID, tracking_number: "2026-E-1" });
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("expedientes/[id]/api/radicados/batch POST (vincular en lote)", () => {
  it("lista vacía: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosBatchPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({ radicados: [] }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("un ítem con radicado_id inválido invalida todo el lote: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosBatchPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({
          radicados: [
            { radicado_id: RAD_ID, tracking_number: "2026-E-1" },
            { radicado_id: "malo", tracking_number: "2026-E-2" },
          ],
        }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("lote válido: allowlist por ítem, POST hacia /radicados/batch", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl, capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ vinculados: 2, omitidos: 0, total: 2 }), {
        status: 200,
      });
    });
    try {
      const res = await radicadosBatchPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({
          radicados: [
            { radicado_id: RAD_ID, tracking_number: "2026-E-1", contenido_extra: "x" },
            { radicado_id: "44444444-4444-4444-4444-444444444444", tracking_number: "2026-E-2" },
          ],
        }),
      });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/expedientes/${encodeURIComponent(EXP_ID)}/radicados/batch`);
      expect(capturedBody.radicados).toEqual([
        { radicado_id: RAD_ID, tracking_number: "2026-E-1" },
        { radicado_id: "44444444-4444-4444-4444-444444444444", tracking_number: "2026-E-2" },
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("BatchLinkResult con vinculados/omitidos null (M2, no-read-up) se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ vinculados: null, omitidos: null, total: 2 }), {
          status: 200,
        }),
    );
    try {
      const res = await radicadosBatchPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        request: requestWith({
          radicados: [{ radicado_id: RAD_ID, tracking_number: "2026-E-1" }],
        }),
      });
      const body = await res.json();
      expect(body.vinculados).toBeNull();
      expect(body.total).toBe(2);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// radicados/[rid] (excluir — soft-delete)
// ---------------------------------------------------------------------------
describe("expedientes/[id]/api/radicados/[rid] POST (excluir)", () => {
  it("rid inválido: 400, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosUnlinkPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID, rid: "malo" },
        request: requestWith({ causal: "motivo" }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("causal ausente: 422, motivo obligatorio, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosUnlinkPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID, rid: RAD_ID },
        request: requestWith({}),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("causal > 500 caracteres: 422, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await radicadosUnlinkPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID, rid: RAD_ID },
        request: requestWith({ causal: "a".repeat(501) }),
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("DELETE real hacia el gateway con causal en query, 204 → 200 { ok: true }", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl, capturedMethod;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedMethod = init.method;
      return new Response(null, { status: 204 });
    });
    try {
      const res = await radicadosUnlinkPost({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID, rid: RAD_ID },
        request: requestWith({ causal: "Documento duplicado" }),
      });
      expect(res.status).toBe(200);
      expect((await res.json()).ok).toBe(true);
      expect(capturedMethod).toBe("DELETE");
      expect(capturedUrl).toContain(
        `/expedientes/${encodeURIComponent(EXP_ID)}/radicados/${encodeURIComponent(RAD_ID)}`,
      );
      expect(capturedUrl).toContain("causal=Documento");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// Lecturas simples — validación de id, encodeURIComponent, pass-through
// ---------------------------------------------------------------------------
describe("Fase 2 — proxies de lectura simples", () => {
  it("foliado: id inválido → 400 sin llamar al gateway; id válido → pass-through", async () => {
    const badRes = await foliadoGet({ locals: SESSION_LOCALS, params: { id: "malo" } });
    expect(badRes.status).toBe(400);

    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(
        JSON.stringify({ expediente_id: EXP_ID, code: "X", total_folios: 3, items: [] }),
        { status: 200 },
      );
    });
    try {
      const res = await foliadoGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/expedientes/${encodeURIComponent(EXP_ID)}/foliado`);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("indice/versions: mapea a { items, total } con X-Total-Count", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ version: 1, estado: "firmado" }]), {
          status: 200,
          headers: { "X-Total-Count": "1" },
        }),
    );
    try {
      const res = await indiceVersionsGet({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        url: url(`/expedientes/${EXP_ID}/api/indice/versions`),
      });
      const body = await res.json();
      expect(body.total).toBe(1);
      expect(body.items).toHaveLength(1);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("indice/verify: reenvía version solo si es numérica", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ version: 2, valido: true }), { status: 200 });
    });
    try {
      await indiceVerifyGet({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        url: url(`/expedientes/${EXP_ID}/api/indice/verify?version=abc`),
      });
      expect(capturedUrl).not.toContain("version=");

      await indiceVerifyGet({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        url: url(`/expedientes/${EXP_ID}/api/indice/verify?version=2`),
      });
      expect(capturedUrl).toContain("version=2");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("indice/firmar: POST con X-User-Id, pass-through del resultado", async () => {
    const originalFetch = globalThis.fetch;
    let capturedHeaders, capturedMethod;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init.headers;
      capturedMethod = init.method;
      return new Response(JSON.stringify({ firma_id: "f-1", estado: "firmado" }), { status: 200 });
    });
    try {
      const res = await indiceFirmarPost({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(200);
      expect(capturedMethod).toBe("POST");
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("indice/firmar: 409 upstream (ya firmado) se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "indice_ya_firmado" }), { status: 409 }),
    );
    try {
      const res = await indiceFirmarPost({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(409);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("historial-acceso: pagina y satura tamaño a [1,100]", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "0" } });
    });
    try {
      await historialAccesoGet({
        locals: SESSION_LOCALS,
        params: { id: EXP_ID },
        url: url(`/expedientes/${EXP_ID}/api/historial-acceso?size=9999`),
      });
      expect(capturedUrl).toContain("size=100");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("roles-autorizados: pass-through de la lista", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ grupo: "archivistas", clearanceMax: 3, crud: 2 }]), {
          status: 200,
        }),
    );
    try {
      const res = await rolesAutorizadosGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      const body = await res.json();
      expect(body).toHaveLength(1);
      expect(body[0].grupo).toBe("archivistas");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("aip: 404 (no empaquetado / sin clearance) se reenvía tal cual", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "not_found" }), { status: 404 }),
    );
    try {
      const res = await aipGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(404);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("preservacion-eventos: lista vacía es una respuesta legítima (200)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
    try {
      const res = await preservacionEventosGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual([]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// export.zip — streaming binario, sin bufferizar
// ---------------------------------------------------------------------------
describe("expedientes/[id]/api/export GET (ZIP streaming)", () => {
  it("id inválido: 400 sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await exportGet({ locals: SESSION_LOCALS, params: { id: "malo" } });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 upstream → not_found neutro", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({}), { status: 404 }));
    try {
      const res = await exportGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(404);
      expect((await res.json()).error).toBe("not_found");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("200: retransmite el body en streaming (no lo bufferiza) y sanea el filename", async () => {
    const originalFetch = globalThis.fetch;
    const fakeStream = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2, 3]));
        controller.close();
      },
    });
    globalThis.fetch = vi.fn(async () => {
      const res = new Response(fakeStream, {
        status: 200,
        headers: {
          "content-disposition": 'attachment; filename="../../etc/passwd"',
        },
      });
      return res;
    });
    try {
      const res = await exportGet({ locals: SESSION_LOCALS, params: { id: EXP_ID } });
      expect(res.status).toBe(200);
      expect(res.headers.get("Content-Type")).toBe("application/zip");
      // Nombre no seguro (contiene "/") se descarta a favor del genérico.
      expect(res.headers.get("Content-Disposition")).toContain(`expediente_${EXP_ID}.zip`);
      expect(res.headers.get("Content-Disposition")).not.toContain("etc/passwd");
      // El cuerpo es el mismo stream (pass-through), no un blob materializado.
      expect(res.body).toBe(fakeStream);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
