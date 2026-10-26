// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Bandeja — actos de trámite por TENENCIA del paso activo (ADR-028).
 *
 * El estado del radicado lo mueve el flujo: ya no hay «Distribuir»/«Iniciar
 * trámite» manuales. «Marcar en trámite», «Finalizar trámite» y «Devolver» solo
 * los ofrece quien POSEE el paso activo; el backend re-valida.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const DOC = {
  id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-ICETEX-E-000001",
  doc_type: "E",
  subject: "Solicitud",
  status: "distributed",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Ventanilla Única",
  dest_dept: null,
  pages: 1,
};
const YO = "aaaaaaaa-0000-0000-0000-000000000001";
const OTRO = "bbbbbbbb-0000-0000-0000-000000000002";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

const jr = (body, status = 200) => new Response(JSON.stringify(body), { status });

function login(permissions = ["PERM_TRAMITAR", "PERM_RADI"], nivel = 3) {
  initSession({
    sub: "kc-sub",
    user_id: YO,
    email: "o@orpyca.com",
    name: "Op",
    preferred_username: "op",
    roles: [],
    tenant_slug: "icetex",
    permissions,
    // /auth/me solo trae nivel de los permisos que el usuario TIENE.
    permission_levels: Object.fromEntries(
      permissions.map((p) => [p, p === "PERM_TRAMITAR" ? nivel : 3]),
    ),
    dependencias: [{ codigo: 7, nombre: "Jurídica", sigla: "JUR" }],
    context: { user_id: YO, active_depe_id: 7, available_depts: [{ depe_id: 7, is_active: true }] },
  });
}

/** @param {object|null} paso  null = 404 no_active_step */
function mockFetch(paso, extra = {}) {
  const calls = [];
  const fn = vi.fn(async (url, init) => {
    const u = url.toString();
    calls.push({ u, init });
    if (u === `/bandeja/api/${DOC.id}` && !init) return jr(extra.__doc ?? DOC);
    if (u.startsWith("/anulaciones/api/")) return jr({}, 404);
    if (u === `/bandeja/api/${DOC.id}/current`)
      return paso
        ? jr({ id: "s1", radicado_id: DOC.id, ...paso })
        : jr({ error: "no_active_step" }, 404);
    if (u.endsWith("/respuestas") || u.endsWith("/actores")) return jr([]);
    if (typeof extra[u] === "function") return extra[u](init);
    if (u.startsWith("/bandeja/api")) return jr({ documents: [DOC], total: 1 });
    return jr({});
  });
  fn.calls = calls;
  return fn;
}

const data = () => ({
  documents: [DOC],
  total: 1,
  loadError: null,
  vista: "lista",
  dependencias: [{ nombre: "Jurídica", sigla: "JUR" }],
  initialFilters: { docType: "", status: "", page: 1 },
});

async function abrirDetalle() {
  render(BandejaPage, { props: { data: data() } });
  await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
  );
  // /current ya resuelto
  await waitFor(() =>
    expect(globalThis.fetch.mock.calls.some(([u]) => String(u).endsWith("/current"))).toBe(true),
  );
  await new Promise((r) => setTimeout(r, 20));
}

const FIN = /finalizar trámite/i;
const MARCAR = /marcar en trámite/i;

describe("bandeja — sin avance manual de estado", () => {
  it("la fila ya no ofrece Distribuir ni Iniciar trámite", () => {
    login();
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    render(BandejaPage, { props: { data: data() } });
    expect(screen.queryByText(/^\s*Distribuir\s*$/)).toBeNull();
    expect(screen.queryByText(/iniciar trámite/i)).toBeNull();
  });
});

