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
 *  - 01 Radicados pendientes: UNA llamada a /documents con `status` repetido
 *    (distributed e in_progress), con
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
 * @param {{pendientes?: object[], pendientesTotal?: number|null, alertas?: object[], recent?: object[], summaryOk?: boolean}} config
 */
function fakeGatewayFetch({
  pendientes = [],
  pendientesTotal = null,
  alertas = [],
  recent = [],
  summaryOk = true,
  pendientesOk = true,
  alertasOk = true,
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
      // `getAll` y no `get`: la cadena de consulta es un multidict y `get`
      // devuelve solo el primer valor — mirarla con `get` reproduciría dentro
      // del doble el mismo defecto que el gateway tenía al colapsarla.
      const estados = url.searchParams.getAll("status");

      if (estados.length === 1) {
        throw new Error(
          "El tablero volvió a pedir UN estado por llamada. Dos ventanas de N no son " +
            "las N más recientes, y el total salía de sumar dos `X-Total-Count`.",
        );
      }

      if (estados.length > 1) {
        // Las alertas se distinguen por pedir un orden: es su rasgo propio.
        if (url.searchParams.get("order_by") === "due_date") {
          if (!alertasOk) {
            return { ok: false, status: 500, json: async () => ({ detail: "Falla alertas" }) };
          }
          return {
            ok: true,
            status: 200,
            headers: { get: (h) => (h === "X-Total-Count" ? String(alertas.length) : null) },
            json: async () => alertas,
          };
        }
        if (!pendientesOk) {
          return { ok: false, status: 500, json: async () => ({ detail: "Falla pendientes" }) };
        }
        const total = pendientesTotal ?? pendientes.length;
        return {
          ok: true,
          status: 200,
          headers: { get: (h) => (h === "X-Total-Count" ? String(total) : null) },
          json: async () => pendientes,
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
  it("pide los dos estados en UNA llamada y toma el total del servidor", async () => {
    const fetch = fakeGatewayFetch({
      pendientes: [
        makeDoc({ id: "d2", status: "in_progress", registered_at: "2026-01-03T00:00:00Z" }),
        makeDoc({ id: "d1", status: "distributed", registered_at: "2026-01-02T00:00:00Z" }),
      ],
      // El total NO coincide con lo devuelto a propósito: son las 5 visibles de
      // un conjunto mayor. Si el loader volviera a contar `items.length`, el
      // tablero anunciaría como pendiente solo lo que cabe en el módulo.
      pendientesTotal: 37,
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.pendientes.total).toBe(37);
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d2", "d1"]);
    expect(result.pendientes.error).toBeNull();
    // Tres llamadas a /documents: pendientes, alertas y recientes. Con una por
    // estado serían cuatro — y el doble ya no las atiende.
    const aDocumentos = fetch.mock.calls.filter(([u]) => String(u).includes("/documents"));
    expect(aDocumentos).toHaveLength(3);
  });

  it("pide al servidor solo los 5 que caben en el módulo", async () => {
    const fetch = fakeGatewayFetch({ pendientes: [], pendientesTotal: 6 });

    await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    const pedido = fetch.mock.calls
      .map(([u]) => new URL(u))
      .find((u) => u.searchParams.getAll("status").length > 1 && !u.searchParams.get("order_by"));

    // El recorte lo hace la base, no el cliente: traer 20 por estado para
    // enseñar 5 es pedir cuatro veces lo que se usa, y la parte descartada es
    // justamente la que decide qué se ve.
    expect(pedido.searchParams.get("size")).toBe("5");
    expect(pedido.searchParams.getAll("status")).toEqual(["distributed", "in_progress"]);
  });

  it("si la llamada de pendientes falla, expone error y no rompe el resto de módulos", async () => {
    const fetch = fakeGatewayFetch({ pendientesOk: false });

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
      pendientes: [makeDoc({ id: "d1" })],
      recentOk: false,
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.recientes.error).toBeTruthy();
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d1"]);
  });
});

describe("dashboard/+page.server.js#load — módulo 03 Alertas", () => {
  it("son una CONSULTA PROPIA ordenada por plazo, no un subproducto de los pendientes", async () => {
    const fetch = fakeGatewayFetch({ alertas: [] });

    await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    const pedido = fetch.mock.calls
      .map(([u]) => new URL(u))
      .find((u) => u.searchParams.get("order_by") === "due_date");

    // Es el corazón del arreglo: derivarlas de la ventana de pendientes —los
    // más RECIENTES— dejaba fuera al que lleva más tiempo vencido, que es
    // exactamente el que una alerta busca. Se radicó antes, luego salía después.
    expect(pedido).toBeTruthy();
    expect(pedido.searchParams.get("order_dir")).toBe("asc");
    expect(pedido.searchParams.getAll("status")).toEqual(["distributed", "in_progress"]);
  });

  it("respeta el orden que da el servidor y traduce los días que faltan", async () => {
    const now = Date.now();
    const iso = (deltaDays) => new Date(now + deltaDays * 24 * 60 * 60 * 1000).toISOString();

    // Llegan ya ordenadas por el backend. El loader NO las reordena: hacerlo
    // sobre una ventana de 5 daría un orden correcto dentro de una selección
    // equivocada, que es el error que se acaba de quitar.
    const fetch = fakeGatewayFetch({
      alertas: [
        makeDoc({ id: "vencido", due_date: iso(-2) }),
        makeDoc({ id: "vence-en-1", due_date: iso(1) }),
        makeDoc({ id: "vence-en-10", due_date: iso(10) }),
      ],
    });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.alertas.items.map((d) => d.id)).toEqual(["vencido", "vence-en-1", "vence-en-10"]);
    expect(result.alertas.items[0].diasRestantes).toBeLessThan(0);
  });

  it("un radicado sin due_date no se pinta aunque llegue", async () => {
    // El `NULLS LAST` del backend ya impide que llegue; la vista no depende de
    // esa garantía para no mostrar «NaN días».
    const fetch = fakeGatewayFetch({ alertas: [makeDoc({ id: "sin-fecha", due_date: null })] });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.alertas.items).toEqual([]);
    expect(result.alertas.error).toBeNull();
  });

  it("un fallo de alertas no arrastra a los pendientes, y se dice cuál falló", async () => {
    // Antes compartían llamada y por tanto compartían error: si fallaba, los
    // dos módulos culpaban a lo mismo aunque uno de ellos hubiera respondido.
    const fetch = fakeGatewayFetch({ alertasOk: false, pendientes: [makeDoc({ id: "d1" })] });

    const result = await load({ cookies: { get: () => "tok" }, parent: parentWithUser(), fetch });

    expect(result.alertas.error).toBeTruthy();
    expect(result.pendientes.error).toBeNull();
    expect(result.pendientes.items.map((d) => d.id)).toEqual(["d1"]);
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
      pendientes: [makeDoc({ id: "d1" })],
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
      pendientes: [makeDoc({ id: "d1" })],
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
