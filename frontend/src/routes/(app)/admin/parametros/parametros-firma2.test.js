// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * FIRMA-2 en /admin/parametros: (1) un parámetro no admite `null` (el backend responde
 * 422 `valor_nulo`; no existe «volver a heredar»), y (2) los festivos —crear, borrar,
 * sembrar— exigen `USUA_PERM_ADMIN` nivel 3, igual que los parámetros.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({
  goto: vi.fn(),
  invalidateAll: vi.fn(() => Promise.resolve()),
}));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/parametros") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: ParametrosPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { toasts } = await import("$lib/stores/notifications.js");
const { get } = await import("svelte/store");

const data = () => ({
  params: { plazo_dias: 15 },
  holidays: [{ id: "h-1", fecha: "2026-01-01", descripcion: "Año Nuevo", is_national: true }],
  loadError: null,
});

function sesion(nivel) {
  initSession({
    sub: "u-1",
    name: "Admin",
    tenant_slug: "demo",
    roles: [],
    permissions: ["USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_ADMIN: nivel },
  });
}

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/admin/parametros — valor null", () => {
  it("crear un parámetro con valor null o vacío no llama al servidor", async () => {
    sesion(3);
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    render(ParametrosPage, { props: { data: data() } });
    await fireEvent.change(screen.getByLabelText(/^clave/i), {
      target: { value: "entidad_sigla" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /crear parámetro/i }));
    expect(screen.getByText(/el valor es obligatorio/i)).toBeTruthy();
    await fireEvent.input(screen.getByLabelText(/valor \(json\)/i), { target: { value: "null" } });
    await fireEvent.click(screen.getByRole("button", { name: /crear parámetro/i }));
    expect(screen.getByText(/no puede quedar vacío/i)).toBeTruthy();
    expect(f).not.toHaveBeenCalled();
  });

  it("la clave nueva es un selector de las admitidas que aún no existen", () => {
    sesion(3);
    render(ParametrosPage, { props: { data: { ...data(), params: { entidad_sigla: "X" } } } });
    const opciones = [...screen.getByLabelText(/^clave/i).querySelectorAll("option")].map(
      (o) => o.value,
    );
    expect(opciones).toContain("entidad_nombre");
    expect(opciones).not.toContain("entidad_sigla");
    expect(opciones).not.toContain("plazo_dias");
  });

  it("un 422 config_param_desconocido se explica en español", async () => {
    sesion(3);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        json: async () => ({ detail: { error: "config_param_desconocido", detail: "x" } }),
      })),
    );
    render(ParametrosPage, { props: { data: data() } });
    await fireEvent.change(screen.getByLabelText(/^clave/i), {
      target: { value: "entidad_sigla" },
    });
    await fireEvent.input(screen.getByLabelText(/valor \(json\)/i), { target: { value: "1" } });
    await fireEvent.click(screen.getByRole("button", { name: /crear parámetro/i }));
    await waitFor(() =>
      expect(get(toasts).some((t) => /no es configurable/i.test(t.message))).toBe(true),
    );
  });

  it("un 422 valor_nulo del servidor se explica en español", async () => {
    sesion(3);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        json: async () => ({ detail: { error: "valor_nulo", detail: "x" } }),
      })),
    );
    render(ParametrosPage, { props: { data: data() } });
    await fireEvent.change(screen.getByLabelText(/^clave/i), {
      target: { value: "entidad_sigla" },
    });
    await fireEvent.input(screen.getByLabelText(/valor \(json\)/i), { target: { value: "1" } });
    await fireEvent.click(screen.getByRole("button", { name: /crear parámetro/i }));
    await waitFor(() =>
      expect(get(toasts).some((t) => /no se puede dejar sin valor/i.test(t.message))).toBe(true),
    );
  });
});

describe("/admin/parametros — festivos exigen administración 3", () => {
  const irAFestivos = () => fireEvent.click(screen.getByRole("tab", { name: /festivos/i }));

  it("nivel 3 ofrece crear, eliminar y precargar", async () => {
    sesion(3);
    render(ParametrosPage, { props: { data: data() } });
    await irAFestivos();
    expect(screen.getByRole("button", { name: /crear festivo/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /eliminar/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /precargar festivos/i })).toBeTruthy();
  });

  it("nivel 2 solo consulta: ni crear, ni eliminar, ni precargar", async () => {
    sesion(2);
    render(ParametrosPage, { props: { data: data() } });
    await irAFestivos();
    expect(screen.getByText("Año Nuevo")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /crear festivo/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /eliminar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /precargar festivos/i })).toBeNull();
  });
});
