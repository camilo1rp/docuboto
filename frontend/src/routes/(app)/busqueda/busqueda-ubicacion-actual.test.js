// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ADR-029 — /busqueda: columna y filtro por UBICACIÓN ACTUAL.
 *
 * El filtro `ubicacion_dept_code` viaja por el proxy y por el `load`, ambos con
 * allowlist: solo un entero positivo llega al gateway (texto libre = 422). El hit y
 * el radicado tienen dependencias distintas a propósito.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
const entorno = vi.hoisted(() => ({ browser: false }));
vi.mock("$app/environment", () => ({
  get browser() {
    return entorno.browser;
  },
  dev: false,
  building: false,
}));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/busqueda") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: Busqueda } = await import("./+page.svelte");
const { GET: proxyGet } = await import("./api/+server.js");
const { load } = await import("./+page.server.js");

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "u-1" } };
const ok = (body) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body });

const HIT = {
  id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-DEMO-E-000117",
  doc_type: "E",
  subject: "Solicitud",
  dest_dept: "Oficina de Tecnología",
  ubicacion_dept_code: 3,
  ubicacion_dept: "Control Interno",
  status: "in_progress",
  registered_at: "2026-08-17T10:00:00+00:00",
};

function pageData(extra = {}) {
  return {
    tab: "exacta",
    results: [HIT],
    total: 1,
    loadError: null,
    dependencias: [
      { codigo: 3, nombre: "Control Interno" },
      { codigo: 7, nombre: "Oficina de Tecnología" },
    ],
    initialFilters: {
      q: "solicitud",
      docType: "",
      status: "",
      dateFrom: "",
      dateTo: "",
      destDeptCode: "",
      ubicacionDeptCode: "",
      page: 1,
      ...extra,
    },
    semanticaResults: null,
    semanticaError: null,
    semanticaInitial: { sq: "", stopk: 5 },
    antecedentesData: null,
    antecedentesError: null,
    antecedentesInitial: { aq: "", atr: "", atopk: 5 },
  };
}

afterEach(() => {
  entorno.browser = false;
  vi.restoreAllMocks();
});

describe("/busqueda — columna", () => {
  it("«Ubicación actual» muestra ubicacion_dept y no la dependencia de radicación", () => {
    render(Busqueda, { data: pageData() });
    expect(screen.getByRole("columnheader", { name: "Ubicación actual" })).toBeTruthy();
    const fila = screen.getByText(HIT.tracking_number).closest("tr");
    expect(within(fila).getByText("Control Interno")).toBeTruthy();
    expect(within(fila).queryByText("Oficina de Tecnología")).toBeNull();
  });

  it("el filtro avanzado distingue ubicación actual (selector) de dependencia de radicación (código)", async () => {
    render(Busqueda, { data: pageData() });
    await fireEvent.click(screen.getByRole("button", { name: /Filtros avanzados/ }));
    const sel = screen.getByLabelText(/^Ubicación actual/);
    expect(sel.tagName).toBe("SELECT");
    expect(screen.queryByLabelText(/Código de ubicación actual/)).toBeNull();
    expect(screen.getByLabelText(/Código de dependencia de radicación/)).toBeTruthy();
  });

  it("el selector ofrece «nombre — código» del catálogo y explica que filtra por código", async () => {
    render(Busqueda, { data: pageData() });
    await fireEvent.click(screen.getByRole("button", { name: /Filtros avanzados/ }));
    const sel = screen.getByLabelText(/^Ubicación actual/);
    const textos = [...sel.querySelectorAll("option")].map((o) => o.textContent.trim());
    expect(textos).toEqual(["Cualquiera", "Control Interno — 3", "Oficina de Tecnología — 7"]);
    expect([...sel.querySelectorAll("option")].map((o) => o.value)).toEqual(["", "3", "7"]);
    expect(document.getElementById("filter-ubicacion-hint").textContent).toMatch(
      /filtra por el código/i,
    );
  });

  it("sin catálogo el selector queda en «Cualquiera» y no rompe", async () => {
    render(Busqueda, { data: { ...pageData(), dependencias: undefined } });
    await fireEvent.click(screen.getByRole("button", { name: /Filtros avanzados/ }));
    const sel = screen.getByLabelText(/^Ubicación actual/);
    expect(sel.querySelectorAll("option").length).toBe(1);
  });

  it("un filtro de ubicación en la URL abre los filtros avanzados con la dependencia elegida", () => {
    render(Busqueda, { data: pageData({ ubicacionDeptCode: "3" }) });
    expect(screen.getByLabelText(/^Ubicación actual/).value).toBe("3");
  });
});

