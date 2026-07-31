// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reglas-page.test.js — Render de /admin/flujos/reglas.
 *
 * Cubre el hallazgo crítico de la revisión UX: `buildConditionsPayload`
 * descartaba en silencio una condición sin valor (o TODAS, produciendo una
 * regla "atrapa-todo" sin que el administrador se enterara). `saveRule` debe
 * ahora bloquear el guardado con una condición sin valor, y exigir
 * confirmación explícita cuando no queda ninguna condición.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

const { default: ReglasPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-admin",
    email: "admin@orpyca.com",
    name: "Admin",
    preferred_username: "admin",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

async function openCreateModal() {
  await fireEvent.click(screen.getByRole("button", { name: "Nueva regla" }));
  await fireEvent.input(screen.getByLabelText("Nombre", { exact: false }), {
    target: { value: "Circulares TIC" },
  });
  await fireEvent.input(screen.getByLabelText("Dependencia de destino", { exact: false }), {
    target: { value: "TIC" },
  });
}

describe("/admin/flujos/reglas — validación de condiciones al guardar", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("una condición sin valor bloquea el guardado y no llama al proxy", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
    render(ReglasPage, { props: { data: { reglas: [], loadError: null } } });

    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Agregar condición" }));

    await fireEvent.click(screen.getByRole("button", { name: "Crear regla" }));

    expect(await screen.findByText(/la condición 1 no tiene valor/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("una condición con valor SÍ permite guardar (sin pedir confirmación de 'sin condiciones')", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ id: "r-1" }), { status: 201 }),
    );
    globalThis.fetch = fetchMock;
    render(ReglasPage, { props: { data: { reglas: [], loadError: null } } });

    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Agregar condición" }));
    await fireEvent.input(screen.getByLabelText("Valor"), { target: { value: "E" } });

    await fireEvent.click(screen.getByRole("button", { name: "Crear regla" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText(/regla sin condiciones/i)).toBeNull();
  });

  it("guardar sin ninguna condición exige confirmación explícita antes de llamar al proxy", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ id: "r-1" }), { status: 201 }),
    );
    globalThis.fetch = fetchMock;
    render(ReglasPage, { props: { data: { reglas: [], loadError: null } } });

    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Crear regla" }));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(await screen.findByText(/regla sin condiciones/i)).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Confirmar y guardar" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it("quitar una condición intermedia no reasocia por accidente los valores de la restante (clave por id, no por índice)", async () => {
    const fetchMock = vi.fn(async (input, init) => {
      return new Response(JSON.stringify({ id: "r-1", ...JSON.parse(init.body) }), { status: 201 });
    });
    globalThis.fetch = fetchMock;
    render(ReglasPage, { props: { data: { reglas: [], loadError: null } } });

    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Agregar condición" }));
    await fireEvent.click(screen.getByRole("button", { name: "Agregar condición" }));

    const valueInputs = screen.getAllByLabelText("Valor");
    await fireEvent.input(valueInputs[0], { target: { value: "primero" } });
    await fireEvent.input(valueInputs[1], { target: { value: "segundo" } });

    // Quita la primera fila — la segunda ("segundo") debe conservar su valor.
    const removeButtons = screen.getAllByRole("button", { name: /quitar condición/i });
    await fireEvent.click(removeButtons[0]);

    expect(screen.getAllByLabelText("Valor")).toHaveLength(1);
    expect(screen.getByLabelText("Valor")).toHaveValue("segundo");
  });
});
