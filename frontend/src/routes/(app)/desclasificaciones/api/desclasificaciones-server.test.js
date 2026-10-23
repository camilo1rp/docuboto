// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * desclasificaciones-server.test.js — BFF de la desclasificación con segunda
 * persona (C-2, ADR-039, D-05): cola, aprobar, rechazar, retirar y el
 * historial/solicitud bajo bandeja/api/[id]/desclasificacion.
 *
 * Cada gate tiene su control positivo: junto al 401 / 400 / 422 que se espera, un
 * caso válido que SÍ llega al gateway con lo que debe (si el guard rechazara todo,
 * los negativos pasarían igual).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as colaGet } from "./cola/+server.js";
import { POST as aprobarPost } from "./[id]/aprobar/+server.js";
import { POST as rechazarPost } from "./[id]/rechazar/+server.js";
import { POST as retirarPost } from "./[id]/retirar/+server.js";
import {
  GET as historialGet,
  POST as solicitarPost,
} from "../../bandeja/api/[id]/desclasificacion/+server.js";

const ID = "11111111-1111-1111-1111-111111111111";
const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};
const req = (cuerpo) => ({ json: async () => cuerpo });
const url = (p) => new URL(`http://localhost${p}`);
const ok = (cuerpo, init = {}) =>
  new Response(JSON.stringify(cuerpo), {
    status: 200,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });

afterEach(() => vi.restoreAllMocks());

/** Ejecuta `fn` con un fetch global que lanza si se invoca. */
async function sinRed(fn) {
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(() => {
    throw new Error("fetch no debió invocarse");
  });
  try {
    return await fn();
  } finally {
    globalThis.fetch = original;
  }
}

describe("401 sin sesión, antes de tocar el gateway", () => {
  it.each([
    ["cola GET", () => colaGet({ locals: {}, url: url("/x"), fetch: globalThis.fetch })],
    ["aprobar", () => aprobarPost({ locals: {}, params: { id: ID }, request: req({}) })],
    ["rechazar", () => rechazarPost({ locals: {}, params: { id: ID }, request: req({}) })],
    ["retirar", () => retirarPost({ locals: {}, params: { id: ID }, request: req({}) })],
    ["historial", () => historialGet({ locals: {}, params: { id: ID } })],
    ["solicitar", () => solicitarPost({ locals: {}, params: { id: ID }, request: req({}) })],
  ])("%s", async (_n, llamar) => {
    const res = await sinRed(llamar);
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });
});

describe("id de ruta: solo UUID", () => {
  it.each([["../../etc"], ["'; DROP"], ["x"]])("id=%s → 400", async (bad) => {
    for (const llamar of [
      () =>
        aprobarPost({ locals: LOCALS, params: { id: bad }, request: req({ confirmado: true }) }),
      () =>
        rechazarPost({
          locals: LOCALS,
          params: { id: bad },
          request: req({ resolucion_motivo: "motivo suficiente" }),
        }),
      () => retirarPost({ locals: LOCALS, params: { id: bad }, request: req({}) }),
      () => historialGet({ locals: LOCALS, params: { id: bad } }),
      () => solicitarPost({ locals: LOCALS, params: { id: bad }, request: req({}) }),
    ]) {
      expect((await sinRed(llamar)).status).toBe(400);
    }
  });
});

describe("cola GET", () => {
  it("control positivo: pide /desclasificaciones con token, tenant y X-Total-Count", async () => {
    const f = vi.fn(async () => ok([{ id: ID }], { headers: { "X-Total-Count": "7" } }));
    const res = await colaGet({ locals: LOCALS, url: url("/x?page=2&size=500"), fetch: f });
    const [u, init] = f.mock.calls[0];
    expect(u).toContain("/desclasificaciones?page=2&size=20");
    expect(init.headers.Authorization).toBe("Bearer tok-abc");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(await res.json()).toEqual({ items: [{ id: ID }], total: 7 });
  });

  it("403 del backend: se propaga con su estado (no se vuelve «cola vacía»)", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ detail: "no" }), { status: 403 }));
    const res = await colaGet({ locals: LOCALS, url: url("/x"), fetch: f });
    expect(res.status).toBe(403);
  });
});

