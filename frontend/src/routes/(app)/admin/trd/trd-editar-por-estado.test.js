// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * «Editar» sobre un instrumento archivístico solo existe en estado borrador.
 *
 * `update_trd_serie` es append-only y su propio resumen lo dice: *"only
 * 'borrador'; use /versiones otherwise"*. La pantalla, sin embargo, gateaba el
 * botón únicamente por el NIVEL de permiso (`canWriteLevel3`) y no por
 * `item.estado`. Encontrado navegando: se aprueba la serie DEM-100, sigue
 * apareciendo «Editar», se abre un formulario con cuatro campos editables, se
 * cambia el nombre, se guarda… y el backend responde **409**.
 *
 * Rompía la regla dura del proyecto —la UI nunca ofrece una transición que el
 * backend rechazaría— que el propio `InstrumentLifecycleActions` sí cumple para
 * sus botones: el gate por estado faltaba solo en el botón del componente
 * padre, y estaba igual en TRD y en TVD.
 *
 * Para cambiar un instrumento ya aprobado el camino es «Nueva versión»: eso es
 * lo que exige el circuito de convalidación (Ac. AGN 001/2024), no una edición
 * en sitio que borraría el rastro de lo que aprobó el Comité.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/trd") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: TrdPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function serie(estado) {
  return {
    id: `s-${estado}`,
    code: "DEM-100",
    version: 1,
    name: `Derechos de Petición (${estado})`,
    estado,
    disposicion_final: "CT",
    anios_gestion: 5,
    anios_total: 10,
    parent_id: null,
  };
}

beforeEach(() => {
  initSession({
    sub: "u-1",
    email: "trd@orpyca.com",
    name: "Gestora TRD",
    preferred_username: "gestora",
    roles: [],
    tenant_slug: "demo",
    permissions: ["USUA_PERM_TRD"],
    permission_levels: { USUA_PERM_TRD: 3 },
  });
});

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

const editar = () => screen.queryAllByRole("button", { name: /^editar$/i });

describe("/admin/trd — «Editar» respeta el estado del instrumento", () => {
  it("en borrador SÍ se ofrece (control positivo)", () => {
    // Sin esto, un `{#if false}` dejaría los tests de abajo en verde y la
    // pantalla sin forma de corregir una serie recién creada.
    render(TrdPage, {
      props: { data: { series: [serie("borrador")], tipos: [], pendientes: [] } },
    });
    expect(editar().length).toBeGreaterThan(0);
  });

  for (const estado of ["aprobada", "convalidada", "migrada", "derogada"]) {
    it(`en ${estado} NO se ofrece — el backend responde 409`, () => {
      render(TrdPage, { props: { data: { series: [serie(estado)], tipos: [], pendientes: [] } } });
      expect(editar()).toHaveLength(0);
    });
  }

  it("sin nivel CRUD 3 no se ofrece ni en borrador", () => {
    // El gate de estado se AÑADE al de permiso, no lo sustituye.
    clearSession();
    initSession({
      sub: "u-2",
      email: "lectora@orpyca.com",
      name: "Lectora",
      preferred_username: "lectora",
      roles: [],
      tenant_slug: "demo",
      permissions: ["USUA_PERM_TRD"],
      permission_levels: { USUA_PERM_TRD: 1 },
    });
    render(TrdPage, {
      props: { data: { series: [serie("borrador")], tipos: [], pendientes: [] } },
    });
    expect(editar()).toHaveLength(0);
  });
});
