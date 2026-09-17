// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La bandeja no ofrece abrir una cadena de firmas sobre una ENTRADA.
 *
 * Una cadena de firmas se abre sobre lo que la entidad PRODUCE —una Salida o un
 * Interno—. Sobre una Entrada no: ese documento lo firmó quien lo envió, y que
 * la entidad receptora lo firme no significa nada.
 *
 * Dos ejes distintos y ambos necesarios, como en la anulación: el tipo dice si
 * el acto es POSIBLE sobre este documento, el permiso si esta persona está
 * AUTORIZADA. Por eso los tres primeros conceden los dos permisos: lo que se
 * mide ahí es la regla del tipo.
 *
 * Y un tercero, propio de este acto: un radicado ANULADO no recoge firmas.
 *
 * OJO CON EL VALOR: el estado es `anulado` —lo escribe document-service con
 * `UPDATE radicados SET status='anulado'`—, NO `cancelled`, que pertenece al
 * vocabulario de los pasos de flujo. La primera versión de esta guarda comparaba
 * con `cancelled` y su test también, así que pasaba en verde sobre una guarda
 * que no excluía nada: un test que codifica el valor equivocado es peor que su
 * ausencia. Lo delató ir a implementar la barrera del backend y mirar qué
 * escribe de verdad la anulación. Ver `radicado-estados.test.js`, que censa el
 * vocabulario contra el servicio para que no vuelva a divergir.
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

const ID = "11111111-1111-1111-1111-111111111111";
const BOTON = /Cadena de firmas/i;

function doc(doc_type, status = "registered") {
  return {
    id: ID,
    tracking_number: `2026-ICETEX-${doc_type}-000001`,
    doc_type,
    subject: "Asunto de prueba",
    status,
    registered_at: "2026-07-01T10:00:00Z",
    origin_dept: "Jurídica",
    dest_dept: null,
    pages: 1,
  };
}

function data(doc_type, status) {
  return {
    documents: [doc(doc_type, status)],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [{ nombre: "Jurídica", sigla: "JUR" }],
    initialFilters: { docType: "", status: "", page: 1 },
  };
}

const originalFetch = globalThis.fetch;

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

/** El cajón consulta el detalle y la cola de anulaciones al abrirse. */
function mockDrawerFetch(detalle) {
  globalThis.fetch = vi.fn(async (url) => {
    const u = url.toString();
    if (u === `/bandeja/api/${ID}`) return jsonResponse(detalle);
    if (u.startsWith(`/anulaciones/api/${ID}`)) return jsonResponse({}, 404);
    if (u === `/bandeja/api/${ID}/current`) return jsonResponse({ error: "no_active_step" }, 404);
    // El modal solo pide su catálogo cuando se abre; aquí no se abre.
    return jsonResponse({}, 404);
  });
}

function entrar(permissions) {
  initSession({
    sub: "u-1",
    email: "operador@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  });
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

/** Abre el cajón del único radicado de la lista y devuelve cuando está pintado. */
async function abrirCajon(d) {
  mockDrawerFetch(d.documents[0]);
  render(BandejaPage, { props: { data: d } });
  await fireEvent.click(screen.getByLabelText(/Ver detalle de radicado/i));
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
}

describe("bandeja — la cadena de firmas depende del TIPO y del permiso", () => {
  it("una ENTRADA no ofrece abrir cadena, aun teniendo los dos permisos", async () => {
    entrar(["PERM_RADI", "PERM_FIRMA"]);
    await abrirCajon(data("E"));

    await waitFor(() => expect(screen.getByText("Asunto de prueba")).toBeTruthy());
    expect(screen.queryByRole("button", { name: BOTON })).toBeNull();
  });

  it("una SALIDA sí lo ofrece", async () => {
    // Control positivo: sin él, una regla que negara siempre dejaría el test de
    // arriba en verde y el acto inalcanzable para todo el sistema.
    entrar(["PERM_RADI", "PERM_FIRMA"]);
    await abrirCajon(data("S"));

    expect(await screen.findByRole("button", { name: BOTON })).toBeInTheDocument();
  });

  it("un INTERNO también lo ofrece", async () => {
    entrar(["PERM_RADI", "PERM_FIRMA"]);
    await abrirCajon(data("I"));

    expect(await screen.findByRole("button", { name: BOTON })).toBeInTheDocument();
  });

  it("sin PERM_FIRMA no lo ofrece ni sobre una Salida", async () => {
    // Ofrecerlo sería un botón destinado al 403: el router de cadenas lo exige.
    entrar(["PERM_RADI"]);
    await abrirCajon(data("S"));

    await waitFor(() => expect(screen.getByText("Asunto de prueba")).toBeTruthy());
    expect(screen.queryByRole("button", { name: BOTON })).toBeNull();
  });

  it("un radicado ANULADO no lo ofrece", async () => {
    entrar(["PERM_RADI", "PERM_FIRMA"]);
    await abrirCajon(data("S", "anulado"));

    await waitFor(() => expect(screen.getByText("Asunto de prueba")).toBeTruthy());
    expect(screen.queryByRole("button", { name: BOTON })).toBeNull();
  });
});