describe("/busqueda — ubicación en móvil", () => {
  function conViewport(estrecho) {
    entorno.browser = true;
    window.matchMedia = vi.fn().mockImplementation((q) => ({
      matches: estrecho,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }));
  }

  it("estrecho: la columna se oculta y la ubicación sube bajo el asunto", async () => {
    conViewport(true);
    render(Busqueda, { data: pageData() });
    const linea = await screen.findByTestId("fila-ubicacion-movil");
    expect(linea.textContent.trim()).toBe("En Control Interno");
    expect(screen.queryByRole("columnheader", { name: "Ubicación actual" })).toBeNull();
  });

  it("ancho: sin duplicado, la columna ya la muestra", () => {
    conViewport(false);
    render(Busqueda, { data: pageData() });
    expect(screen.queryByTestId("fila-ubicacion-movil")).toBeNull();
  });
});

describe("load de /busqueda — catálogo de dependencias", () => {
  const ev = () => ({
    cookies: { get: () => "tok" },
    parent: async () => ({ user: LOCALS.user }),
    url: new URL("http://localhost/busqueda?q=abc"),
  });

  it("lo carga de /dependencias?activo=true con token y tenant", async () => {
    globalThis.fetch = vi.fn(async (u) =>
      String(u).includes("/dependencias")
        ? ok([{ codigo: 3, nombre: "Control Interno" }])
        : ok({ hits: [], total: 0 }),
    );
    const data = await load(ev());
    expect(data.dependencias).toEqual([{ codigo: 3, nombre: "Control Interno" }]);
    const dep = globalThis.fetch.mock.calls.find((c) => String(c[0]).includes("/dependencias"));
    expect(String(dep[0])).toContain("activo=true");
    expect(dep[1].headers["X-Tenant-Slug"]).toBe("icetex");
  });

  it("un fallo degrada a lista vacía sin romper la búsqueda", async () => {
    globalThis.fetch = vi.fn(async (u) => {
      if (String(u).includes("/dependencias")) throw new Error("boom");
      return ok({ hits: [HIT], total: 1 });
    });
    const data = await load(ev());
    expect(data.dependencias).toEqual([]);
    expect(data.results.length).toBe(1);
  });
});

describe("proxy /busqueda/api — ubicacion_dept_code", () => {
  async function params(qs) {
    globalThis.fetch = vi.fn(async () => ok({ hits: [], total: 0 }));
    await proxyGet({ locals: LOCALS, url: new URL(`http://localhost/busqueda/api?q=abc&${qs}`) });
    return new URL(globalThis.fetch.mock.calls[0][0]).searchParams;
  }

  it("reenvía un entero positivo", async () => {
    expect((await params("ubicacion_dept_code=12")).get("ubicacion_dept_code")).toBe("12");
  });

  it("descarta texto libre, negativos y decimales (evita 422)", async () => {
    for (const malo of ["abc", "-3", "1.5", "1;drop", ""]) {
      expect((await params(`ubicacion_dept_code=${malo}`)).has("ubicacion_dept_code"), malo).toBe(
        false,
      );
    }
  });

  it("no mezcla el filtro nuevo con el de radicación", async () => {
    const p = await params("ubicacion_dept_code=5");
    expect(p.has("dest_dept_code")).toBe(false);
  });
});

describe("load de /busqueda — ubicacion_dept_code", () => {
  async function run(qs) {
    globalThis.fetch = vi.fn(async () => ok({ hits: [HIT], total: 1 }));
    const data = await load({
      cookies: { get: () => "tok" },
      parent: async () => ({ user: LOCALS.user }),
      url: new URL(`http://localhost/busqueda?q=abc&${qs}`),
    });
    return { data, sent: new URL(globalThis.fetch.mock.calls[0][0]).searchParams };
  }

  it("lo valida, lo reenvía al gateway y lo devuelve a los filtros iniciales", async () => {
    const { data, sent } = await run("ubicacion_dept_code=9");
    expect(sent.get("ubicacion_dept_code")).toBe("9");
    expect(data.initialFilters.ubicacionDeptCode).toBe("9");
  });

  it("descarta lo que no es entero", async () => {
    const { data, sent } = await run("ubicacion_dept_code=x1");
    expect(sent.has("ubicacion_dept_code")).toBe(false);
    expect(data.initialFilters.ubicacionDeptCode).toBe("");
  });
});
