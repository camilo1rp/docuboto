// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dashboard-server.test.js — +page.server.js del panel principal (T-07, §10).
 *
 * Cubre el rediseño del tablero a los cuatro módulos personales:
 *  - 01 Radicados pendientes: dos llamadas a /documents (status=distributed
 *    e in_progress), mezcladas y ordenadas por registered_at desc, con
 *    total = suma de X-Total-Count de ambas.
 *  - 02 Actividad reciente: una llamada sin filtro de estado.
 *  - 03 Alertas: derivadas de `due_date` de los radicados abiertos, con
 *    `diasRestantes` calculado y orden ascendente (más urgente primero).
 *  - Métricas agregadas (sección secundaria): /reports/radicados, sin cambios
 *    de contrato frente a la versión anterior.
 *  - Degradación independiente por módulo: un fetch caído no debe tumbar los
 *    otros tres.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

// `permissions` incluye SGD_PERM_ESTADISTICA por defecto (el permiso que gatea
// la sección secundaria de métricas, espejo del RBAC — ver describe de
// "permisos" más abajo para el caso sin ese permiso).
const parentWithUser =
  (tenant_slug = "icetex", permissions = ["SGD_PERM_ESTADISTICA"]) =>
  async () => ({ user: { tenant_slug, permissions } });

/** Construye un documento mínimo de /documents. */
function makeDoc(overrides = {}) {
  return {
    id: "doc-1",
    tracking_number: "2026-ICETEX-E-000001",
    doc_type: "E",
    subject: "Asunto de prueba",
    status: "distributed",
    registered_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    due_date: null,
    ...overrides,
  };
}

const SUMMARY_BODY = {
  total: 10,
  by_doc_type: { E: 6, S: 3, I: 1 },
  by_status: { registered: 2, distributed: 3, in_progress: 4, closed: 1 },
  by_month: [{ month: "2026-01", count: 10 }],
  by_dependencia: { Tesorería: 4 },
};

/**
 * Fake fetch que responde según el status querystring de /documents y
 * siempre resuelve /reports/radicados con SUMMARY_BODY.
 * @param {{distributed?: object[], in_progress?: object[], recent?: object[], summaryOk?: boolean}} config
 */
function fakeGatewayFetch({
  distributed = [],
  in_progress = [],
  recent = [],
  summaryOk = true,
  distributedOk = true,
  inProgressOk = true,
  recentOk = true,
} = {}) {
  return vi.fn(async (input) => {
    const url = new URL(input);

    if (url.pathname.endsWith("/reports/radicados")) {
      if (!summaryOk) {
        return { ok: false, status: 500, json: async () => ({ detail: "Falla reportes" }) };
      }
      return {
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => SUMMARY_BODY,
      };
    }

    if (url.pathname.endsWith("/documents")) {
      const status = url.searchParams.get("status");

      if (status === "distributed") {
        if (!distributedOk) {
          return { ok: false, status: 500, json: async () => ({ detail: "Falla distributed" }) };
        }
        return {
          ok: true,
          status: 200,
          headers: { get: (h) => (h === "X-Total-Count" ? String(distributed.length) : null) },
          json: async () => distributed,
        };
      }

      if (status === "in_progress") {
        if (!inProgressOk) {
          return { ok: false, status: 500, json: async () => ({ detail: "Falla in_progress" }) };
        }
        return {
          ok: true,
          status: 200,
          headers: { get: (h) => (h === "X-Total-Count" ? String(in_progress.length) : null) },
          json: async () => in_progress,
        };
      }

      // Sin filtro de estado -> actividad reciente
      if (!recentOk) {
        return { ok: false, status: 500, json: async () => ({ detail: "Falla recientes" }) };
      }
      return {
        ok: true,
        status: 200,
        headers: { get: (h) => (h === "X-Total-Count" ? String(recent.length) : null) },
        json: async () => recent,
      };
    }

    throw new Error(`URL no esperada en el test: ${input}`);
  });
}

describe("dashboard/+page.server.js#load — módulo 01 Radicados pendientes", () => {
  it("mezcla distributed + in_progress y suma los totales de ambos", async () => {
    const fetch = fakeGatewayFetch({
      distributed: [
        makeDoc({ id: "d1", status: "distributed", registered_at: "2026-01-02T00:00:00Z" }),
      ],
      in_progress: [
        makeDoc({ id: "d2", status: "in_progress", registered_at: "2026-01-03T00:00:00Z" }),
      ],
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.pendientes.total).toBe(2);
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d2", "d1"]); // más reciente primero
    expect(result.pendientes.error).toBeNull();
  });

  it("limita a 5 ítems visibles aunque haya más pendientes", async () => {
    const distributed = Array.from({ length: 6 }, (_, i) =>
      makeDoc({
        id: `d${i}`,
        registered_at: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
      }),
    );
    const fetch = fakeGatewayFetch({ distributed });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.pendientes.items).toHaveLength(5);
    expect(result.pendientes.total).toBe(6);
  });

  it("si ambas llamadas de estado fallan, expone error y no rompe el resto de módulos", async () => {
    const fetch = fakeGatewayFetch({ distributedOk: false, inProgressOk: false });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.pendientes.error).toBeTruthy();
    expect(result.pendientes.items).toEqual([]);
    expect(result.summary).toEqual(SUMMARY_BODY); // el resto sigue cargando
  });
});

