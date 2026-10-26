// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * `/bandeja?radicado=<uuid>` abre el detalle de ESE radicado.
 *
 * El defecto que cierra se vio buscando una tutela: el resultado ofrecía «Ver en
 * bandeja», el enlace era `/bandeja` a secas, y quien lo pulsaba aterrizaba en
 * la bandeja de su propia dependencia — donde el documento buscado no estaba,
 * porque una bandeja lista lo que esa dependencia tramita, no lo que se acaba de
 * buscar. No había error ni aviso: la pantalla simplemente mostraba otra cosa.
 * El `aria-label` del enlace decía «Ver radicado X en la bandeja», así que el
 * enlace anunciaba una cosa y hacía otra.
 *
 * El detalle se pide a `/bandeja/api/{id}`, que resuelve cualquier radicado y
 * aplica sus propias reglas de acceso — por eso funciona aunque el radicado no
 * esté en la lista cargada.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
let currentPageStore;

vi.mock("$app/navigation", () => ({ goto: (...args) => gotoMock(...args) }));
vi.mock("$app/stores", () => {
  currentPageStore = writable({ url: new URL("http://localhost/bandeja") });
  return { page: currentPageStore, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { clearSession } = await import("$lib/stores/auth.js");

const ID = "d86a41ee-b044-4bfc-a9bb-1314e7adf6c7";

function baseData(overrides = {}) {
  return {
    documents: [],
    total: 0,
    loadError: null,
    vista: "lista",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;

/** Devuelve las URLs pedidas, para ver QUÉ se consultó. */
function espiarFetch(detalle) {
  const pedidas = [];
  globalThis.fetch = vi.fn(async (url) => {
    pedidas.push(String(url));
    if (String(url).includes(`/bandeja/api/${ID}`) && !String(url).includes("/current")) {
      return new Response(JSON.stringify(detalle), { status: 200 });
    }
    return new Response(JSON.stringify({ documents: [], total: 0 }), { status: 200 });
  });
  return pedidas;
}

beforeEach(() => {
  gotoMock.mockClear();
  currentPageStore.set({ url: new URL("http://localhost/bandeja") });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

describe("/bandeja?radicado= — el enlace lleva REALMENTE al radicado", () => {
  it("con el parámetro, se pide el detalle de ese radicado", async () => {
    currentPageStore.set({ url: new URL(`http://localhost/bandeja?radicado=${ID}`) });
    const pedidas = espiarFetch({
      id: ID,
      tracking_number: "2026-DEMO-E-000001",
      subject: "Tutela de Juan Perez sobre certificado",
      status: "in_progress",
      doc_type: "E",
    });

    render(BandejaPage, { props: { data: baseData() } });

    await waitFor(() =>
      expect(
        pedidas.some((u) => u.includes(`/bandeja/api/${ID}`)),
        `no se pidió el detalle; se pidió: ${pedidas.join(", ")}`,
      ).toBe(true),
    );
  });

  it("el número del radicado pedido aparece en pantalla", async () => {
    // Gemelo del anterior: que se pida el detalle no basta — el defecto se veía
    // precisamente porque la pantalla mostraba otra cosa sin quejarse.
    currentPageStore.set({ url: new URL(`http://localhost/bandeja?radicado=${ID}`) });
    espiarFetch({
      id: ID,
      tracking_number: "2026-DEMO-E-000001",
      subject: "Tutela de Juan Perez sobre certificado",
      status: "in_progress",
      doc_type: "E",
    });

    const { findAllByText } = render(BandejaPage, { props: { data: baseData() } });
    const encontrados = await findAllByText(/2026-DEMO-E-000001/);
    expect(encontrados.length).toBeGreaterThan(0);
  });

  it("SIN el parámetro no se pide ningún detalle", async () => {
    // Control negativo: abrir un drawer que nadie pidió sería el defecto
    // opuesto, y dejaría este test pasando por accidente.
    const pedidas = espiarFetch({});
    render(BandejaPage, { props: { data: baseData() } });
    await new Promise((r) => setTimeout(r, 50));
    expect(pedidas.filter((u) => /\/bandeja\/api\/[0-9a-f-]{36}/.test(u))).toEqual([]);
  });

  it("navegar de un radicado a OTRO cambia el detalle", async () => {
    // La primera versión de esta corrección leía el parámetro en `onMount`, y
    // eso solo funciona la primera vez: SvelteKit reutiliza el componente al
    // navegar dentro de la misma ruta. Ir de un radicado a otro dejaba en
    // pantalla el detalle del anterior mientras la URL decía otra cosa — que es
    // exactamente el defecto que esta pantalla venía a cerrar, reaparecido un
    // paso más adelante. Se detectó navegando en el navegador, no aquí: por eso
    // el caso queda escrito.
    const OTRO = "36f313b3-6f32-45a7-93ac-308abad9a12f";
    currentPageStore.set({ url: new URL(`http://localhost/bandeja?radicado=${ID}`) });
    const pedidas = [];
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url);
      pedidas.push(u);
      if (u.includes(`/bandeja/api/${ID}`) && !u.includes("/current")) {
        return new Response(JSON.stringify({ id: ID, tracking_number: "2026-DEMO-E-000001" }), {
          status: 200,
        });
      }
      if (u.includes(`/bandeja/api/${OTRO}`) && !u.includes("/current")) {
        return new Response(JSON.stringify({ id: OTRO, tracking_number: "2026-DEMO-E-000014" }), {
          status: 200,
        });
      }
      return new Response(JSON.stringify({ documents: [], total: 0 }), { status: 200 });
    });

    const { findAllByText } = render(BandejaPage, { props: { data: baseData() } });
    await findAllByText(/2026-DEMO-E-000001/);

    currentPageStore.set({ url: new URL(`http://localhost/bandeja?radicado=${OTRO}`) });
    const encontrados = await findAllByText(/2026-DEMO-E-000014/);
    expect(encontrados.length).toBeGreaterThan(0);
  });
});
