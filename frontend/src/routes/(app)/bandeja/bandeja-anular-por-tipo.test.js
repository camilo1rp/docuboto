// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La bandeja no ofrece anular un radicado de ENTRADA.
 *
 * Un radicado de Entrada no se anula: si el documento entró a la entidad,
 * entró, y eso no se deja sin efecto. La anulación existe para lo que la
 * entidad emite —Salida o Interno—, donde sí cabe anular lo emitido.
 *
 * Es un eje DISTINTO del permiso, y por eso estos tests conceden
 * `PERM_SOL_ANULAR` en todos los casos: `canAnular` responde a «¿está
 * autorizada esta persona?» y la regla de tipo a «¿es posible sobre este
 * documento?». Antes solo se comprobaba lo primero, así que la pantalla ofrecía
 * el botón sobre una Entrada.
 *
 * La barrera de verdad está en el backend (`app/core/reglas_tipo.py`, 409
 * `acto_no_aplica_al_tipo`), que no la salta ni ROOT. Esto es lo que evita
 * ofrecer un botón destinado a ser rechazado.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function doc(doc_type) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    tracking_number: `2026-ICETEX-${doc_type}-000001`,
    doc_type,
    subject: "Asunto de prueba",
    status: "registered",
    registered_at: "2026-07-01T10:00:00Z",
    origin_dept: "Ventanilla Única",
    dest_dept: null,
    pages: 1,
  };
}

function data(doc_type) {
  return {
    documents: [doc(doc_type)],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [{ nombre: "Jurídica", sigla: "JUR" }],
    initialFilters: { docType: "", status: "", page: 1 },
  };
}

/** Con el permiso CONCEDIDO: lo que se prueba es la regla del tipo, no el RBAC. */
function loginConPermisoDeAnulacion() {
  initSession({
    sub: "u-1",
    email: "operador@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["PERM_SOL_ANULAR", "PERM_RADI"],
  });
}

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

const BOTON = /Solicitar anulación del radicado/i;

describe("bandeja — anular depende del TIPO, no solo del permiso", () => {
  it("una ENTRADA no ofrece el botón de anular, aun teniendo el permiso", () => {
    loginConPermisoDeAnulacion();
    render(BandejaPage, { props: { data: data("E") } });

    expect(screen.queryByLabelText(BOTON)).not.toBeInTheDocument();
  });

  it("una SALIDA sí lo ofrece", () => {
    // Control positivo: sin él, una regla que negara siempre dejaría el test de
    // arriba en verde y la anulación inalcanzable para todo el sistema.
    loginConPermisoDeAnulacion();
    render(BandejaPage, { props: { data: data("S") } });

    expect(screen.getByLabelText(BOTON)).toBeInTheDocument();
  });

  it("un INTERNO también lo ofrece", () => {
    loginConPermisoDeAnulacion();
    render(BandejaPage, { props: { data: data("I") } });

    expect(screen.getByLabelText(BOTON)).toBeInTheDocument();
  });

  it("sin el permiso no lo ofrece ni siquiera sobre una Salida", () => {
    // Los dos ejes son necesarios, no alternativos: el tipo lo hace posible y el
    // permiso lo hace autorizado. Que falte cualquiera de los dos basta.
    initSession({
      sub: "u-2",
      email: "consulta@orpyca.com",
      name: "Consulta",
      preferred_username: "consulta",
      roles: [],
      tenant_slug: "icetex",
      permissions: ["PERM_RADI"],
    });
    render(BandejaPage, { props: { data: data("S") } });

    expect(screen.queryByLabelText(BOTON)).not.toBeInTheDocument();
  });
});
