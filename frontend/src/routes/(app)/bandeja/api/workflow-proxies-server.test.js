// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * workflow-proxies-server.test.js — proxies BFF de distribución de flujos
 * (D-05, ADR-011, E05 §Fase 1).
 *
 * Cubre lo que `bffProxyGuards.test.js` NO cubre (guard 401 genérico):
 *   - allowlist de payload (anti mass-assignment) en assign/transfer/transactions/complete.
 *   - validación de id (UUID) antes de tocar el gateway, con `encodeURIComponent` en la URL.
 *   - EL BUG QUE QUEREMOS IMPOSIBILITAR: `complete` usa `step_id` (ruta
 *     `api/steps/[stepId]/complete`), NUNCA `radicado_id` — se fija con un test
 *     dedicado que confirma la URL exacta hacia el gateway.
 *   - `assign`/`transfer` fijan `action` server-side (no se toma del cliente).
 *   - Authorization/X-Tenant-Slug/X-User-Id se inyectan desde locals, nunca del cliente.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as inboxGet } from "./inbox/+server.js";
import { GET as pendingGet } from "./pending/+server.js";
import { GET as transactionTypesGet } from "./transaction-types/+server.js";
import { POST as assignPost } from "./assign/+server.js";
import { POST as transferPost } from "./transfer/+server.js";
import { PATCH as completePatch } from "./steps/[stepId]/complete/+server.js";
import { POST as transactionsPost } from "./[id]/transactions/+server.js";
import { POST as rollbackPost } from "./[id]/rollback/+server.js";
import { POST as finalizarPost } from "./[id]/finalizar/+server.js";
import { GET as historyGet } from "./[id]/history/+server.js";
import { GET as eventsGet } from "./[id]/events/+server.js";
import { GET as currentGet } from "./[id]/current/+server.js";

const RAD_ID = "11111111-1111-1111-1111-111111111111";
const STEP_ID = "22222222-2222-2222-2222-222222222222";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const url = (path) => new URL(`http://localhost${path}`);

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

