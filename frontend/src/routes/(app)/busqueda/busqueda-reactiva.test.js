// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La búsqueda tiene que MOSTRAR lo que el servidor encuentra.
 *
 * Buscar navega con `goto()` a la misma ruta con otros parámetros: SvelteKit
 * reutiliza el componente y solo sustituye la prop `data`. La pantalla hacía
 * `let results = data.results ?? []` —una asignación de una sola vez—, así que
 * el valor quedaba congelado en el del primer render.
 *
 * El efecto era total y silencioso: quien entraba por el menú y buscaba veía
 * SIEMPRE «Sin resultados», mientras el gateway respondía 200 con las
 * coincidencias correctas. Solo funcionaba recargando la página entera con la
 * consulta ya puesta en la URL, que no es como se usa un buscador. En un
 * sistema de gestión documental, la búsqueda es la pantalla más usada.
 *
 * Este test monta la página y **cambia `data`**, que es exactamente lo que hace
 * una navegación del cliente. Un test que solo comprobara el primer render
 * pasaría con el defecto puesto.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/environment", () => ({ browser: false, dev: false, building: false }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/busqueda") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: Busqueda } = await import("./+page.svelte");

const HIT = {
  id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-DEMO-E-000014",
  doc_type: "E",
  subject: "Solicitud de certificación laboral [demo]",
  sender_name: "María Fernanda Ruiz",
  sender_entity: null,
  dest_dept: "Oficina Jurídica",
  status: "in_progress",
  registered_at: "2026-08-17T10:00:00+00:00",
  rank: 0.42,
};

/** El `data` que entrega el `load` de la ruta. */
function pageData({ q = "", results = [], total = 0 } = {}) {
  return {
    tab: "exacta",
    results,
    total,
    loadError: null,
    initialFilters: {
      q,
      docType: "",
      status: "",
      dateFrom: "",
      dateTo: "",
      destDeptCode: "",
      page: 1,
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
  vi.restoreAllMocks();
});

describe("/busqueda — los resultados siguen a la navegación", () => {
  it("una búsqueda hecha desde la propia pantalla muestra sus resultados", async () => {
    // Primer render: se llega por el menú, sin consulta. Es el estado en el que
    // el componente se monta y en el que quedaba congelado.
    const { component } = render(Busqueda, { data: pageData() });

    expect(screen.queryByText(/2026-DEMO-E-000014/)).toBeNull();

    // Buscar: `goto()` re-ejecuta el load y SvelteKit sustituye `data` sobre el
    // MISMO componente. Esto es la navegación, no un montaje nuevo.
    await component.$set({
      data: pageData({ q: "certificación laboral", results: [HIT], total: 1 }),
    });

    await waitFor(() => {
      expect(screen.getByText(/2026-DEMO-E-000014/)).toBeTruthy();
    });
    expect(screen.getByText(/Solicitud de certificación laboral/)).toBeTruthy();
  });

  it("y una búsqueda posterior sin coincidencias limpia las anteriores", async () => {
    // Gemelo: reaccionar solo «hacia arriba» —rellenar cuando hay resultados y
    // no vaciar cuando no los hay— dejaría en pantalla los aciertos de la
    // consulta anterior como si fueran de la nueva. Sobre un buscador de
    // radicados eso es peor que no mostrar nada.
    const { component } = render(Busqueda, {
      data: pageData({ q: "certificación laboral", results: [HIT], total: 1 }),
    });

    await waitFor(() => expect(screen.getByText(/2026-DEMO-E-000014/)).toBeTruthy());

    await component.$set({ data: pageData({ q: "zzzz", results: [], total: 0 }) });

    await waitFor(() => {
      expect(screen.queryByText(/2026-DEMO-E-000014/)).toBeNull();
    });
  });
});
