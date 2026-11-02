// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/inteligencia — el acto de pasar a local ofrece retirar los datos,
 * y en local se ve cuántos radicados conserva el proveedor.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { invalidateAll } from "$app/navigation";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn() }));

const { default: Page } = await import("./+page.svelte");

const VISTA = {
  provider: { id: "example-provider", name: "Example Provider" },
  subprocessors: [{ id: "model-host-a", name: "Example Model Host" }],
  routing: [
    {
      level: "public",
      hosting: "hosted",
      model_aliases: ["triage-small"],
      subprocessors: ["model-host-a"],
      prompt_and_output_retention: "none",
      trains_on_customer_data: false,
    },
    {
      level: "reserved",
      hosting: "installation",
      model_aliases: ["drafting-large"],
      subprocessors: [],
      prompt_and_output_retention: "none",
      trains_on_customer_data: false,
    },
  ],
};

function datos(extra = {}) {
  return {
    config: {
      provider: "local",
      levels: [1],
      send_metadata: false,
      suspended: false,
    },
    configError: null,
    capabilities: VISTA,
    capabilitiesError: null,
    inventory: { records: 4, complete: false },
    inventoryError: null,
    ...extra,
  };
}

afterEach(() => {
  invalidateAll.mockClear();
  vi.restoreAllMocks();
});

describe("/admin/inteligencia", () => {
  it("en local dice cuántos radicados conserva el proveedor", () => {
    render(Page, { props: { data: datos() } });
    expect(screen.getByTestId("inventario").textContent).toMatch(/al menos 4/);
    expect(screen.getByTestId("estado").textContent).toMatch(/índice local/);
  });

  it("sin declaración no deja pasar al proveedor", async () => {
    render(Page, {
      props: {
        data: datos({
          capabilities: null,
          capabilitiesError: "No se pudo leer la declaración del proveedor.",
          inventory: null,
        }),
      },
    });
    await fireEvent.click(screen.getByRole("radio", { name: "Proveedor PPI" }));
    expect(screen.getByTestId("capabilities-error").textContent).toMatch(/no puede pasar/);
    expect(screen.queryByTestId("declaracion-proveedor")).toBeNull();
    expect(screen.getByRole("button", { name: "Aplicar proveedor" })).toBeDisabled();
  });

  it("volver a local ofrece retirar también los datos", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url, init) => {
      llamadas.push({ url, body: init?.body ? JSON.parse(init.body) : null, method: init?.method });
      return new Response(JSON.stringify({ provider: "local", purge_ref: "p-1" }), { status: 200 });
    });
    render(Page, {
      props: {
        data: datos({
          config: { provider: "ppi", levels: [1], send_metadata: false, suspended: false },
          inventory: null,
        }),
      },
    });
    await fireEvent.click(screen.getByRole("radio", { name: "Índice local" }));
    await fireEvent.click(screen.getByRole("button", { name: "Aplicar proveedor" }));
    const dialogo = screen.getByRole("dialog", { name: "Confirmar" });
    expect(dialogo.textContent).toMatch(/Retirar también los datos/);
    const casilla = screen.getByRole("checkbox", { name: "Retirar también los datos" });
    await fireEvent.click(casilla);
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    await waitFor(() => expect(invalidateAll).toHaveBeenCalled());
    expect(llamadas.map((c) => c.url)).toEqual([
      "/admin/inteligencia/api",
      "/admin/inteligencia/api/retiro",
    ]);
    expect(llamadas[0].body).toEqual({ provider: "local" });
    expect(llamadas[0].body.transmission_statement).toBeUndefined();
  });

  it("sin la casilla, volver a local no pide el borrado", async () => {
    const urls = [];
    globalThis.fetch = vi.fn(async (url) => {
      urls.push(url);
      return new Response(JSON.stringify({ provider: "local" }), { status: 200 });
    });
    render(Page, {
      props: {
        data: datos({
          config: { provider: "ppi", levels: [1, 2], send_metadata: false, suspended: true },
          inventory: null,
        }),
      },
    });
    expect(screen.getByTestId("estado").textContent).toMatch(/suspendido/);
    await fireEvent.click(screen.getByRole("radio", { name: "Índice local" }));
    await fireEvent.click(screen.getByRole("button", { name: "Aplicar proveedor" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    await waitFor(() => expect(invalidateAll).toHaveBeenCalled());
    expect(urls).toEqual(["/admin/inteligencia/api"]);
  });

  it("habilitar RESERVADA manda la declaración de esa ruta", async () => {
    /** @type {object | null} */
    let cuerpo = null;
    globalThis.fetch = vi.fn(async (_url, init) => {
      cuerpo = JSON.parse(init.body);
      return new Response(JSON.stringify({ levels: [1, 2], purge: "not_applicable" }), {
        status: 200,
      });
    });
    render(Page, {
      props: {
        data: datos({
          config: { provider: "ppi", levels: [1], send_metadata: false, suspended: false },
          inventory: null,
        }),
      },
    });
    const campo = screen.getByLabelText("Declaración de RESERVADA");
    expect(campo.value).toContain("installation");
    expect(campo.value).toContain("drafting-large");
    await fireEvent.click(screen.getByRole("button", { name: "Habilitar RESERVADA" }));
    expect(cuerpo).toMatchObject({ level: 2, enabled: true });
    expect(cuerpo.statement).toContain("drafting-large");
  });
});
