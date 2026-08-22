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

// ---------------------------------------------------------------------------
// El detalle no puede AFIRMAR lo que no llegó a leer
// ---------------------------------------------------------------------------
//
// `openDetail` lanza dos peticiones en paralelo (permisos efectivos y URD) y
// compartían una sola variable de error con un `else if (!detailError)`: si la
// primera fallaba, el fallo de la segunda se descartaba, `urdList` se quedaba
// en `[]` y la sección pintaba «Sin dependencias asignadas» — una afirmación
// FALSA sobre datos que nunca se leyeron.
//
// Visto en vivo con un 429 del limitador del gateway: el panel decía que
// `operador` no tenía ninguna dependencia asignada mientras la base tenía su
// URD 901 marcada como principal. Un administrador que se lo crea le asigna
// otra encima. Es el mismo defecto que `/anulaciones` ya corrigió en su estado
// vacío de búsqueda (caso A4 de este mismo repositorio).

function respuesta(ok, cuerpo) {
  return Promise.resolve({ ok, status: ok ? 200 : 429, json: async () => cuerpo });
}

async function abrirDetalle() {
  await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
  await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
}

describe("/admin/usuarios — el detalle distingue «vacío» de «no se pudo leer»", () => {
  it("con la URD caída NO dice que el usuario no tenga dependencias", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url) =>
        String(url).includes("/urd")
          ? respuesta(false, {})
          : respuesta(true, { is_root: false, permissions: { PERM_RADI: 3 } }),
      ),
    );
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await waitFor(() =>
      expect(screen.getByText(/no se pudieron cargar las dependencias/i)).toBeTruthy(),
    );
    // La aserción que importa: la frase falsa NO aparece.
    expect(screen.queryByText(/sin dependencias asignadas/i)).toBeNull();
  });

  it("el fallo de la URD sobrevive aunque los permisos TAMBIÉN fallen", async () => {
    // Este es el caso exacto que se dio: los dos 429 a la vez. Con la variable
    // compartida, el segundo mensaje se perdía por completo.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => respuesta(false, {})),
    );
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await waitFor(() =>
      expect(screen.getByText(/no se pudieron cargar las dependencias/i)).toBeTruthy(),
    );
    expect(screen.getByText(/no se pudieron cargar los permisos efectivos/i)).toBeTruthy();
    expect(screen.queryByText(/sin dependencias asignadas/i)).toBeNull();
  });

  it("cuando la URD SÍ carga y viene vacía, entonces sí lo dice", async () => {
    // Control positivo: sin él, un componente que nunca mostrara el estado
    // vacío dejaría los dos tests de arriba en verde y la pantalla sin informar
    // de que a esa persona no le han asignado unidad.
    vi.stubGlobal(
      "fetch",
      vi.fn((url) =>
        String(url).includes("/urd")
          ? respuesta(true, [])
          : respuesta(true, { is_root: false, permissions: {} }),
      ),
    );
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await waitFor(() => expect(screen.getByText(/sin dependencias asignadas/i)).toBeTruthy());
    expect(screen.queryByText(/no se pudieron cargar las dependencias/i)).toBeNull();
  });
});

describe("/admin/usuarios — restablecimiento del segundo factor", () => {
  /**
   * El acto que cierra el encierro de quien pierde el autenticador. Lo que se
   * comprueba aquí es lo que hace que sea seguro ofrecerlo: que exija un motivo
   * antes de llamar a nada, y que NO se ofrezca sobre uno mismo.
   *
   * Ese último punto es donde el defecto es silencioso: el `sub` de Keycloak y
   * el `auth_users.id` interno son dos UUID distintos para la misma persona.
   * Comparar contra el `sub` —que es lo que hacía el frontend antes de la
   * corrección de identidad— daría siempre falso, y la pantalla ofrecería a un
   * administrador restablecerse a sí mismo para acabar en un 409 del backend.
   */
  function sesion({ userIdInterno }) {
    initSession({
      sub: "kc-sub-ajeno-al-tenant",
      user_id: userIdInterno,
      email: "admin@orpyca.com",
      name: "Administradora",
      preferred_username: "administradora",
      roles: [],
      tenant_slug: "icetex",
      permissions: ["USUA_PERM_ADMIN"],
      permission_levels: { USUA_PERM_ADMIN: 3 },
    });
  }

  function detalleCargado() {
    vi.stubGlobal(
      "fetch",
      vi.fn((url) =>
        String(url).includes("/urd")
          ? respuesta(true, [])
          : respuesta(true, { is_root: false, permissions: {} }),
      ),
    );
  }

  it("ofrece el restablecimiento sobre OTRA persona", async () => {
    sesion({ userIdInterno: "otra-identidad-interna" });
    detalleCargado();
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /restablecer segundo factor/i })).toBeTruthy(),
    );
  });

  it("NO lo ofrece sobre uno mismo, comparando la identidad interna y no el sub", async () => {
    // `u-1` es el id del usuario del listado; el `sub` de la sesión es otro a
    // propósito. Si la pantalla comparara contra el `sub`, este test fallaría.
    sesion({ userIdInterno: "u-1" });
    detalleCargado();
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /^segundo factor$/i })).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: /restablecer segundo factor/i })).toBeNull();
    expect(screen.getByText(/para el suyo use «mi perfil»/i)).toBeTruthy();
  });

  it("exige un motivo antes de llamar al servidor", async () => {
    sesion({ userIdInterno: "otra-identidad-interna" });
    detalleCargado();
    render(UsuariosPage, { props: { data: baseData() } });
    await abrirDetalle();

    await fireEvent.click(
      await screen.findByRole("button", { name: /restablecer segundo factor/i }),
    );
    await fireEvent.click(
      await screen.findByRole("button", { name: /confirmar restablecimiento/i }),
    );

    await waitFor(() => expect(screen.getByText(/explique el motivo/i)).toBeTruthy());
    const llamadas = globalThis.fetch.mock.calls.map((c) => String(c[0]));
    expect(llamadas.some((u) => u.includes("/totp"))).toBe(false);
  });
});
