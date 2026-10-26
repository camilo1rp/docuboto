// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-permiso.test.js — El PINAR deja de leerse sin permiso (2026-08-28).
 *
 * Las 11 lecturas del PINAR pasaron a exigir `USUA_PERM_ADMIN` en
 * `tenant-service`, que era lo que la interfaz ya daba por hecho: la barra
 * lateral y las tarjetas de `/admin` esconden la pantalla desde siempre.
 *
 * Aquí se prueban las dos consecuencias en el frontend, que son distintas:
 *  1. El `load` NO presenta ese 403 como un fallo de carga. Una negativa de
 *     acceso disfrazada de avería manda a buscar el problema donde no está
 *     — el defecto que ya apareció en `/envios` («no hay registros» por un
 *     403) y en la bandeja de correo («la lista puede estar desactualizada»).
 *  2. La pantalla lo dice con su nombre, y **hace caso al backend aunque la
 *     sesión diga otra cosa**: el nivel CRUD o una concesión revocada pueden
 *     hacer que `$permissions` y la respuesta real discrepen.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

import { load } from "./+page.server.js";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/pinar") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: PinarPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const evento = (res) => ({
  cookies: { get: () => "tok" },
  parent: async () => ({ user: { tenant_slug: "icetex", sub: "u-1" } }),
  fetch: async () => res,
  url: new URL("http://localhost/admin/pinar"),
});

const respuesta = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

describe("el load distingue la negativa de acceso del fallo de carga", () => {
  it("403 → sinPermiso, y NINGÚN mensaje de error de carga", async () => {
    const data = await load(
      evento(respuesta(403, { detail: { error: "forbidden", detail: "Permiso requerido" } })),
    );
    expect(data.sinPermiso).toBe(true);
    expect(data.loadError).toBeNull();
    expect(data.planes).toEqual([]);
  });

  it("500 sigue siendo un fallo de carga, no un bloqueo", async () => {
    const data = await load(evento(respuesta(500, { detail: { detail: "Boom" } })));
    expect(data.sinPermiso).toBe(false);
    expect(data.loadError).toBeTruthy();
  });

  it("200 no bloquea nada", async () => {
    const data = await load(respuestaOk());
    expect(data.sinPermiso).toBe(false);
    expect(data.loadError).toBeNull();
    expect(data.planes).toHaveLength(1);
  });

  function respuestaOk() {
    return evento(respuesta(200, [{ id: "p-1", version: 1, estado: "borrador" }]));
  }
});

describe("la pantalla dice qué permiso falta, y el backend manda", () => {
  const baseData = (o = {}) => ({
    planes: [],
    loadError: null,
    sinPermiso: false,
    estadoFiltro: "",
    ...o,
  });
  const sesion = (permissions) => ({
    sub: "u-1",
    email: "a@b.co",
    name: "A",
    preferred_username: "a",
    roles: [],
    tenant_slug: "icetex",
    permissions,
    permission_levels: Object.fromEntries(permissions.map((p) => [p, 3])),
  });

  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("sin USUA_PERM_ADMIN: bloqueo con el nombre del permiso, y ningún plan a la vista", () => {
    initSession(sesion([]));
    render(PinarPage, {
      props: { data: baseData({ planes: [{ id: "p-1", version: 1, estado: "borrador" }] }) },
    });
    expect(screen.getByText(/Sin permiso de administración del PINAR/i)).toBeInTheDocument();
    expect(screen.getByText(/USUA_PERM_ADMIN/)).toBeInTheDocument();
    expect(screen.queryByText("v1")).not.toBeInTheDocument();
  });

  it("la sesión dice que sí y el backend dijo que no: manda el backend", () => {
    initSession(sesion(["USUA_PERM_ADMIN"]));
    render(PinarPage, { props: { data: baseData({ sinPermiso: true }) } });
    expect(screen.getByText(/Sin permiso de administración del PINAR/i)).toBeInTheDocument();
  });

  it("con permiso y sin negativa, la pantalla es la de siempre", () => {
    initSession(sesion(["USUA_PERM_ADMIN"]));
    render(PinarPage, { props: { data: baseData() } });
    expect(screen.queryByText(/Sin permiso de administración del PINAR/i)).not.toBeInTheDocument();
    expect(screen.getByText("Sin planes PINAR")).toBeInTheDocument();
  });
});
