// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ADR-029 — la bandeja dice DÓNDE ESTÁ el radicado, no a quién iba dirigido.
 *
 * `2026-DEMO-E-000117` aparecía con «Oficina de Tecnología» tras haber sido repartido
 * a Talento Humano y devuelto a Control Interno: la pantalla pintaba `dest_dept`
 * (radicación). Aquí el radicado tiene las dos dependencias DISTINTAS a propósito:
 * un test con la misma en ambas no distingue la columna buena de la mala.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { clearSession, initSession } = await import("$lib/stores/auth.js");

const ID = "11111111-1111-1111-1111-111111111111";
const RADICACION = "Oficina de Tecnología";
const UBICACION = "Control Interno";

const DOC = {
  id: ID,
  tracking_number: "2026-DEMO-E-000117",
  doc_type: "E",
  subject: "Solicitud de certificación",
  status: "in_progress",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Externo",
  dest_dept: RADICACION,
  dest_dept_code: 7,
  flujo_dept: UBICACION,
  flujo_dept_code: 3,
  flujo_usuario: "22222222-2222-2222-2222-222222222222",
  ubicacion_dept: UBICACION,
  ubicacion_dept_code: 3,
  pages: 1,
};

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

const sesion = (permissions) => ({
  sub: "u-1",
  email: "op@orpyca.com",
  name: "Operadora",
  preferred_username: "operadora",
  roles: [],
  tenant_slug: "icetex",
  permissions,
});

const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

function mock(detalle) {
  globalThis.fetch = vi.fn(async (url) => {
    const u = url.toString();
    if (u === `/bandeja/api/${ID}`) return json(detalle);
    if (u.startsWith("/bandeja/api/users")) {
      return json({
        users: [{ id: "22222222-2222-2222-2222-222222222222", username: "maria.gomez" }],
      });
    }
    if (u === `/bandeja/api/${ID}/current`) return json({ error: "no_active_step" }, 404);
    if (u.endsWith("/actores") || u.endsWith("/respuestas")) return json([]);
    return json({ items: [], total: 0 }, 404);
  });
}

const data = (doc) => ({
  documents: [doc],
  total: 1,
  loadError: null,
  vista: "lista",
  dependencias: [],
  initialFilters: { docType: "", status: "", page: 1 },
});

async function abrir(doc, detalle = doc, permisos = ["PERM_RADI"]) {
  initSession(sesion(permisos));
  mock(detalle);
  render(BandejaPage, { props: { data: data(doc) } });
  await waitFor(() => expect(screen.getByText(doc.tracking_number)).toBeTruthy());
  await fireEvent.click(
    screen.getByRole("button", { name: `Ver detalle de radicado ${doc.tracking_number}` }),
  );
  await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
}

describe("/bandeja — lista", () => {
  it("la columna es «Ubicación actual» y la celda lleva la del flujo, no la de radicación", () => {
    mock(DOC);
    render(BandejaPage, { props: { data: data(DOC) } });
    expect(screen.getByRole("columnheader", { name: "Ubicación actual" })).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: "Dependencia destino" })).toBeNull();
    const fila = screen.getByText(DOC.tracking_number).closest("tr");
    expect(within(fila).getByText(UBICACION)).toBeTruthy();
    expect(within(fila).queryByText(RADICACION)).toBeNull();
  });

  it("sin ubicación no inventa la de radicación: guion largo", () => {
    const sin = { ...DOC, ubicacion_dept: null, flujo_dept: null };
    mock(sin);
    render(BandejaPage, { props: { data: data(sin) } });
    const fila = screen.getByText(DOC.tracking_number).closest("tr");
    expect(within(fila).queryByText(RADICACION)).toBeNull();
    expect(within(fila).getByText("—")).toBeTruthy();
  });
});