describe("aprobar", () => {
  it("sin `confirmado: true` NO llega al gateway (422)", async () => {
    for (const cuerpo of [{}, { confirmado: "true" }, { confirmado: false }, null]) {
      const res = await sinRed(() =>
        aprobarPost({ locals: LOCALS, params: { id: ID }, request: req(cuerpo) }),
      );
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("confirmacion_requerida");
    }
  });

  it("control positivo: confirmado → POST /aprobar sin cuerpo del cliente", async () => {
    const f = vi.fn(async () => ok({ nivel_seguridad: 1 }));
    globalThis.fetch = f;
    const res = await aprobarPost({
      locals: LOCALS,
      params: { id: ID },
      request: req({ confirmado: true, nivel_seguridad: 1, resuelto_por: "x" }),
    });
    const [u, init] = f.mock.calls[0];
    expect(u).toContain(`/desclasificaciones/${ID}/aprobar`);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(init.headers["X-User-Id"]).toBe(LOCALS.user.sub);
    expect(res.status).toBe(200);
  });

  it("propaga el 409 del backend (separación de funciones) tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: { error: "separacion_de_funciones", status: 409 } }),
          { status: 409 },
        ),
    );
    const res = await aprobarPost({
      locals: LOCALS,
      params: { id: ID },
      request: req({ confirmado: true }),
    });
    expect(res.status).toBe(409);
  });
});

describe("rechazar", () => {
  it.each([[""], ["corto"], ["x".repeat(501)], [undefined]])(
    "motivo inválido (%s) → 422 sin gateway",
    async (m) => {
      const res = await sinRed(() =>
        rechazarPost({
          locals: LOCALS,
          params: { id: ID },
          request: req({ resolucion_motivo: m }),
        }),
      );
      expect(res.status).toBe(422);
    },
  );

  it("control positivo: reenvía SOLO resolucion_motivo recortado", async () => {
    const f = vi.fn(async () => ok({ estado: "rechazada" }));
    globalThis.fetch = f;
    await rechazarPost({
      locals: LOCALS,
      params: { id: ID },
      request: req({ resolucion_motivo: "  motivo suficiente  ", estado: "aprobada" }),
    });
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({
      resolucion_motivo: "motivo suficiente",
    });
  });
});

describe("retirar", () => {
  it("motivo > 500 → 422 sin gateway", async () => {
    const res = await sinRed(() =>
      retirarPost({
        locals: LOCALS,
        params: { id: ID },
        request: req({ motivo: "x".repeat(501) }),
      }),
    );
    expect(res.status).toBe(422);
  });

  it("control positivo: cuerpo vacío si no hay motivo; apunta a /retirar", async () => {
    const f = vi.fn(async () => ok({ estado: "retirada" }));
    globalThis.fetch = f;
    await retirarPost({ locals: LOCALS, params: { id: ID }, request: req({}) });
    expect(f.mock.calls[0][0]).toContain(`/desclasificaciones/${ID}/retirar`);
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({});
  });
});

describe("bandeja/api/[id]/desclasificacion", () => {
  const valido = { nivel_seguridad: 2, motivo: "motivo suficiente" };

  it("GET control positivo: devuelve la lista del historial", async () => {
    const f = vi.fn(async () => ok([{ id: "s1", estado_efectivo: "pendiente" }]));
    globalThis.fetch = f;
    const res = await historialGet({ locals: LOCALS, params: { id: ID } });
    expect(f.mock.calls[0][0]).toContain(`/documents/${ID}/desclasificacion`);
    expect(await res.json()).toEqual([{ id: "s1", estado_efectivo: "pendiente" }]);
  });

  it.each([
    [{ ...valido, nivel_seguridad: 3 }],
    [{ ...valido, nivel_seguridad: 0 }],
    [{ ...valido, motivo: "corto" }],
    [{ ...valido, plazo_reserva_meses: 0 }],
    [{ ...valido, plazo_reserva_meses: 181 }],
    [{ ...valido, plazo_reserva_meses: 1.5 }],
  ])("cuerpo inválido %# → 422 sin gateway", async (cuerpo) => {
    const res = await sinRed(() =>
      solicitarPost({ locals: LOCALS, params: { id: ID }, request: req(cuerpo) }),
    );
    expect(res.status).toBe(422);
  });

  it("control positivo: allowlist, plazo entero y sin campos ajenos", async () => {
    const f = vi.fn(async () => ok({ id: "s1" }));
    globalThis.fetch = f;
    await solicitarPost({
      locals: LOCALS,
      params: { id: ID },
      request: req({
        ...valido,
        causal_reserva: "art19_b_seguridad_publica",
        fundamento_juridico: "Ley X",
        plazo_reserva_meses: "60",
        solicitado_por: "otro",
        estado: "aprobada",
      }),
    });
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({
      nivel_seguridad: 2,
      motivo: "motivo suficiente",
      causal_reserva: "art19_b_seguridad_publica",
      fundamento_juridico: "Ley X",
      plazo_reserva_meses: 60,
    });
  });

  it("el 404 es neutro: no distingue inexistente de inalcanzable", async () => {
    globalThis.fetch = vi.fn(async () => new Response("{}", { status: 404 }));
    const res = await solicitarPost({ locals: LOCALS, params: { id: ID }, request: req(valido) });
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("not_found");
  });
});
