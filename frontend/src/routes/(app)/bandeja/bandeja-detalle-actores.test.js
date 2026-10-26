// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-detalle-actores.test.js — el detalle del radicado enseña sus actores.
 *
 * Por qué existe: el vínculo radicado↔tercero se escribía al radicar y **no lo
 * leía ninguna pantalla**. Un dato que nadie mira no se puede comprobar: podía
 * romperse en silencio y nada lo delataba. Peor todavía, el detalle describía al
 * actor desde `sender_name`, que solo lleva al PRINCIPAL — así que una Salida
 * con tres destinatarios se veía idéntica a una con uno solo, contradiciendo la
 * regla de negocio del propio dominio («una salida puede tener varios
 * destinatarios»).
 *
 * Lo que se mide aquí es eso: que los tres aparezcan, que remitente y
 * destinatario no se mezclen, y que lo que se pide sea la copia CONGELADA del
 * radicado y no la ficha vigente del catálogo — son preguntas distintas.
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
const { clearSession } = await import("$lib/stores/auth.js");

const RADICADO_ID = "11111111-1111-1111-1111-111111111111";

const DOC = {
  id: RADICADO_ID,
  tracking_number: "2026-ICETEX-S-000009",
  doc_type: "S",
  subject: "Circular de convocatoria",
  status: "distributed",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Jurídica",
  dest_dept: "Externo",
  pages: 2,
};

function actor(extra) {
  return {
    id: `a-${extra.orden}-${extra.rol}`,
    tercero_id: `t-${extra.orden}`,
    principal: false,
    canal: "email",
    entidad: null,
    email: null,
    telefono: null,
    direccion: null,
    municipio: null,
    departamento: null,
    municipio_codigo: null,
    departamento_codigo: null,
    pais: "CO",
    creado_at: "2026-07-01T10:00:00Z",
    ...extra,
  };
}

const TRES_DESTINATARIOS = [
  actor({ rol: "destinatario", orden: 0, principal: true, nombre: "José Pérez" }),
  actor({ rol: "destinatario", orden: 1, nombre: "Ana Restrepo" }),
  actor({ rol: "destinatario", orden: 2, nombre: "Carlos Mejía" }),
];

function baseData(overrides = {}) {
  return {
    documents: [DOC],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

/** Mock del cajón completo; `actores` es lo que devuelve el proxy de actores. */
function mockDrawer(actores) {
  const llamadas = [];
  globalThis.fetch = vi.fn(async (url) => {
    const u = url.toString();
    llamadas.push(u);
    if (u === `/bandeja/api/${RADICADO_ID}`) return jsonResponse(DOC);
    if (u === `/bandeja/api/${RADICADO_ID}/actores`) return jsonResponse(actores);
    if (u === `/bandeja/api/${RADICADO_ID}/respuestas`) return jsonResponse([]);
    if (u === `/bandeja/api/${RADICADO_ID}/current`) {
      return jsonResponse({ error: "no_active_step" }, 404);
    }
    if (u.startsWith(`/anulaciones/api/${RADICADO_ID}`)) return jsonResponse({}, 404);
    return jsonResponse({ items: [], total: 0 });
  });
  return llamadas;
}

async function abrirCajon() {
  render(BandejaPage, { props: { data: baseData() } });
  await waitFor(() => expect(screen.getByText(DOC.tracking_number)).toBeTruthy());
  await fireEvent.click(
    screen.getByRole("button", { name: `Ver detalle de radicado ${DOC.tracking_number}` }),
  );
  await waitFor(() => expect(document.querySelector(".doc-detail")).toBeTruthy());
}

describe("/bandeja — el detalle enseña los actores congelados del radicado", () => {
  it("una Salida con TRES destinatarios los muestra los tres, no solo el principal", async () => {
    mockDrawer(TRES_DESTINATARIOS);
    await abrirCajon();

    await waitFor(() => expect(screen.getByText("José Pérez")).toBeTruthy());
    // Los otros dos son justamente los que `sender_name` no podía representar.
    expect(screen.getByText("Ana Restrepo")).toBeTruthy();
    expect(screen.getByText("Carlos Mejía")).toBeTruthy();
    expect(screen.getByText(/Destinatarios \(3\)/)).toBeTruthy();
  });

  it("remitente y destinatario van en secciones distintas: son dos preguntas", async () => {
    mockDrawer([
      actor({ rol: "remitente", orden: 0, principal: true, nombre: "Ministerio de Educación" }),
      actor({ rol: "destinatario", orden: 0, principal: true, nombre: "José Pérez" }),
    ]);
    await abrirCajon();

    await waitFor(() => expect(screen.getByText(/Remitente \(1\)/)).toBeTruthy());
    expect(screen.getByText(/Destinatario \(1\)/)).toBeTruthy();
  });

  it("pide la copia CONGELADA del radicado, nunca la ficha vigente del catálogo", async () => {
    const llamadas = mockDrawer(TRES_DESTINATARIOS);
    await abrirCajon();

    await waitFor(() => expect(screen.getByText("Ana Restrepo")).toBeTruthy());
    expect(llamadas).toContain(`/bandeja/api/${RADICADO_ID}/actores`);
    // Ir a `/terceros/{tercero_id}` respondería «dónde vive hoy esa persona»,
    // que no es lo que el detalle de un radicado afirma.
    expect(llamadas.some((u) => u.includes("/terceros/"))).toBe(false);
  });

  it("control positivo: sin actores no inventa secciones ni rompe el cajón", async () => {
    mockDrawer([]);
    await abrirCajon();

    // Un Interno no tiene actores externos y es un caso legítimo, no un fallo.
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(document.querySelector(".actores-list")).toBeNull();
    expect(screen.queryByText(/Destinatarios? \(\d+\)/)).toBeNull();
    expect(screen.queryByText(/Remitentes? \(\d+\)/)).toBeNull();
  });
});
