// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * usuarios-page.test.js — Render de /admin/usuarios (+page.svelte).
 *
 * Cubre el defecto que destapó ESLint al desbloquearse: al extraer
 * CatalogCrudPanel, el botón "Nuevo usuario" pasó a abrir el modal por
 * `bind:createOpen` y nadie volvió a llamar a `openCreate()`, que era quien
 * limpiaba el formulario. El modal reabría con lo escrito antes.
 *
 * Aquí importa más que en las otras seis pantallas: si un ROOT marca la casilla
 * "Otorgar ROOT", cancela y vuelve a abrir para dar de alta a otra persona, la
 * casilla seguía marcada. Es un camino silencioso a conceder ROOT sin querer.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/usuarios") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: UsuariosPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function baseData(overrides = {}) {
  return {
    users: [{ id: "u-1", username: "administradora", email: "admin@orpyca.com", is_root: true }],
    callerIsRoot: true,
    groups: [],
    dependencias: [],
    loadError: null,
    ...overrides,
  };
}

beforeEach(() => {
  initSession({
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_ADMIN: 3 },
  });
});

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

async function abrirModalCreacion() {
  await fireEvent.click(screen.getByRole("button", { name: /nuevo usuario/i }));
  await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
}

describe("/admin/usuarios — el formulario de alta reabre limpio", () => {
  it("no conserva la casilla ROOT marcada tras cancelar", async () => {
    render(UsuariosPage, { props: { data: baseData() } });

    await abrirModalCreacion();
    const root = screen.getByLabelText(/otorgar root/i);
    await fireEvent.click(root);
    expect(root.checked).toBe(true);

    await fireEvent.click(screen.getByRole("button", { name: /^cancelar$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await abrirModalCreacion();
    expect(screen.getByLabelText(/otorgar root/i).checked).toBe(false);
  });

  it("no conserva el nombre de usuario ni el correo escritos antes", async () => {
    render(UsuariosPage, { props: { data: baseData() } });

    await abrirModalCreacion();
    await fireEvent.input(screen.getByLabelText(/nombre de usuario/i), {
      target: { value: "borrador-abandonado" },
    });
    await fireEvent.input(screen.getByLabelText(/correo/i), {
      target: { value: "quien@sea.com" },
    });

    await fireEvent.click(screen.getByRole("button", { name: /^cancelar$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await abrirModalCreacion();
    expect(screen.getByLabelText(/nombre de usuario/i).value).toBe("");
    expect(screen.getByLabelText(/correo/i).value).toBe("");
  });

  it("no conserva el error de validación del intento anterior", async () => {
    render(UsuariosPage, { props: { data: baseData() } });

    await abrirModalCreacion();
    await fireEvent.click(screen.getByRole("button", { name: /crear usuario/i }));
    await waitFor(() => expect(screen.getByText(/nombre de usuario es obligatorio/i)).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: /^cancelar$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await abrirModalCreacion();
    expect(screen.queryByText(/nombre de usuario es obligatorio/i)).toBeNull();
  });
});
