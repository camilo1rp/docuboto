// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros — tarjeta «Capa de conocimiento» (C-1, ADR-037).
 * Editable solo con USUA_PERM_ADMIN >= 3; el backend re-valida.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

const invalidateAll = vi.fn(() => Promise.resolve());
vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/parametros") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: ParametrosPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sesion(nivel) {
  initSession({
    sub: "u-1",
    name: "Admin",
    tenant_slug: "demo",
    permissions: ["USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_ADMIN: nivel },
  });
}

const datos = (extra = {}) => ({
  params: {},
  holidays: [],
  paramsError: null,
  holidaysError: null,
  asistenteHabilitado: false,
  asistenteError: null,
  ...extra,
});

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
  invalidateAll.mockClear();
});

describe("/admin/parametros — tarjeta de la capa de conocimiento", () => {
  it.each([1, 2])("con administración %s el interruptor está deshabilitado y no hay botón", (n) => {
    sesion(n);
    render(ParametrosPage, { props: { data: datos() } });
    expect(screen.getByRole("switch")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /aplicar cambio/i })).toBeNull();
    expect(screen.getByTestId("asistente-solo-lectura").textContent).toMatch(/nivel 3/);
  });

  it("con administración 3 el interruptor se puede cambiar y aplicar (control positivo)", async () => {
    sesion(3);
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ habilitado: true, cambiado: true }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    render(ParametrosPage, { props: { data: datos() } });

    const sw = screen.getByRole("switch");
    expect(sw).not.toBeDisabled();
    const aplicar = screen.getByRole("button", { name: /aplicar cambio/i });
    expect(aplicar).toBeDisabled(); // sin cambios no hay nada que aplicar

    await fireEvent.click(sw);
    // encender exige la declaración: sin ella no se puede aplicar
    expect(aplicar).toBeDisabled();
    await fireEvent.click(screen.getByLabelText(/Declaro que la entidad tiene contrato/));
    await fireEvent.input(screen.getByLabelText(/Motivo/), { target: { value: "  piloto  " } });
    expect(aplicar).not.toBeDisabled();
    await fireEvent.click(aplicar);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/admin/parametros/api/asistente");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({
      habilitado: true,
      declaracion_transmision: true,
      motivo: "piloto",
    });
    await waitFor(() => expect(invalidateAll).toHaveBeenCalled());
  });

  it("muestra el aviso del backend al reactivar", async () => {
    sesion(3);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            habilitado: true,
            cambiado: true,
            aviso: "revise los grupos con PERM_CONOCIMIENTO",
          }),
          { status: 200 },
        ),
    );
    render(ParametrosPage, { props: { data: datos() } });
    await fireEvent.click(screen.getByRole("switch"));
    await fireEvent.click(screen.getByLabelText(/Declaro que la entidad tiene contrato/));
    await fireEvent.click(screen.getByRole("button", { name: /aplicar cambio/i }));
    expect((await screen.findByTestId("asistente-aviso")).textContent).toMatch(/revise los grupos/);
  });

  it("un 403 del servidor no se da por aplicado", async () => {
    sesion(3);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            detail: { error: "forbidden", detail: "Se necesita nivel 3", status: 403 },
          }),
          { status: 403 },
        ),
    );
    render(ParametrosPage, { props: { data: datos() } });
    await fireEvent.click(screen.getByRole("switch"));
    await fireEvent.click(screen.getByLabelText(/Declaro que la entidad tiene contrato/));
    await fireEvent.click(screen.getByRole("button", { name: /aplicar cambio/i }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(invalidateAll).not.toHaveBeenCalled();
    expect(screen.queryByTestId("asistente-aviso")).toBeNull();
  });

  it("indica el estado actual y, si no se pudo leer, no inventa «desactivado»", () => {
    sesion(3);
    const { unmount } = render(ParametrosPage, {
      props: { data: datos({ asistenteHabilitado: true }) },
    });
    expect(screen.getByTestId("asistente-estado").textContent).toMatch(/activado/);
    unmount();
    render(ParametrosPage, {
      props: { data: datos({ asistenteHabilitado: null, asistenteError: "Error 500" }) },
    });
    expect(screen.queryByTestId("asistente-estado")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText("Error 500")).toBeTruthy();
  });

  it("describe la capa de conocimiento, sin asistente conversacional ni indexación", () => {
    sesion(3);
    render(ParametrosPage, { props: { data: datos() } });
    const card = screen.getByTestId("asistente-card").textContent.replace(/\s+/g, " ");
    expect(card).toMatch(/Capa de conocimiento/);
    expect(card).toMatch(/búsqueda por conocimiento y de antecedentes/);
    expect(card).toMatch(/Docuboto no llama a modelos de lenguaje/);
    expect(card).toMatch(/PERM_CONOCIMIENTO se concedió a los grupos que ya tenían algún permiso/);
    expect(screen.getByRole("switch", { name: /Activar la capa de conocimiento/ })).toBeTruthy();
    expect(card).not.toMatch(/asistente conversacional,|indexa|embeddings|anthropic|EE\. UU\./i);
    expect(screen.queryByTestId("asistente-proveedores")).toBeNull();
  });

  it("apagar no pide declaración y envía solo {habilitado:false}", async () => {
    sesion(3);
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ habilitado: false, cambiado: true }), { status: 200 }),
    );
    globalThis.fetch = fetchMock;
    render(ParametrosPage, { props: { data: datos({ asistenteHabilitado: true }) } });
    await fireEvent.click(screen.getByRole("switch"));
    expect(screen.queryByTestId("asistente-declaracion")).toBeNull();
    await fireEvent.click(screen.getByRole("button", { name: /aplicar cambio/i }));
    await waitFor(() => expect(invalidateAll).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ habilitado: false });
  });

  it.each([
    ["declaracion_requerida", 422, /declaración/],
    ["politica_no_publicada", 409, /política de tratamiento/],
  ])("el error %s se explica y no se da por aplicado", async (codigo, status) => {
    sesion(3);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { error: codigo, detail: "x", status } }), {
          status,
        }),
    );
    render(ParametrosPage, { props: { data: datos() } });
    await fireEvent.click(screen.getByRole("switch"));
    await fireEvent.click(screen.getByLabelText(/Declaro que la entidad tiene contrato/));
    await fireEvent.click(screen.getByRole("button", { name: /aplicar cambio/i }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(invalidateAll).not.toHaveBeenCalled();
  });
});