describe("dashboard/+page.server.js#load — módulo 02 Actividad reciente", () => {
  it("usa /documents sin filtro de estado", async () => {
    const recent = [makeDoc({ id: "r1", status: "closed" })];
    const fetch = fakeGatewayFetch({ recent });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.recientes.items.map((d) => d.id)).toEqual(["r1"]);
    expect(result.recientes.error).toBeNull();
  });

  it("un fallo en recientes no afecta a pendientes", async () => {
    const fetch = fakeGatewayFetch({
      distributed: [makeDoc({ id: "d1" })],
      recentOk: false,
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.recientes.error).toBeTruthy();
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d1"]);
  });
});

describe("dashboard/+page.server.js#load — módulo 03 Alertas (derivadas de due_date)", () => {
  it("solo incluye radicados abiertos CON due_date, ordenados por urgencia (más próximo/vencido primero)", async () => {
    const now = Date.now();
    const iso = (deltaDays) => new Date(now + deltaDays * 24 * 60 * 60 * 1000).toISOString();

    const distributed = [
      makeDoc({ id: "sin-fecha", due_date: null }),
      makeDoc({ id: "vence-en-10", due_date: iso(10) }),
      makeDoc({ id: "vencido", due_date: iso(-2) }),
      makeDoc({ id: "vence-en-1", due_date: iso(1) }),
    ];
    const fetch = fakeGatewayFetch({ distributed });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.alertas.items.map((d) => d.id)).toEqual(["vencido", "vence-en-1", "vence-en-10"]);
    expect(result.alertas.items[0].diasRestantes).toBeLessThan(0);
    expect(result.alertas.items.some((d) => d.id === "sin-fecha")).toBe(false);
  });

  it("sin ningún due_date entre los abiertos, alertas queda vacío sin error", async () => {
    const fetch = fakeGatewayFetch({ distributed: [makeDoc({ due_date: null })] });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.alertas.items).toEqual([]);
    expect(result.alertas.error).toBeNull();
  });
});

describe("dashboard/+page.server.js#load — sección secundaria (métricas agregadas)", () => {
  it("carga /reports/radicados igual que antes del rediseño", async () => {
    const fetch = fakeGatewayFetch({});

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.summary).toEqual(SUMMARY_BODY);
    expect(result.summaryError).toBeNull();
  });

  it("un fallo de reportes no afecta a los módulos personales", async () => {
    const fetch = fakeGatewayFetch({
      distributed: [makeDoc({ id: "d1" })],
      summaryOk: false,
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.summary).toBeNull();
    expect(result.summaryError).toBeTruthy();
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d1"]);
  });
});

describe("dashboard/+page.server.js#load — espejo del RBAC (SGD_PERM_ESTADISTICA)", () => {
  it("sin el permiso, no llama a /reports/radicados y summary/summaryError quedan null", async () => {
    const fetch = fakeGatewayFetch({
      distributed: [makeDoc({ id: "d1" })],
      // Si el load llamara igual a /reports/radicados, fakeGatewayFetch lo
      // resolvería con SUMMARY_BODY — la aserción de abajo distingue "no
      // se llamó" de "se llamó y falló".
    });

    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser("icetex", []),
      fetch,
    });

    expect(result.summary).toBeNull();
    expect(result.summaryError).toBeNull();
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d1"]); // no bloquea el resto
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/reports/radicados"),
      expect.anything(),
    );
  });

  it("con el permiso, sigue cargando /reports/radicados normalmente", async () => {
    const fetch = fakeGatewayFetch({});

    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser("icetex", ["SGD_PERM_ESTADISTICA"]),
      fetch,
    });

    expect(result.summary).toEqual(SUMMARY_BODY);
    expect(result.summaryError).toBeNull();
  });
});

describe("dashboard/+page.server.js#load — fallo total de conexión", () => {
  it("un gateway inalcanzable expone error en los cuatro módulos sin lanzar", async () => {
    const fetch = vi.fn(() => {
      throw new Error("connect ECONNREFUSED");
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.pendientes.error).toBeTruthy();
    expect(result.recientes.error).toBeTruthy();
    expect(result.summaryError).toBeTruthy();
    expect(result.pendientes.items).toEqual([]);
    expect(result.recientes.items).toEqual([]);
    expect(result.alertas.items).toEqual([]);
  });
});