describe("/bandeja — detalle", () => {
  it("«Ubicación actual» con responsable, y «Dependencia de radicación» aparte", async () => {
    await abrir(DOC);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    expect(ubic.textContent).toContain(UBICACION);
    await waitFor(() =>
      expect(screen.getByTestId("detalle-ubicacion-actual").textContent).toContain(
        "Responsable: maria.gomez",
      ),
    );
    // nunca el UUID crudo del poseedor
    expect(screen.getByTestId("detalle-ubicacion-actual").textContent).not.toContain("2222");
    const rad = screen.getByTestId("detalle-dependencia-radicacion");
    expect(rad.textContent).toContain(RADICACION);
    expect(rad.textContent).not.toContain(UBICACION);
  });

  it("un cerrado dice «Cerrado en X» y no ofrece responsable", async () => {
    const cerrado = { ...DOC, status: "closed" };
    await abrir(cerrado);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    expect(ubic.textContent).toContain(`Cerrado en ${UBICACION}`);
    expect(ubic.textContent).not.toContain("Responsable");
  });

  it("sin pasos y con destino: «Sin repartir — en X», sin el paréntesis confuso", async () => {
    const quieto = {
      ...DOC,
      flujo_dept: null,
      flujo_dept_code: null,
      flujo_usuario: null,
      ubicacion_dept: RADICACION,
      ubicacion_dept_code: 7,
    };
    await abrir(quieto);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    expect(ubic.textContent).toContain(`Sin repartir — en ${RADICACION}`);
    expect(ubic.textContent).not.toContain("aún en su dependencia");
  });

  it("Salida sin destino ni pasos: dice «dependencia productora», no una ubicación a secas", async () => {
    const salida = {
      ...DOC,
      doc_type: "S",
      dest_dept: null,
      dest_dept_code: null,
      origin_dept: "Jurídica",
      flujo_dept: null,
      flujo_dept_code: null,
      flujo_usuario: null,
      ubicacion_dept: "Jurídica",
    };
    await abrir(salida);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    expect(ubic.textContent).toContain("Sin repartir — en la dependencia productora: Jurídica");
    expect(ubic.textContent).not.toMatch(/^\s*Jurídica/);
  });

  it("un anulado que nunca pasó por el flujo se redacta sin el «aún en…» ambiguo", async () => {
    const anulado = {
      ...DOC,
      status: "anulado",
      flujo_dept: null,
      flujo_dept_code: null,
      flujo_usuario: null,
      ubicacion_dept: RADICACION,
    };
    await abrir(anulado);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    expect(ubic.textContent).toContain(`Anulado sin pasar por el flujo — en ${RADICACION}`);
    expect(ubic.textContent).not.toContain("aún en su dependencia");
  });

  it("«Ubicación actual» es la PRIMERA fila del detalle, antes que el tipo y la radicación", async () => {
    await abrir(DOC);
    const dts = [...document.querySelectorAll(".doc-detail__dl dt")].map((e) =>
      e.textContent.trim(),
    );
    expect(dts[0]).toBe("Ubicación actual");
    expect(dts.indexOf("Ubicación actual")).toBeLessThan(dts.indexOf("Dependencia de radicación"));
    expect(dts.indexOf("Dependencia de radicación")).toBeLessThan(
      dts.indexOf("Dependencia origen"),
    );
  });
});

describe("/bandeja — responsable sin PERM_RADI (PERM_TRAMITAR)", () => {
  it("no llama a /bandeja/api/users (evita el 403 en cada apertura) y no avisa error", async () => {
    await abrir(DOC, DOC, ["PERM_TRAMITAR"]);
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    await waitFor(() => expect(ubic.textContent).toContain("responsable de la dependencia"));
    const llamadas = globalThis.fetch.mock.calls.map((c) => c[0].toString());
    expect(llamadas.some((u) => u.startsWith("/bandeja/api/users"))).toBe(false);
    expect(ubic.textContent).not.toContain("2222");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("si la carga de usuarios falla, degrada en silencio y NO reintenta al reabrir", async () => {
    initSession(sesion(["PERM_RADI"]));
    globalThis.fetch = vi.fn(async (url) => {
      const u = url.toString();
      if (u === `/bandeja/api/${ID}`) return json(DOC);
      if (u.startsWith("/bandeja/api/users")) return json({ error: "forbidden" }, 403);
      if (u.endsWith("/actores") || u.endsWith("/respuestas")) return json([]);
      return json({ error: "x" }, 404);
    });
    render(BandejaPage, { props: { data: data(DOC) } });
    const abrirBtn = () =>
      screen.getByRole("button", { name: `Ver detalle de radicado ${DOC.tracking_number}` });
    await fireEvent.click(abrirBtn());
    await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
    const ubic = await screen.findByTestId("detalle-ubicacion-actual");
    await waitFor(() => expect(ubic.textContent).toContain("responsable de la dependencia"));
    await fireEvent.click(abrirBtn());
    await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
    const users = globalThis.fetch.mock.calls.filter((c) =>
      c[0].toString().startsWith("/bandeja/api/users"),
    );
    expect(users.length).toBe(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("/bandeja — ubicación en móvil", () => {
  it("la fila lleva la ubicación como línea secundaria bajo el asunto (la columna se oculta)", () => {
    mock(DOC);
    render(BandejaPage, { props: { data: data(DOC) } });
    const linea = screen.getByTestId("fila-ubicacion-movil");
    expect(linea.textContent.trim()).toBe(`En ${UBICACION}`);
    expect(linea.closest("td").querySelector(".subject-text")).toBeTruthy();
  });

  it("sin ubicación no pinta la línea", () => {
    const sin = { ...DOC, ubicacion_dept: null };
    mock(sin);
    render(BandejaPage, { props: { data: data(sin) } });
    expect(screen.queryByTestId("fila-ubicacion-movil")).toBeNull();
  });
});