describe("bandeja/api/inbox GET", () => {
  it("box inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await inboxGet({
        locals: SESSION_LOCALS,
        url: url("/bandeja/api/inbox?box=raro"),
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("assigned_to inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await inboxGet({
        locals: SESSION_LOCALS,
        url: url("/bandeja/api/inbox?assigned_to=no-es-uuid"),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("id/box válidos: reenvía Authorization/X-Tenant-Slug/X-User-Id y envuelve {items,total} desde X-Total-Count", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify([{ id: "s-1" }]), {
        status: 200,
        headers: { "X-Total-Count": "1" },
      });
    });
    try {
      const res = await inboxGet({
        locals: SESSION_LOCALS,
        url: url("/bandeja/api/inbox?box=entrada"),
      });
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.total).toBe(1);
      expect(body.items).toHaveLength(1);
      expect(capturedUrl).toContain("/workflows/inbox?");
      expect(capturedUrl).toContain("box=entrada");
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("401 sin token, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await inboxGet({ locals: {}, url: url("/bandeja/api/inbox") });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/pending GET", () => {
  it("envuelve {items,total} desde X-Total-Count", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify([{ id: "s-1" }, { id: "s-2" }]), {
        status: 200,
        headers: { "X-Total-Count": "2" },
      });
    });
    try {
      const res = await pendingGet({ locals: SESSION_LOCALS, url: url("/bandeja/api/pending") });
      const body = await res.json();
      expect(body.total).toBe(2);
      expect(body.items).toHaveLength(2);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("401 sin token, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await pendingGet({ locals: {}, url: url("/bandeja/api/pending") });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/transaction-types GET", () => {
  it("reenvía el catálogo tal cual, sin envoltorio de paginación", async () => {
    const originalFetch = globalThis.fetch;
    const catalog = [{ code: "anular", nombre: "Anular", activo: true }];
    globalThis.fetch = mockFetchOnce(catalog, 200);
    try {
      const res = await transactionTypesGet({ locals: SESSION_LOCALS });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual(catalog);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("401 sin token, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await transactionTypesGet({ locals: {} });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/assign POST", () => {
  const validBody = {
    radicado_id: RAD_ID,
    tracking_number: "2026-ICETEX-E-000001",
    to_dept_code: 7,
  };

  it("radicado_id inválido: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await assignPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ ...validBody, radicado_id: "no-es-uuid" }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("to_dept_code ausente: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await assignPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ ...validBody, to_dept_code: undefined }) },
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: fija action='assign' server-side y descarta campos ajenos (anti mass-assignment)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "step-1" }), { status: 201 });
    });
    try {
      const res = await assignPost({
        locals: SESSION_LOCALS,
        request: {
          json: async () => ({
            ...validBody,
            action: "transfer", // intento de colar una acción distinta — debe ignorarse
            assigned_by: "otro-usuario-id", // campo ajeno
            status: "forzado", // campo ajeno
          }),
        },
      });
      expect(res.status).toBe(201);
      expect(capturedUrl).toContain("/workflows/assign");
      expect(capturedBody.action).toBe("assign");
      expect(capturedBody.assigned_by).toBeUndefined();
      expect(capturedBody.status).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("401 sin token, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await assignPost({ locals: {}, request: { json: async () => validBody } });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/transfer POST", () => {
  const validBody = {
    radicado_id: RAD_ID,
    tracking_number: "2026-ICETEX-E-000001",
    to_dept_code: 7,
  };

  it("el origen NO se reenvía aunque el cliente lo mande: lo deriva el servidor (ADR-032)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedBody;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "step-2" }), { status: 201 });
    });
    try {
      await transferPost({
        locals: SESSION_LOCALS,
        request: {
          json: async () => ({ ...validBody, from_dept: "Recepción", from_dept_code: 3 }),
        },
      });
      expect(capturedBody.from_dept).toBeUndefined();
      expect(capturedBody.from_dept_code).toBeUndefined();
      expect(capturedBody.to_dept_code).toBe(7);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([["Jurídica"], [0], [-1], [1000000], [1.5], [""]])(
    "to_dept_code %j inválido: 422, sin llamar al gateway",
    async (bad) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = forbidFetch();
      try {
        const res = await transferPost({
          locals: SESSION_LOCALS,
          request: { json: async () => ({ ...validBody, to_dept_code: bad }) },
        });
        expect(res.status).toBe(422);
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it("allowlist: fija action='transfer' server-side", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "step-2" }), { status: 201 });
    });
    try {
      const res = await transferPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ ...validBody, action: "assign" }) },
      });
      expect(res.status).toBe(201);
      expect(capturedUrl).toContain("/workflows/transfer");
      expect(capturedBody.action).toBe("transfer");
      expect(capturedBody.to_dept_code).toBe(7);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/steps/[stepId]/complete PATCH — usa step_id, NO radicado_id", () => {
  it("stepId inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await completePatch({
        locals: SESSION_LOCALS,
        params: { stepId: "no-es-uuid" },
        request: { json: async () => ({}) },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("la URL hacia el gateway usa el step_id de la ruta, no un radicado_id del body (bug crítico a imposibilitar)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedMethod;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedMethod = init?.method;
      return new Response(JSON.stringify({ id: STEP_ID, status: "completed" }), { status: 200 });
    });
    try {
      const res = await completePatch({
        locals: SESSION_LOCALS,
        params: { stepId: STEP_ID },
        // Un cliente malicioso o confundido intenta colar un radicado_id — debe ignorarse.
        request: { json: async () => ({ notes: "listo", radicado_id: RAD_ID }) },
      });
      expect(res.status).toBe(200);
      expect(capturedMethod).toBe("PATCH");
      expect(capturedUrl).toBe(
        `http://api-gateway:8080/api/v1/workflows/${encodeURIComponent(STEP_ID)}/complete`,
      );
      expect(capturedUrl).not.toContain(RAD_ID);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo se reenvía `notes`", async () => {
    const originalFetch = globalThis.fetch;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: STEP_ID }), { status: 200 });
    });
    try {
      await completePatch({
        locals: SESSION_LOCALS,
        params: { stepId: STEP_ID },
        request: { json: async () => ({ notes: "  listo  ", status: "forzado" }) },
      });
      expect(capturedBody).toEqual({ notes: "listo" });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("401 sin token, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await completePatch({
        locals: {},
        params: { stepId: STEP_ID },
        request: { json: async () => ({}) },
      });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/transactions POST", () => {
  it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await transactionsPost({
        locals: SESSION_LOCALS,
        params: { id: "no-es-uuid" },
        request: { json: async () => ({ tipo_tx: "anular" }) },
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("tipo_tx ausente: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await transactionsPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: { json: async () => ({}) },
      });
      expect(res.status).toBe(422);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: solo tipo_tx/comentario/detalles, encodeURIComponent en la URL, 403 del backend se reenvía tal cual (permiso dinámico por-tipo)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ error: "forbidden" }), { status: 403 });
    });
    try {
      const res = await transactionsPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: {
          json: async () => ({
            tipo_tx: "anular",
            comentario: "motivo",
            detalles: { x: 1 },
            actor: "otro-usuario", // campo ajeno
          }),
        },
      });
      expect(res.status).toBe(403);
      expect(capturedUrl).toContain(`/workflows/${encodeURIComponent(RAD_ID)}/transactions`);
      expect(capturedBody).toEqual({ tipo_tx: "anular", comentario: "motivo", detalles: { x: 1 } });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/rollback POST", () => {
  const motivoBody = (motivo) => ({ json: async () => ({ motivo }) });

  it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await rollbackPost({
        locals: SESSION_LOCALS,
        params: { id: "no-es-uuid" },
        request: motivoBody("Motivo válido de reversión."),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it.each([[undefined], [""], ["   "], [null], ["abcd"]])(
    "motivo=%j ausente/demasiado corto: 422 validation_error, sin llamar al gateway",
    async (motivo) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = forbidFetch("fetch no debió invocarse sin motivo válido");
      try {
        const res = await rollbackPost({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: motivoBody(motivo),
        });
        expect(res.status).toBe(422);
        expect((await res.json()).error).toBe("validation_error");
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it("motivo > 500 caracteres: 422 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch("fetch no debió invocarse con motivo demasiado largo");
    try {
      const res = await rollbackPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: motivoBody("a".repeat(501)),
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("409 del backend (RollbackError) se reenvía tal cual, no se traduce a 500", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce({ error: "no_previous_step" }, 409);
    try {
      const res = await rollbackPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: motivoBody("Motivo válido de reversión."),
      });
      expect(res.status).toBe(409);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("allowlist: reenvía solo `motivo` (recortado) — nunca el body íntegro del cliente (anti mass-assignment)", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedInit;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedInit = init;
      return new Response(JSON.stringify({ id: "step-3" }), { status: 200 });
    });
    try {
      const res = await rollbackPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: {
          json: async () => ({
            motivo: "  Se asignó por error.  ",
            // Campos ajenos que un cliente malicioso podría intentar colar:
            actor_id: "otro-usuario-id",
            estado: "forzado",
          }),
        },
      });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/workflows/${encodeURIComponent(RAD_ID)}/rollback`);
      expect(JSON.parse(capturedInit.body)).toEqual({ motivo: "Se asignó por error." });
      expect(capturedInit.headers.Authorization).toBe("Bearer tok-abc");
      expect(capturedInit.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(capturedInit.headers["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/history GET", () => {
  it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await historyGet({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("id válido: encodeURIComponent en la URL y reenvía headers de identidad", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(
        JSON.stringify({ radicado_id: RAD_ID, tracking_number: "x", steps: [] }),
        { status: 200 },
      );
    });
    try {
      const res = await historyGet({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/workflows/${encodeURIComponent(RAD_ID)}/history`);
      expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
      expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/events GET", () => {
  it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await eventsGet({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("id válido: encodeURIComponent en la URL hacia /events", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    try {
      const res = await eventsGet({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(`/workflows/${encodeURIComponent(RAD_ID)}/events`);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/current GET", () => {
  it("id inválido: 400 validation_error, sin llamar al gateway", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await currentGet({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("404 no_active_step del backend se reenvía tal cual (no-oráculo)", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = mockFetchOnce({ error: "no_active_step" }, 404);
    try {
      const res = await currentGet({ locals: SESSION_LOCALS, params: { id: RAD_ID } });
      expect(res.status).toBe(404);
      expect((await res.json()).error).toBe("no_active_step");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("bandeja/api/[id]/finalizar POST (ADR-028)", () => {
  const body = (comentario, extra = {}) => ({
    json: async () => ({ comentario, motivo_cierre: "respondido", ...extra }),
  });

  it.each([[undefined], [null], [""], ["otra"], ["RESPONDIDO"], [7]])(
    "motivo_cierre=%j inválido o ausente: 422 sin llamar al gateway",
    async (motivo) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = forbidFetch();
      try {
        const res = await finalizarPost({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: body("Trámite concluido.", { motivo_cierre: motivo }),
        });
        expect(res.status).toBe(422);
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it.each([[undefined], [""], ["   "], ["abcd"], ["x".repeat(501)]])(
    "comentario=%j inválido: 422 sin llamar al gateway",
    async (comentario) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = forbidFetch();
      try {
        const res = await finalizarPost({
          locals: SESSION_LOCALS,
          params: { id: RAD_ID },
          request: body(comentario),
        });
        expect(res.status).toBe(422);
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  it("reenvía SOLO { comentario, motivo_cierre } (allowlist) a /workflows/{id}/finalizar y propaga 409/404", async () => {
    const originalFetch = globalThis.fetch;
    const f = mockFetchOnce({ error: "radicado_terminal", status: 409 }, 409);
    globalThis.fetch = f;
    try {
      const res = await finalizarPost({
        locals: SESSION_LOCALS,
        params: { id: RAD_ID },
        request: body("  Trámite concluido.  ", {
          status: "closed",
          motivo_cierre: "traslado_competencia",
        }),
      });
      expect(res.status).toBe(409);
      const [u, init] = f.mock.calls[0];
      expect(String(u)).toContain(`/workflows/${RAD_ID}/finalizar`);
      expect(JSON.parse(init.body)).toEqual({
        comentario: "Trámite concluido.",
        motivo_cierre: "traslado_competencia",
      });
      expect(init.headers.Authorization).toBe("Bearer tok-abc");
      expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
