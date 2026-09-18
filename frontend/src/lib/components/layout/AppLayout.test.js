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
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";
import { tick } from "svelte";

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

describe("AppLayout — cierre de sesión", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetFavorites();
    initSession(sessionWith());
  });

  afterEach(() => {
    clearSession();
  });

  it("el control de cerrar sesión es un botón de un formulario POST, no un enlace", () => {
    // Era `<a href="/logout">` y el efecto vivía en el `load` de esa ruta. Con
    // `data-sveltekit-preload-data="hover"` en app.html, SvelteKit precarga el
    // `load` al pasar el ratón por encima: rozar el icono cerraba la sesión.
    render(AppLayout);

    const boton = screen.getByRole("button", { name: "Cerrar sesión" });
    const form = boton.closest("form");

    expect(form).not.toBeNull();
    expect(form.getAttribute("method").toUpperCase()).toBe("POST");
    expect(form.getAttribute("action")).toBe("/logout");
  });

  it("no queda ningún enlace apuntando a /logout", () => {
    // Aserción de AUSENCIA: es la que se rompe si alguien reintroduce el `<a>`.
    const { container } = render(AppLayout);

    expect(container.querySelector('a[href="/logout"]')).toBeNull();
  });
});

describe("AppLayout — aviso de permisos degradados", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetFavorites();
  });

  afterEach(() => {
    clearSession();
  });

  it("no muestra nada cuando los permisos cargaron bien", () => {
    initSession(sessionWith());
    render(AppLayout);

    expect(screen.queryByText(/No pudimos cargar tus permisos/)).toBeNull();
  });

  it("avisa cuando /auth/me no respondió, en vez de dejar el menú vacío en silencio", () => {
    // Sin el aviso, el menú vacío es indistinguible de «me revocaron los
    // permisos»: el funcionario llama al administrador, que ve sus permisos
    // intactos y no encuentra nada que arreglar.
    initSession({ ...sessionWith(), permisosDegradados: true });
    render(AppLayout);

    expect(screen.getByText(/No pudimos cargar tus permisos/)).toBeInTheDocument();
  });

  it("el aviso es un role=status: los lectores de pantalla lo anuncian sin robar el foco", () => {
    initSession({ ...sessionWith(), permisosDegradados: true });
    render(AppLayout);

    expect(screen.getByRole("status")).toHaveTextContent(/No pudimos cargar tus permisos/);
  });
});

describe("AppLayout — el atajo «/» no saca el foco de un diálogo modal", () => {
  beforeEach(() => {
    gotoMock.mockReset();
    window.localStorage.clear();
    resetFavorites();
    initSession(sessionWith());
  });

  afterEach(() => {
    clearSession();
    document.querySelectorAll("[data-modal-de-prueba]").forEach((n) => n.remove());
  });

  it("sin nada modal abierto, «/» enfoca el buscador", async () => {
    // Control positivo: sin él, una guarda que bloqueara siempre dejaría el
    // test de abajo en verde con el atajo muerto para todo el mundo.
    //
    // El foco se pone tras un `tick()` —el campo móvil se expande primero—, así
    // que se espera a que ocurra en vez de leerlo en el mismo latido.
    render(AppLayout);
    fireEvent.keyDown(document, { key: "/" });

    const campo = screen.getByRole("searchbox", { name: /buscar/i });
    await waitFor(() => expect(document.activeElement).toBe(campo));
  });

  it("con un [aria-modal] abierto, «/» no mueve el foco", async () => {
    render(AppLayout);

    // Lo que abre el asistente, el drawer de navegación o cualquier Modal:
    // dentro de un diálogo modal el foco NO puede salir, y «/» lo mandaba al
    // buscador del topbar — un sitio que el diálogo declara inexistente para
    // quien navega con teclado o con lector de pantalla.
    const dialogo = document.createElement("div");
    dialogo.setAttribute("role", "dialog");
    dialogo.setAttribute("aria-modal", "true");
    dialogo.setAttribute("data-modal-de-prueba", "");
    const dentro = document.createElement("button");
    dialogo.appendChild(dentro);
    document.body.appendChild(dialogo);
    dentro.focus();

    fireEvent.keyDown(document, { key: "/" });
    // El foco se mueve tras un `tick()`, así que comprobarlo en el mismo latido
    // pasa con guarda y sin ella: la mutación que quita la guarda NO caía. Hay
    // que dejar correr los microtasks y mirar DESPUÉS.
    await tick();
    await tick();

    expect(document.activeElement).toBe(dentro);
  });
});