describe("bandeja — actos de trámite por tenencia", () => {
  it("poseedor (asignado a mí): ve Marcar en trámite, Finalizar y Devolver", async () => {
    login();
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.getByRole("button", { name: MARCAR })).toBeTruthy();
    expect(screen.getByRole("button", { name: FIN })).toBeTruthy();
    expect(screen.getByRole("button", { name: /devolver/i })).toBeTruthy();
  });

  it("poseedor por dependencia (sin asignar): también", async () => {
    login();
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: null,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.getByRole("button", { name: FIN })).toBeTruthy();
  });

  it("paso en trámite: Finalizar sí, Marcar en trámite no", async () => {
    login();
    globalThis.fetch = mockFetch({
      status: "in_progress",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.getByRole("button", { name: FIN })).toBeTruthy();
    expect(screen.queryByRole("button", { name: MARCAR })).toBeNull();
  });

  it("NO poseedor (asignado a un colega de mi dependencia): ningún acto, ni Devolver", async () => {
    login();
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: OTRO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    expect(screen.queryByRole("button", { name: MARCAR })).toBeNull();
    expect(screen.queryByRole("button", { name: /devolver/i })).toBeNull();
  });

  it("Devolver sigue a PERM_TRAMITAR (lo que gatea el backend), no a PERM_RADI", async () => {
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    login(["PERM_TRAMITAR"]);
    await abrirDetalle();
    expect(screen.getByRole("button", { name: /devolver/i })).toBeTruthy();
  });

  it("con solo PERM_RADI no se ofrece Devolver (el backend daría 403)", async () => {
    login(["PERM_RADI"]);
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.queryByRole("button", { name: /devolver/i })).toBeNull();
  });

  it("sin paso activo (404): ningún acto", async () => {
    login();
    globalThis.fetch = mockFetch(null);
    await abrirDetalle();
    expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    expect(screen.queryByRole("button", { name: /devolver/i })).toBeNull();
  });

  it("poseedor sin PERM_TRAMITAR: no se ofrecen Marcar ni Finalizar", async () => {
    login(["PERM_RADI"]);
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    expect(screen.queryByRole("button", { name: MARCAR })).toBeNull();
  });

  it("Marcar en trámite envía marcar_leido al proxy de transacciones", async () => {
    login();
    const f = mockFetch(
      { status: "pending", assigned_to: YO, to_dept: "Jurídica", to_dept_code: 7 },
      { [`/bandeja/api/${DOC.id}/transactions`]: () => jr({ ok: true }) },
    );
    globalThis.fetch = f;
    await abrirDetalle();
    await fireEvent.click(screen.getByRole("button", { name: MARCAR }));
    await waitFor(() => {
      const c = f.calls.find((x) => x.u.endsWith("/transactions"));
      expect(c).toBeTruthy();
      expect(JSON.parse(c.init.body)).toEqual({ tipo_tx: "marcar_leido" });
    });
  });

  it("Finalizar exige motivo, comentario Y casilla, y envía { comentario, motivo_cierre }", async () => {
    login();
    const f = mockFetch(
      { status: "in_progress", assigned_to: YO, to_dept: "Jurídica", to_dept_code: 7 },
      { [`/bandeja/api/${DOC.id}/finalizar`]: () => jr({ id: "s1" }) },
    );
    globalThis.fetch = f;
    await abrirDetalle();
    await fireEvent.click(screen.getByRole("button", { name: FIN }));

    await waitFor(() => expect(document.querySelector("textarea")).toBeTruthy());
    const dialog = /** @type {HTMLElement} */ (document.querySelector("textarea")).closest(
      '[role="dialog"]',
    );
    const boton = Array.from(dialog.querySelectorAll("button")).find((b) =>
      /finalizar trámite/i.test(b.textContent ?? ""),
    );
    expect(boton).toBeDisabled();
    // Etiquetas en español con tildes en el selector del motivo.
    const opciones = Array.from(dialog.querySelectorAll("select option")).map((o) => o.textContent);
    expect(opciones).toEqual(
      expect.arrayContaining([
        "Respondido",
        "No requiere respuesta (NRR)",
        "Traslado por competencia",
        "Otro",
      ]),
    );
    await fireEvent.input(dialog.querySelector("textarea"), {
      target: { value: "Se respondió y no hay más actuaciones." },
    });
    expect(boton).toBeDisabled();
    await fireEvent.click(dialog.querySelector('input[type="checkbox"]'));
    expect(boton).not.toBeDisabled();
    // Sin motivo NO se envía: el backend daría 422.
    await fireEvent.click(boton);
    await waitFor(() => expect(screen.getByText(/seleccione el motivo de cierre/i)).toBeTruthy());
    expect(f.calls.some((x) => x.u.endsWith("/finalizar"))).toBe(false);

    await fireEvent.change(dialog.querySelector("select"), { target: { value: "nrr" } });
    await fireEvent.click(boton);

    await waitFor(() => {
      const c = f.calls.find((x) => x.u.endsWith("/finalizar"));
      expect(c).toBeTruthy();
      expect(JSON.parse(c.init.body)).toEqual({
        comentario: "Se respondió y no hay más actuaciones.",
        motivo_cierre: "nrr",
      });
    });
  });

  it("PERM_TRAMITAR nivel 2 (editar): NO se ofrece Finalizar (el backend da 403), Marcar sí", async () => {
    login(["PERM_TRAMITAR", "PERM_RADI"], 2);
    globalThis.fetch = mockFetch({
      status: "in_progress",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.queryByRole("button", { name: FIN })).toBeNull();
  });

  it("PERM_TRAMITAR nivel 1 con paso pendiente propio: Marcar sí, Finalizar no", async () => {
    login(["PERM_TRAMITAR", "PERM_RADI"], 1);
    globalThis.fetch = mockFetch({
      status: "pending",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
    });
    await abrirDetalle();
    expect(screen.getByRole("button", { name: MARCAR })).toBeTruthy();
    expect(screen.queryByRole("button", { name: FIN })).toBeNull();
  });

  describe("sin paso activo: poseedor del último paso completado (R12)", () => {
    const hist = (steps) => ({ [`/bandeja/api/${DOC.id}/history`]: () => jr({ steps }) });
    const paso = (o) => ({
      id: `s9-${o?.step_number ?? 2}`,
      radicado_id: DOC.id,
      tracking_number: DOC.tracking_number,
      step_number: 2,
      status: "completed",
      assigned_to: YO,
      to_dept: "Jurídica",
      to_dept_code: 7,
      ...o,
    });

    it("lo poseía: se ofrece Finalizar", async () => {
      login();
      globalThis.fetch = mockFetch(
        null,
        hist([paso({ step_number: 1, assigned_to: OTRO }), paso()]),
      );
      await abrirDetalle();
      await waitFor(() => expect(screen.getByRole("button", { name: FIN })).toBeTruthy());
    });

    it("el último paso era de un colega: no", async () => {
      login();
      globalThis.fetch = mockFetch(
        null,
        hist([paso({ step_number: 1 }), paso({ assigned_to: OTRO })]),
      );
      await abrirDetalle();
      expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    });

    it("con nivel 2: no, aunque lo poseyera", async () => {
      login(["PERM_TRAMITAR", "PERM_RADI"], 2);
      globalThis.fetch = mockFetch(null, hist([paso()]));
      await abrirDetalle();
      expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    });

    it("radicado ya cerrado: no", async () => {
      login();
      globalThis.fetch = mockFetch(null, {
        ...hist([paso()]),
        __doc: { ...DOC, status: "closed" },
      });
      render(BandejaPage, { props: { data: data() } });
      await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
      await waitFor(() =>
        expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
      );
      await new Promise((r) => setTimeout(r, 30));
      expect(screen.queryByRole("button", { name: FIN })).toBeNull();
    });
  });
});
