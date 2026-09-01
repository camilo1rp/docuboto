// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * preservacion-page.test.js — Render de /admin/preservacion (+page.svelte),
 * pestaña "Plan de preservación".
 *
 * Cubre A6 (revisión UX): `set_plan` (PUT /preservacion/plan,
 * storage-service preservacion.py línea 99) exige
 * `require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)` — antes el
 * formulario "Actualizar plan" solo miraba presencia del permiso (el mismo
 * que gatea la vista de solo lectura, `get_plan`/`list_plan_versions`,
 * min_crud=1 default).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/preservacion") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: PreservacionPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [], permission_levels = {} } = {}) {
  return {
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
    permission_levels,
  };
}

function baseData(overrides = {}) {
  return {
    plan: { data: null, error: { status: 404 } },
    versions: { data: [], error: null },
    indicesPendientes: { data: [], error: null },
    wormPendientes: { data: [], error: null },
    ...overrides,
  };
}

describe("/admin/preservacion — 'Actualizar plan' exige min_crud=3 (A6, revisión UX)", () => {
  beforeEach(() => clearSession());
  afterEach(() => clearSession());

  it("solo presencia (nivel lectura): la vista se ve, pero NO el formulario de actualizar plan", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 1 },
      }),
    );
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.queryByText("Sin permiso")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Guardar nueva versión/ })).not.toBeInTheDocument();
  });

  it("con min_crud=3: el formulario 'Actualizar plan' sí se ve", () => {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
      }),
    );
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.getByRole("button", { name: /Guardar nueva versión/ })).toBeInTheDocument();
  });

  it("sin el permiso: la vista completa queda bloqueada (canRead también depende de él)", () => {
    initSession(sessionWith({ permissions: [] }));
    render(PreservacionPage, { props: { data: baseData() } });
    expect(screen.getByText("Sin permiso")).toBeInTheDocument();
  });
});

describe("/admin/preservacion — la política de migración se declara aquí (RF-PRE-04)", () => {
  beforeEach(() => clearSession());
  afterEach(() => {
    clearSession();
    vi.unstubAllGlobals();
  });

  const PLAN = {
    id: "p-1",
    version: 3,
    formatos_destino: ["PDF/A-2b"],
    formatos_origen: ["application/msword"],
    num_copias: 2,
    periodicidad_fixity_dias: 180,
    politica_migracion: null,
    contingencia: null,
    creado_por: "ana",
    created_at: "2026-08-30T10:00:00Z",
  };

  function conPlan(plan) {
    initSession(
      sessionWith({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
      }),
    );
    return render(PreservacionPage, {
      props: { data: baseData({ plan: { data: plan, error: null } }) },
    });
  }

  it("un plan SIN formatos en riesgo lo dice, en vez de dejar el hueco en blanco", () => {
    // Un guion aquí se leería como «no aplica». Lo que ocurre de verdad es que
    // el barrido periódico no convierte nada de esta entidad, y eso hay que
    // decirlo: el fallo que importa no es convertir de más, es creer que se
    // convirtió.
    conPlan({ ...PLAN, formatos_origen: [] });
    expect(screen.getByText(/no migra nada de esta entidad/)).toBeInTheDocument();
  });

  it("lo declarado en el plan es lo que llega al backend, no lo que se ve en pantalla", async () => {
    // Se asserta sobre el cuerpo ENVIADO y no sobre el input: el valor que
    // decide qué se convierte es el que viaja, y un formulario que pinta bien
    // y manda otra cosa pasaría un test hecho sobre el DOM.
    const enviado = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts) => {
        if (opts?.method === "PUT") {
          enviado.push(JSON.parse(opts.body));
          return new Response(JSON.stringify({ ...PLAN, version: 4 }), { status: 201 });
        }
        return new Response("[]", { status: 200 });
      }),
    );

    conPlan(PLAN);
    const campo = screen.getByLabelText(/Formatos en riesgo de obsolescencia/);
    expect(campo).toHaveValue("application/msword");

    await fireEvent.input(campo, { target: { value: "image/tiff, application/vnd.ms-excel" } });
    await fireEvent.click(screen.getByRole("button", { name: /Guardar nueva versión/ }));

    expect(enviado).toHaveLength(1);
    expect(enviado[0].formatos_origen).toEqual(["image/tiff", "application/vnd.ms-excel"]);
  });
});
