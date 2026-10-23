// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * trd-c4-page.test.js — /admin/trd tras C-4 (ADR-040): fases como fuente única,
 * productoras por `codigo`, total en vivo de solo lectura, pestaña Cuadro y
 * `publicable` del tipo documental (nivel 3).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/trd") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: TrdPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const sesion = (nivel) =>
  initSession({
    sub: "u-1",
    email: "a@orpyca.com",
    name: "A",
    preferred_username: "a",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["USUA_PERM_TRD"],
    permission_levels: { USUA_PERM_TRD: nivel },
  });

const DEPS = [
  { codigo: 12, nombre: "Secretaría General" },
  { codigo: 40, nombre: "Talento Humano" },
];
const SERIE = {
  id: "s-1",
  code: "10",
  version: 1,
  name: "Actas",
  parent_id: null,
  disposition: "CT",
  archivo_gestion_years: 2,
  archivo_central_years: 8,
  total_retention: 10,
  dependencias_productoras: [
    { dependencia_codigo: 12, dependencia_nombre: "Secretaría General", ruta_convalidada: null },
  ],
  estado: "borrador",
};
const TIPO_PUBLICABLE_INACTIVO = {
  id: "t-1",
  code: "10.1",
  nombre: "Acta pública",
  trd_serie_id: "s-1",
  obligatorio: false,
  activo: false,
  admite_ventanilla_publica: false,
  publicable: true,
};
const data = (extra = {}) => ({
  series: [SERIE],
  seriesError: null,
  tipos: [TIPO_PUBLICABLE_INACTIVO],
  tiposError: null,
  dependencias: DEPS,
  dependenciasError: null,
  ...extra,
});

beforeEach(() => {
  globalThis.fetch = vi.fn(
    async () =>
      new Response(
        JSON.stringify({ secciones: [], avisos: [], sin_seccion: [], fondo: { nombre: "ICETEX" } }),
        { status: 200 },
      ),
  );
});
afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

describe("/admin/trd — fases y productoras (C-4)", () => {
  it("la tabla muestra las dos fases, la productora por nombre y el total derivado", () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    expect(screen.getByText("Secretaría General")).toBeInTheDocument();
    expect(screen.getByText(/2a \/ 8a/)).toBeInTheDocument();
    expect(screen.getByText(/total 10a/)).toBeInTheDocument();
  });

  it("Nueva serie: sin campos legados, con selector de productoras y total en vivo de solo lectura", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("button", { name: "Nueva serie" }));
    expect(screen.queryByLabelText(/retención total/i)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/Años archivo de gestión \(retención\)/i),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: /Dependencias productoras/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Talento Humano/ })).toBeInTheDocument();

    await fireEvent.input(screen.getByLabelText(/Archivo de gestión/), { target: { value: "3" } });
    await fireEvent.input(screen.getByLabelText(/Archivo central/), { target: { value: "4" } });
    expect(screen.getByText(/7 años/)).toBeInTheDocument();
  });

  it("Crear con fases vacías o en 0+0 NO llama al servidor y explica por qué (control: con fases válidas sí)", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("button", { name: "Nueva serie" }));
    await fireEvent.input(screen.getByLabelText(/^Código/), { target: { value: "20" } });
    await fireEvent.input(screen.getByLabelText(/^Nombre/), { target: { value: "Contratos" } });
    const crear = screen.getByRole("button", { name: "Crear serie" });

    await fireEvent.click(crear);
    expect(screen.getByText(/Indique los años/)).toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalledWith("/admin/trd/api", expect.anything());

    await fireEvent.input(screen.getByLabelText(/Archivo de gestión/), { target: { value: "0" } });
    await fireEvent.input(screen.getByLabelText(/Archivo central/), { target: { value: "0" } });
    await fireEvent.click(crear);
    expect(screen.getByText(/al menos 1 año/i)).toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalledWith("/admin/trd/api", expect.anything());

    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            id: "s-2",
            code: "20",
            version: 1,
            name: "Contratos",
            estado: "borrador",
          }),
          { status: 201 },
        ),
    );
    await fireEvent.input(screen.getByLabelText(/Archivo central/), { target: { value: "5" } });
    await fireEvent.click(crear);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/admin/trd/api");
    const cuerpo = JSON.parse(init.body);
    expect(cuerpo.archivo_gestion_years).toBe(0);
    expect(cuerpo.archivo_central_years).toBe(5);
    expect("retention_years" in cuerpo).toBe(false);
    expect("total_retention" in cuerpo).toBe(false);
  });

  it("marcar una productora la envía como entero de dependencias.codigo", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("button", { name: "Nueva serie" }));
    await fireEvent.input(screen.getByLabelText(/^Código/), { target: { value: "20" } });
    await fireEvent.input(screen.getByLabelText(/^Nombre/), { target: { value: "Contratos" } });
    await fireEvent.input(screen.getByLabelText(/Archivo de gestión/), { target: { value: "1" } });
    await fireEvent.input(screen.getByLabelText(/Archivo central/), { target: { value: "2" } });
    await fireEvent.click(screen.getByRole("checkbox", { name: /Talento Humano/ }));
    globalThis.fetch = vi.fn(async () => new Response("{}", { status: 422 }));
    await fireEvent.click(screen.getByRole("button", { name: "Crear serie" }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body).dependencias_productoras).toEqual([
      40,
    ]);
  });
});

