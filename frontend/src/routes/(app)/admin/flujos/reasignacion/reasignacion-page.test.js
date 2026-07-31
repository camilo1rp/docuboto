// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reasignacion-page.test.js — Render de /admin/flujos/reasignacion.
 *
 * Cubre el punto central de esta pantalla (acción masiva e irreversible,
 * RF-FLU-08): el botón "Ejecutar reasignación" permanece deshabilitado hasta
 * que el administrador SELECCIONA origen + destino distintos Y TECLEA la
 * frase de confirmación exacta — un clic en el botón antes de eso no debe
 * disparar ningún fetch.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

const { default: ReasignacionPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const USERS = [
  { id: "11111111-1111-1111-1111-111111111111", username: "ana" },
  { id: "22222222-2222-2222-2222-222222222222", username: "beto" },
];

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

describe("/admin/flujos/reasignacion — sin USUA_PERM_ADMIN", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });

  it("muestra un EmptyState y no ofrece el formulario", () => {
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });
    expect(screen.getByText(/sin permiso de administración/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ejecutar reasignación" })).toBeNull();
  });
});

describe("/admin/flujos/reasignacion — con USUA_PERM_ADMIN", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("el botón de ejecución arranca deshabilitado (sin origen/destino/confirmación)", () => {
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });
    const execBtn = screen.getByRole("button", { name: "Ejecutar reasignación" });
    expect(execBtn).toBeDisabled();
  });

  it("con origen y destino elegidos pero SIN teclear la confirmación, sigue deshabilitado y un clic no llama al proxy", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });

    // Origen: agrega a "ana" en el picker de origen.
    const addAnaButtons = screen.getAllByRole("button", { name: /agregar a ana/i });
    await fireEvent.click(addAnaButtons[0]);
    // Destino: agrega a "beto" en el picker de destino.
    const addBetoButtons = screen.getAllByRole("button", { name: /agregar a beto/i });
    await fireEvent.click(addBetoButtons[addBetoButtons.length - 1]);

    const execBtn = screen.getByRole("button", { name: "Ejecutar reasignación" });
    expect(execBtn).toBeDisabled();

    await fireEvent.click(execBtn);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("escribir una confirmación incorrecta (minúsculas, texto libre) NO habilita el botón", async () => {
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });

    await fireEvent.click(screen.getAllByRole("button", { name: /agregar a ana/i })[0]);
    const addBetoButtons = screen.getAllByRole("button", { name: /agregar a beto/i });
    await fireEvent.click(addBetoButtons[addBetoButtons.length - 1]);

    const confirmInput = screen.getByLabelText(/escriba "REASIGNAR"/i);
    await fireEvent.input(confirmInput, { target: { value: "reasignar" } });

    expect(screen.getByRole("button", { name: "Ejecutar reasignación" })).toBeDisabled();

    await fireEvent.input(confirmInput, { target: { value: "sí, confirmo" } });
    expect(screen.getByRole("button", { name: "Ejecutar reasignación" })).toBeDisabled();
  });

  it("origen y destino con la MISMA persona: el botón permanece deshabilitado aunque se teclee la confirmación", async () => {
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });

    await fireEvent.click(screen.getAllByRole("button", { name: /agregar a ana/i })[0]);
    // Selecciona a "ana" también como destino (mismo usuario en ambos picker).
    const addAnaButtons = screen.getAllByRole("button", { name: /agregar a ana/i });
    await fireEvent.click(addAnaButtons[addAnaButtons.length - 1]);

    await fireEvent.input(screen.getByLabelText(/escriba "REASIGNAR"/i), {
      target: { value: "REASIGNAR" },
    });

    expect(screen.getByText(/el origen y el destino no pueden ser el mismo usuario/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ejecutar reasignación" })).toBeDisabled();
  });

  it("origen + destino distintos + confirmación exacta: habilita el botón y ejecuta con el payload esperado", async () => {
    const fetchMock = vi.fn(async (input) => {
      const u = String(input);
      if (u.startsWith("/admin/flujos/reasignacion/api")) {
        return new Response(JSON.stringify({ reassigned: 4 }), { status: 200 });
      }
      throw new Error(`fetch no esperado: ${u}`);
    });
    globalThis.fetch = fetchMock;

    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });

    await fireEvent.click(screen.getAllByRole("button", { name: /agregar a ana/i })[0]);
    const addBetoButtons = screen.getAllByRole("button", { name: /agregar a beto/i });
    await fireEvent.click(addBetoButtons[addBetoButtons.length - 1]);
    await fireEvent.input(screen.getByLabelText(/escriba "REASIGNAR"/i), {
      target: { value: "REASIGNAR" },
    });

    const execBtn = screen.getByRole("button", { name: "Ejecutar reasignación" });
    expect(execBtn).not.toBeDisabled();

    await fireEvent.click(execBtn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body).toEqual({
      from_assigned_to: USERS[0].id,
      to_assigned_to: USERS[1].id,
      confirm: "REASIGNAR",
    });

    // A12 — el resultado recibe el foco (no se pierde tras la acción irreversible).
    await waitFor(() => expect(screen.getByText(/se reasignaron 4/i)).toBe(document.activeElement));
  });

  it("botón deshabilitado: explica qué requisito falta (sin origen/destino/confirmación)", () => {
    render(ReasignacionPage, { props: { data: { users: USERS, loadError: null } } });
    const missing = document.getElementById("reasig-missing");
    expect(missing).toBeTruthy();
    expect(missing.textContent).toMatch(/seleccione el origen/i);
    expect(missing.textContent).toMatch(/seleccione el destino/i);
    expect(missing.textContent).toMatch(/escriba "REASIGNAR"/i);
  });

  it("origen por dependencia: es un desplegable del catálogo, no texto libre (A10)", async () => {
    const DEPENDENCIAS = [{ id: "d1", nombre: "Tesorería", sigla: "TES", activo: true }];
    render(ReasignacionPage, {
      props: { data: { users: USERS, loadError: null, dependencias: DEPENDENCIAS } },
    });

    await fireEvent.click(screen.getByRole("radio", { name: /por dependencia/i }));

    const select = screen.getByLabelText("Dependencia de origen");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: /TES — Tesorería/i })).toBeTruthy();
    // La ayuda ya no menciona el nombre de la columna del backend.
    expect(screen.queryByText(/to_dept/i)).toBeNull();
  });
});
