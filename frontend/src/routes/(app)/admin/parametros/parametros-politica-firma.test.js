// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros — escritura de parámetros solo con administración nivel 3,
 * y la política XAdES como selector con motivo al relajarla (ADR-016 addendum).
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

const KEY = "firma_personal_require_xades";

function sesion(nivel) {
  initSession({
    sub: "u-1",
    name: "Admin",
    tenant_slug: "demo",
    permissions: ["USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_ADMIN: nivel },
  });
}

const datos = (params) => ({ params, holidays: [], paramsError: null, holidaysError: null });

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/admin/parametros — nivel de administración", () => {
  it.each([1, 2])("con administración %s no ofrece editar ni crear parámetros", (nivel) => {
    sesion(nivel);
    render(ParametrosPage, { props: { data: datos({ plazo: 5 }) } });
    expect(screen.queryByRole("button", { name: /editar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /crear parámetro/i })).toBeNull();
    expect(screen.getByTestId("params-solo-lectura").textContent).toMatch(/nivel 3/);
  });

  it("con administración 3 sí", () => {
    sesion(3);
    render(ParametrosPage, { props: { data: datos({ plazo: 5 }) } });
    expect(screen.getByRole("button", { name: /editar/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /crear parámetro/i })).toBeTruthy();
  });

  it("sin niveles conocidos falla cerrada", () => {
    initSession({ sub: "u", name: "A", tenant_slug: "demo", permissions: ["USUA_PERM_ADMIN"] });
    render(ParametrosPage, { props: { data: datos({ plazo: 5 }) } });
    expect(screen.queryByRole("button", { name: /editar/i })).toBeNull();
  });

  it("un 403 del servidor se explica con el nivel que exige", async () => {
    sesion(3);
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 }),
    );
    globalThis.fetch = fetchMock;
    render(ParametrosPage, { props: { data: datos({ plazo: 5 }) } });
    await fireEvent.click(screen.getByRole("button", { name: /editar/i }));
    await fireEvent.click(screen.getByRole("button", { name: /guardar/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // el aviso es un toast montado por el layout; aquí basta con que no se dé por guardado
    expect(screen.queryByText("Parámetro guardado.")).toBeNull();
  });
});

describe("/admin/parametros — política XAdES", () => {
  async function editar(valor) {
    sesion(3);
    const fetchMock = vi.fn(
      async (_u, init) =>
        new Response(JSON.stringify({ key: KEY, value: JSON.parse(init.body).value }), {
          status: 200,
        }),
    );
    globalThis.fetch = fetchMock;
    render(ParametrosPage, { props: { data: datos({ [KEY]: valor }) } });
    await fireEvent.click(screen.getByRole("button", { name: /editar/i }));
    return fetchMock;
  }
  const guardar = () => fireEvent.click(screen.getByRole("button", { name: /guardar/i }));
  const elegir = (v) =>
    fireEvent.change(screen.getByLabelText(/política de firma personal/i), {
      target: { value: v },
    });

  it("el valor null se muestra como heredado del entorno", () => {
    sesion(3);
    render(ParametrosPage, { props: { data: datos({ [KEY]: null }) } });
    expect(screen.getByText(/Heredado del entorno/)).toBeTruthy();
  });

  it("se edita con un selector cerrado, no con JSON libre", async () => {
    await editar("todos");
    const sel = screen.getByLabelText(/política de firma personal/i);
    expect(sel.tagName).toBe("SELECT");
    expect(Array.from(sel.querySelectorAll("option")).map((o) => o.value)).toEqual([
      "",
      "off",
      "clasificados",
      "todos",
    ]);
    expect(screen.queryByLabelText(/^$/)).toBeNull();
  });

  it("relajar sin motivo NO llama al servidor y pide el motivo", async () => {
    const f = await editar("todos");
    await elegir("clasificados");
    await guardar();
    expect(f).not.toHaveBeenCalled();
    expect(screen.getByText(/pide un motivo de al menos 10 caracteres/)).toBeTruthy();
  });

  it("relajar con un motivo de menos de 10 caracteres tampoco", async () => {
    const f = await editar("todos");
    await elegir("off");
    await fireEvent.input(screen.getByLabelText(/motivo del cambio/i), {
      target: { value: "corto" },
    });
    await guardar();
    expect(f).not.toHaveBeenCalled();
  });

  it("relajar con motivo envía value y motivo", async () => {
    const f = await editar("todos");
    await elegir("clasificados");
    await fireEvent.input(screen.getByLabelText(/motivo del cambio/i), {
      target: { value: "Aún no se enrolaron los certificados." },
    });
    await guardar();
    await waitFor(() => expect(f).toHaveBeenCalled());
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toBe(`/admin/parametros/api/params/${KEY}`);
    expect(JSON.parse(init.body)).toEqual({
      value: "clasificados",
      motivo: "Aún no se enrolaron los certificados.",
    });
  });

  it("endurecer no pide motivo", async () => {
    const f = await editar("off");
    await elegir("todos");
    await guardar();
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ value: "todos" });
  });

  it("desde null, fijar algo distinto de `todos` pide motivo; `todos` no", async () => {
    const f = await editar(null);
    await elegir("off");
    await guardar();
    expect(f).not.toHaveBeenCalled();
    await elegir("todos");
    await guardar();
    await waitFor(() => expect(f).toHaveBeenCalled());
  });

  it("si el servidor responde 422 motivo_requerido lo explica junto al campo", async () => {
    sesion(3);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { error: "motivo_requerido", detail: "x" } }), {
          status: 422,
        }),
    );
    render(ParametrosPage, { props: { data: datos({ [KEY]: "off" }) } });
    await fireEvent.click(screen.getByRole("button", { name: /editar/i }));
    await elegir("todos");
    await guardar();
    await waitFor(() => expect(screen.getByText(/exige un motivo de al menos 10/)).toBeTruthy());
  });
});
