// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * AppLayout.test.js — Búsqueda global del topbar (Sistema de Diseño §9).
 *
 * Cubre:
 *  - El formulario de búsqueda navega a /busqueda?q=... reutilizando la carga
 *    SSR real de esa ruta (nunca llama al gateway desde aquí — se mockea
 *    `goto` de `$app/navigation`, patrón estándar del proyecto).
 *  - Query vacío navega a /busqueda sin el parámetro `q`.
 *  - El texto se recorta (trim) antes de construir la URL.
 *  - Accesibilidad: `role="search"`, label asociado, botón de alternar en
 *    móvil con `aria-expanded` real.
 *  - El alternador móvil expande/enfoca el campo sin ahogar el topbar.
 *
 * `$app/stores` se mockea porque SidebarNav (montado dentro de AppLayout) lo
 * necesita para resolver la ruta activa — mismo patrón que SidebarNav.test.js.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
vi.mock("$app/navigation", () => ({ goto: gotoMock }));

vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/dashboard") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: AppLayout } = await import("./AppLayout.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { resetFavorites } = await import("$lib/stores/favorites.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

describe("AppLayout — búsqueda global del topbar", () => {
  beforeEach(() => {
    gotoMock.mockReset();
    window.localStorage.clear();
    resetFavorites();
    initSession(sessionWith());
  });

  afterEach(() => {
    clearSession();
  });

  it("es un role=search con label asociado (visible u oculta) al campo", () => {
    render(AppLayout);

    const searchForm = screen.getByRole("search", {
      name: "Búsqueda global de radicados y expedientes",
    });
    expect(searchForm).toBeInTheDocument();
    expect(
      screen.getByLabelText("Buscar radicados, expedientes o dependencias"),
    ).toBeInTheDocument();
  });

  it("enviar un texto navega a /busqueda?q=<texto> codificado", async () => {
    render(AppLayout);

    const input = screen.getByLabelText("Buscar radicados, expedientes o dependencias");
    await fireEvent.input(input, { target: { value: "2024-ICETEX-E-001234" } });
    await fireEvent.submit(screen.getByRole("search", { name: /Búsqueda global/ }));

    expect(gotoMock).toHaveBeenCalledWith("/busqueda?q=2024-ICETEX-E-001234");
  });

  it("recorta espacios antes de construir la URL", async () => {
    render(AppLayout);

    const input = screen.getByLabelText("Buscar radicados, expedientes o dependencias");
    await fireEvent.input(input, { target: { value: "  bandeja  " } });
    await fireEvent.submit(screen.getByRole("search", { name: /Búsqueda global/ }));

    expect(gotoMock).toHaveBeenCalledWith("/busqueda?q=bandeja");
  });

  it("enviar vacío navega a /busqueda sin parámetro q", async () => {
    render(AppLayout);

    await fireEvent.submit(screen.getByRole("search", { name: /Búsqueda global/ }));

    expect(gotoMock).toHaveBeenCalledWith("/busqueda");
  });

  it("codifica caracteres especiales del texto de búsqueda", async () => {
    render(AppLayout);

    const input = screen.getByLabelText("Buscar radicados, expedientes o dependencias");
    await fireEvent.input(input, { target: { value: "juan&maría" } });
    await fireEvent.submit(screen.getByRole("search", { name: /Búsqueda global/ }));

    expect(gotoMock).toHaveBeenCalledWith(`/busqueda?q=${encodeURIComponent("juan&maría")}`);
  });

  it("el alternador móvil arranca cerrado (aria-expanded=false) y se expande al pulsarlo", async () => {
    render(AppLayout);

    const toggle = screen.getByRole("button", { name: "Abrir búsqueda" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await fireEvent.click(toggle);

    expect(screen.getByRole("button", { name: "Cerrar búsqueda" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
