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
  await fireEvent.change(screen.getByLabelText("Dependencia de destino", { exact: false }), {
    target: { value: "7" },
  });
}

const DEPENDENCIAS = [{ codigo: 7, sigla: "TIC", nombre: "Tecnología" }];

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
    render(ReglasPage, {
      props: { data: { reglas: [], loadError: null, dependencias: DEPENDENCIAS } },
    });

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
    render(ReglasPage, {
      props: { data: { reglas: [], loadError: null, dependencias: DEPENDENCIAS } },
    });

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
    render(ReglasPage, {
      props: { data: { reglas: [], loadError: null, dependencias: DEPENDENCIAS } },
    });

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
    render(ReglasPage, {
      props: { data: { reglas: [], loadError: null, dependencias: DEPENDENCIAS } },
    });

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

describe("/admin/flujos/reglas — dependencia por código (ADR-032)", () => {
  const originalFetch = globalThis.fetch;
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("envía assign_to_dept_code (entero) y no el nombre", async () => {
    let enviado = null;
    globalThis.fetch = vi.fn(async (_u, init) => {
      enviado = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "r-1" }), { status: 201 });
    });
    render(ReglasPage, {
      props: { data: { reglas: [], loadError: null, dependencias: DEPENDENCIAS } },
    });
    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Crear regla" }));
    await fireEvent.click(await screen.findByRole("button", { name: "Confirmar y guardar" }));
    await waitFor(() => expect(enviado).not.toBeNull());
    expect(enviado.assign_to_dept_code).toBe(7);
    expect(enviado.assign_to_dept).toBeUndefined();
  });

  it("la tabla muestra el nombre vigente por código, no la instantánea", () => {
    render(ReglasPage, {
      props: {
        data: {
          reglas: [
            {
              id: "r-1",
              name: "Regla A",
              priority: 1,
              is_active: true,
              conditions: [],
              match_mode: "all",
              assign_to_dept: "Nombre viejo",
              assign_to_dept_code: 7,
            },
          ],
          loadError: null,
          dependencias: DEPENDENCIAS,
        },
      },
    });
    expect(screen.getAllByText("Tecnología").length).toBeGreaterThan(0);
    expect(screen.queryByText("Nombre viejo")).toBeNull();
  });
});

describe("/admin/flujos/reglas — operadores y valor por campo (ADR-032)", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });
  afterEach(() => vi.restoreAllMocks());

  async function conCondicion() {
    render(ReglasPage, {
      props: {
        data: {
          reglas: [],
          loadError: null,
          dependencias: DEPENDENCIAS,
          dependenciasTodas: [...DEPENDENCIAS, { codigo: 9, nombre: "Vieja", activo: false }],
        },
      },
    });
    await openCreateModal();
    await fireEvent.click(screen.getByRole("button", { name: "Agregar condición" }));
  }

  it("un campo de código solo ofrece eq/ne/in y el valor es un select de dependencias", async () => {
    await conCondicion();
    await fireEvent.change(screen.getByLabelText("Campo"), { target: { value: "dest_dept_code" } });
    const ops = [...screen.getByLabelText("Operador").querySelectorAll("option")].map(
      (o) => o.value,
    );
    expect(ops).toEqual(["eq", "ne", "in"]);
    await waitFor(() => expect(screen.getByLabelText("Valor").tagName).toBe("SELECT"));
  });

  it("un campo por nombre solo ofrece contains/regex", async () => {
    await conCondicion();
    await fireEvent.change(screen.getByLabelText("Campo"), { target: { value: "dest_dept" } });
    await waitFor(() => {
      const ops = [...screen.getByLabelText("Operador").querySelectorAll("option")].map(
        (o) => o.value,
      );
      expect(ops).toEqual(["contains", "regex"]);
    });
  });

  it("al pasar de texto a select el valor tecleado se limpia", async () => {
    await conCondicion();
    await fireEvent.input(screen.getByLabelText("Valor"), { target: { value: "oficio" } });
    await fireEvent.change(screen.getByLabelText("Campo"), { target: { value: "dest_dept_code" } });
    await waitFor(() => expect(screen.getByLabelText("Valor").value).toBe(""));
  });

  it("«in» sobre un código es un grupo de casillas con las dependencias activas", async () => {
    await conCondicion();
    await fireEvent.change(screen.getByLabelText("Campo"), { target: { value: "dest_dept_code" } });
    await fireEvent.change(screen.getByLabelText("Operador"), { target: { value: "in" } });
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Tecnología/ })).toBeTruthy());
  });
});