describe("/admin/trd — pestaña Cuadro (C-4)", () => {
  it("carga por el BFF con segun=convalidado por defecto", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("tab", { name: "Cuadro" }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(String(globalThis.fetch.mock.calls[0][0])).toContain("/admin/trd/api/cuadro?");
    expect(String(globalThis.fetch.mock.calls[0][0])).toContain("segun=convalidado");
  });

  it("«Incluir borradores» solo con USUA_PERM_TRD (control positivo y negativo)", async () => {
    sesion(3);
    const { unmount } = render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("tab", { name: "Cuadro" }));
    expect(screen.getByRole("checkbox", { name: /Incluir borradores/ })).toBeInTheDocument();
    unmount();
    clearSession();
    initSession({
      sub: "u-2",
      email: "b@o.com",
      name: "B",
      preferred_username: "b",
      roles: [],
      tenant_slug: "icetex",
      permissions: [],
      permission_levels: {},
    });
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("tab", { name: "Cuadro" }));
    expect(screen.queryByRole("checkbox", { name: /Incluir borradores/ })).not.toBeInTheDocument();
  });

  it("muestra avisos arriba, sin ubicar al final, y el 403 como «Sin permiso» (no como vacío)", async () => {
    sesion(3);
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            fondo: { nombre: "ICETEX", sigla: "ICX" },
            secciones: [
              {
                codigo: 1,
                sigla: "SG",
                nombre: "Secretaría",
                activo: true,
                nivel: "seccion",
                series: [
                  {
                    code: "10",
                    name: "Actas",
                    version: 1,
                    estado: "convalidada",
                    archivo_gestion_years: 2,
                    archivo_central_years: 8,
                    subseries: [],
                  },
                ],
                subsecciones: [],
              },
            ],
            sin_seccion: [
              {
                code: "99",
                name: "Huérfana",
                version: 1,
                estado: "migrada",
                motivo: "sin_productora",
              },
            ],
            avisos: [{ tipo: "productora_inactiva", dependencia_codigo: 7 }],
          }),
          { status: 200 },
        ),
    );
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("tab", { name: "Cuadro" }));
    await waitFor(() => expect(screen.getByText(/está inactiva/)).toBeInTheDocument());
    expect(screen.getByText("Huérfana")).toBeInTheDocument();
    expect(screen.getByText(/Sin dependencia productora declarada/)).toBeInTheDocument();

    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "forbidden" }), { status: 403 }),
    );
    await fireEvent.click(screen.getByRole("checkbox", { name: /Incluir borradores/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Sin permiso/));
    expect(screen.queryByText(/Cuadro vacío/)).not.toBeInTheDocument();
  });
});

describe("/admin/trd — tipo documental publicable (C-4)", () => {
  async function abrirEdicion() {
    await fireEvent.click(screen.getByRole("tab", { name: "Tipos documentales" }));
    await fireEvent.click(screen.getByRole("button", { name: "Editar" }));
  }

  it("columna «Publicable (Ley 1712)» separada de «Ventanilla pública»", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await fireEvent.click(screen.getByRole("tab", { name: "Tipos documentales" }));
    expect(screen.getByRole("columnheader", { name: "Publicable (Ley 1712)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ventanilla pública" })).toBeInTheDocument();
  });

  it("nivel 3: casilla Publicable habilitada y «Activo» habilitado", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await abrirEdicion();
    expect(screen.getByRole("checkbox", { name: /Publicable \(Ley 1712\)/ })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /^Activo/ })).toBeEnabled();
  });

  it("nivel 1: Publicable deshabilitada y reactivar un tipo publicable inactivo, también", async () => {
    sesion(1);
    render(TrdPage, { props: { data: data() } });
    await abrirEdicion();
    expect(screen.getByRole("checkbox", { name: /Publicable \(Ley 1712\)/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /^Activo/ })).toBeDisabled();
    expect(screen.getByText(/Reactivar un tipo publicable exige nivel 3/)).toBeInTheDocument();
  });

  it("sin tocar la casilla NO se reenvía publicable; al desmarcarla sí viaja false", async () => {
    sesion(3);
    render(TrdPage, { props: { data: data() } });
    await abrirEdicion();
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ ...TIPO_PUBLICABLE_INACTIVO }), { status: 200 }),
    );
    await fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
    expect("publicable" in JSON.parse(globalThis.fetch.mock.calls[0][1].body)).toBe(false);
    // Esperar a que termine el guardado (el modal se cierra): reabrir antes deja
    // «Guardar» en estado de carga y el segundo clic no hace nada.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull());

    await abrirEdicion();
    await fireEvent.click(screen.getByRole("checkbox", { name: /Publicable \(Ley 1712\)/ }));
    await fireEvent.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
    expect(JSON.parse(globalThis.fetch.mock.calls[1][1].body).publicable).toBe(false);
  });
});
