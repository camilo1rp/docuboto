// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * SidebarNav.test.js — Nav espejo del RBAC (D-16 hardening).
 *
 * SidebarNav filtra sus ítems por `permissions`/`roles` (derivados de
 * `sessionUser`). Estos tests renderizan el componente con distintas
 * combinaciones de sesión y ASERTAN qué enlaces se ven/ocultan — la
 * garantía de que la UI nunca "ofrece" más de lo que el backend permitiría
 * (aunque el backend SIEMPRE re-valida, defensa en profundidad).
 *
 * `$app/stores` se mockea porque el componente solo se monta aquí de forma
 * aislada (sin el router real de SvelteKit) — es el patrón estándar para
 * testear componentes SvelteKit con Vitest.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/dashboard") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: SidebarNav } = await import("./SidebarNav.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

/** Construye una sesión mínima con los roles/permisos indicados. */
function sessionWith({ roles = [], permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles,
    tenant_slug: "icetex",
    permissions,
  };
}

describe("SidebarNav — espejo del RBAC", () => {
  beforeEach(() => {
    clearSession();
  });

  it("sin sesión (permisos vacíos) solo muestra los ítems públicos", () => {
    initSession(sessionWith());
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Panel principal" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Bandeja" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Firmas" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Administración" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Plantillas" })).not.toBeInTheDocument();
  });

  it("PERM_RADI habilita Bandeja, Búsqueda y Borradores (pero no Radicar por sí solo)", () => {
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Bandeja" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Búsqueda" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Borradores" })).toBeInTheDocument();
    // PERM_RADI no es ninguno de los permisos de la lista `permissions` de "Radicar"
    expect(screen.queryByRole("link", { name: "Radicar" })).not.toBeInTheDocument();
  });

  it("PERM_RADI_ENTRADA habilita Radicar (basta uno de la lista permissions[])", () => {
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Radicar" })).toBeInTheDocument();
  });

  it("PERM_RADI_SALIDA habilita Envíos", () => {
    initSession(sessionWith({ permissions: ["PERM_RADI_SALIDA"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Envíos" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Bandeja" })).not.toBeInTheDocument();
  });

  it("PERM_FIRMA habilita Firmas y solo Firmas entre los ítems gateados", () => {
    initSession(sessionWith({ permissions: ["PERM_FIRMA"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Firmas" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Bandeja" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Envíos" })).not.toBeInTheDocument();
  });

  it("USUA_PERM_EXPEDIENTE habilita Expedientes, Archivo físico y Transferencias", () => {
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Expedientes" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Archivo físico" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Transferencias" })).toBeInTheDocument();
  });

  it("SGD_PERM_ESTADISTICA habilita Reportes", () => {
    initSession(sessionWith({ permissions: ["SGD_PERM_ESTADISTICA"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Reportes" })).toBeInTheDocument();
  });

  it("USUA_PERM_ADMIN habilita Plantillas (independiente del rol tenant_admin)", () => {
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Plantillas" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Administración" })).not.toBeInTheDocument();
  });

  it("el rol tenant_admin habilita Administración (gate por rol, no por permiso)", () => {
    initSession(sessionWith({ roles: ["tenant_admin"] }));
    render(SidebarNav);

    expect(screen.getByRole("link", { name: "Administración" })).toBeInTheDocument();
    // El rol NO otorga por sí solo Plantillas (gateado por USUA_PERM_ADMIN)
    expect(screen.queryByRole("link", { name: "Plantillas" })).not.toBeInTheDocument();
  });

  it("un operador con todos los permisos ve todos los ítems gateados", () => {
    initSession(
      sessionWith({
        roles: ["tenant_admin"],
        permissions: [
          "PERM_RADI",
          "PERM_RADI_ENTRADA",
          "PERM_RADI_SALIDA",
          "PERM_RADI_INTERNO",
          "USUA_PERM_EXPEDIENTE",
          "SGD_PERM_ESTADISTICA",
          "PERM_FIRMA",
          "USUA_PERM_ADMIN",
        ],
      }),
    );
    render(SidebarNav);

    for (const label of [
      "Panel principal",
      "Bandeja",
      "Radicar",
      "Expedientes",
      "Búsqueda",
      "Reportes",
      "Borradores",
      "Envíos",
      "Firmas",
      "Archivo físico",
      "Transferencias",
      "Administración",
      "Plantillas",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});
