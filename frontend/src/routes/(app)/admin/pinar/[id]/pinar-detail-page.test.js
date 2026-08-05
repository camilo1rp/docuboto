// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-detail-page.test.js — Render de /admin/pinar/[id] (+page.svelte, Fase 7).
 *
 * Cubre:
 *  - Stepper: el paso activo refleja `plan.estado` (espejo de la máquina de
 *    estados de pinar_service.py — borrador→aprobado→en_ejecucion→cerrado).
 *  - La UI nunca ofrece una transición que el backend rechazaría: "Aprobar"
 *    solo en borrador, "Pasar a ejecución" solo en aprobado, "Cerrar" en
 *    {aprobado, en_ejecucion}; todo además gateado por USUA_PERM_ADMIN.
 *  - Honestidad de la interfaz: el Modal de aprobación advierte la
 *    irreversibilidad (congelamiento del contenido) ANTES de confirmar, y el
 *    botón de confirmar queda deshabilitado sin acto_administrativo.
 *  - Tablero: distingue "no se pudo cargar" (tablero.error) de "vacío
 *    legítimo" (arrays vacíos) — nunca pinta un tablero en cero por error.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";

const invalidateAllMock = vi.fn();
vi.mock("$app/navigation", () => ({ invalidateAll: invalidateAllMock, goto: vi.fn() }));

const { default: PinarDetailPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

/** @param {Partial<any>} overrides */
function plan(overrides = {}) {
  return {
    id: "p-1",
    version: 1,
    estado: "borrador",
    vision: "Visión institucional",
    vigencia_desde: "2026-01-01",
    vigencia_hasta: "2029-12-31",
    acto_administrativo: null,
    aprobado_por: null,
    aprobado_at: null,
    created_at: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

function baseData(planOverrides = {}, tableroOverrides = {}) {
  return {
    plan: plan(planOverrides),
    tablero: {
      data: { plan_id: "p-1", por_objetivo: [], por_eje: [] },
      error: null,
      ...tableroOverrides,
    },
  };
}

describe("/admin/pinar/[id] — Stepper refleja el estado del plan", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });
  afterEach(() => clearSession());

  it("borrador: el paso activo es 'Borrador'", () => {
    render(PinarDetailPage, { props: { data: baseData({ estado: "borrador" }) } });
    const activo = document.querySelector(".op-stepper__step--active .op-stepper__label");
    expect(activo).toHaveTextContent("Borrador");
  });

  it("en_ejecucion: 'Borrador' y 'Aprobado' quedan marcados como completados", () => {
    render(PinarDetailPage, { props: { data: baseData({ estado: "en_ejecucion" }) } });
    const activo = document.querySelector(".op-stepper__step--active .op-stepper__label");
    expect(activo).toHaveTextContent("En ejecución");
    const completados = document.querySelectorAll(".op-stepper__step--done");
    expect(completados).toHaveLength(2);
  });

  it("cerrado: el paso activo es 'Cerrado', ningún paso queda como 'futuro'", () => {
    render(PinarDetailPage, { props: { data: baseData({ estado: "cerrado" }) } });
    const activo = document.querySelector(".op-stepper__step--active .op-stepper__label");
    expect(activo).toHaveTextContent("Cerrado");
    expect(document.querySelectorAll(".op-stepper__step--future")).toHaveLength(0);
  });
});

describe("/admin/pinar/[id] — la UI nunca ofrece una transición que el backend rechazaría", () => {
  afterEach(() => clearSession());

  it("borrador + USUA_PERM_ADMIN: solo 'Aprobar' está disponible", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(PinarDetailPage, { props: { data: baseData({ estado: "borrador" }) } });
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pasar a ejecución" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cerrar plan" })).not.toBeInTheDocument();
  });

  it("aprobado + USUA_PERM_ADMIN: 'Pasar a ejecución' y 'Cerrar plan' disponibles, no 'Aprobar'", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(PinarDetailPage, { props: { data: baseData({ estado: "aprobado" }) } });
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pasar a ejecución" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cerrar plan" })).toBeInTheDocument();
  });

  it("cerrado + USUA_PERM_ADMIN: ninguna acción de ciclo de vida disponible (estado terminal)", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(PinarDetailPage, { props: { data: baseData({ estado: "cerrado" }) } });
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pasar a ejecución" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cerrar plan" })).not.toBeInTheDocument();
  });

  it("borrador SIN USUA_PERM_ADMIN: ninguna acción de gestión, aunque el estado lo permitiría", () => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
    render(PinarDetailPage, { props: { data: baseData({ estado: "borrador" }) } });
    expect(screen.queryByRole("button", { name: "Aprobar" })).not.toBeInTheDocument();
  });
});

describe("/admin/pinar/[id] — irreversibilidad de aprobar, advertida ANTES de confirmar", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });
  afterEach(() => clearSession());

  it("el Modal de aprobación advierte el congelamiento permanente del contenido", async () => {
    const { fireEvent, within } = await import("@testing-library/svelte");
    render(PinarDetailPage, { props: { data: baseData({ estado: "borrador" }) } });

    await fireEvent.click(screen.getByRole("button", { name: "Aprobar" }));

    // El panel Resumen (visible por defecto) ya explica el congelamiento al
    // aprobar; se acota la aserción al propio Modal para no depender de esa
    // coincidencia de texto.
    const dialog = screen.getByRole("dialog", { name: "Aprobar plan PINAR" });
    expect(within(dialog).getByText(/irreversible/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/congelado de forma permanente/i)).toBeInTheDocument();
  });

  it("el botón 'Confirmar aprobación' está deshabilitado sin acto_administrativo", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    render(PinarDetailPage, { props: { data: baseData({ estado: "borrador" }) } });

    await fireEvent.click(screen.getByRole("button", { name: "Aprobar" }));
    const confirmar = screen.getByRole("button", { name: "Confirmar aprobación" });
    expect(confirmar).toBeDisabled();

    const input = screen.getByLabelText(/Acto administrativo/i);
    await fireEvent.input(input, { target: { value: "Resolución 001 de 2026" } });
    expect(confirmar).not.toBeDisabled();
  });
});

describe("/admin/pinar/[id] — tablero: error real vs. vacío legítimo", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });
  afterEach(() => clearSession());

  it("tablero.error: banner explícito, NUNCA una tabla en cero", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    render(PinarDetailPage, {
      props: {
        data: baseData({}, { data: null, error: { detail: "Fallo de red" } }),
      },
    });
    await fireEvent.click(screen.getByRole("tab", { name: /Tablero/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/no se pudo cargar el tablero/i);
  });

  it("tablero con arrays vacíos: EmptyState explícito, no confundido con error", async () => {
    const { fireEvent } = await import("@testing-library/svelte");
    render(PinarDetailPage, { props: { data: baseData({}, {}) } });
    await fireEvent.click(screen.getByRole("tab", { name: /Tablero/i }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Sin objetivos con proyectos")).toBeInTheDocument();
    expect(screen.getByText("Sin ejes con proyectos")).toBeInTheDocument();
  });
});
