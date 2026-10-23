// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Reservar o clasificar exige fundamento jurídico, y la pantalla lo dice.
 *
 * El defecto: el campo se ofrecía como «Fundamento jurídico (opcional)» y el
 * backend lo rechazaba con 422 —«fundamento_juridico es obligatorio al
 * reclasificar a nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28)»—. Quien
 * seguía la pantalla al pie de la letra **no podía clasificar ni un solo
 * radicado**, y el aviso genérico («Error 422 al reclasificar el nivel de
 * seguridad») no dejaba averiguar qué faltaba.
 *
 * No es un detalle de formulario: la reserva de un documento público es un acto
 * motivado en una norma concreta, y un campo marcado «opcional» invita
 * precisamente a omitir esa norma.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
let currentPageStore;

vi.mock("$app/navigation", () => ({ goto: (...args) => gotoMock(...args) }));
vi.mock("$app/stores", () => {
  currentPageStore = writable({ url: new URL("http://localhost/bandeja") });
  return { page: currentPageStore, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const DOC = {
  id: "d86a41ee-b044-4bfc-a9bb-1314e7adf6c7",
  tracking_number: "2026-DEMO-E-000001",
  subject: "Tutela de Juan Perez sobre certificado",
  doc_type: "E",
  status: "in_progress",
  nivel_seguridad: 1,
};

// Catálogo cerrado de causales (SSR, GET /documents/causales-reserva): sin él el
// modal no ofrece reservar ni clasificar.
const CATALOGO_RESERVA = {
  nivel_doble_firma: 3,
  plazo_max_meses: 180,
  causales: [
    {
      codigo: "art19_b_seguridad_publica",
      articulo: "Ley 1712/2014 art. 19",
      literal: "b",
      descripcion: "Seguridad pública",
      nivel: 2,
      plazo_max_meses: 180,
    },
    {
      codigo: "art18_a_intimidad",
      articulo: "Ley 1712/2014 art. 18",
      literal: "a",
      descripcion: "Intimidad",
      nivel: 3,
      plazo_max_meses: null,
    },
  ],
};

function baseData() {
  return {
    catalogoReserva: CATALOGO_RESERVA,
    documents: [DOC],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
  };
}

const originalFetch = globalThis.fetch;
let enviados;

beforeEach(() => {
  enviados = [];
  initSession({
    user: { sub: "u1", name: "Jefe", tenant_slug: "demo" },
    permissions: ["PERM_RECLASIFICAR", "PERM_RADI"],
    permission_levels: { PERM_RECLASIFICAR: 3, PERM_RADI: 3 },
  });
  globalThis.fetch = vi.fn(async (url, init) => {
    if (init?.method === "POST") {
      enviados.push({ url: String(url), body: JSON.parse(init.body) });
      return new Response(JSON.stringify({ ...DOC, nivel_seguridad: 2 }), { status: 200 });
    }
    if (String(url).includes(`/bandeja/api/${DOC.id}`)) {
      return new Response(JSON.stringify(DOC), { status: 200 });
    }
    return new Response(JSON.stringify({ documents: [DOC], total: 1 }), { status: 200 });
  });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

/** Elige Reservada con causal del art. 19 y su plazo (los datos de reserva distintos del fundamento). */
async function elegirReservada(nivel) {
  await fireEvent.change(nivel, { target: { value: "2" } });
  await waitFor(() => expect(document.getElementById("reclasificar-causal")).toBeTruthy());
  await fireEvent.change(document.getElementById("reclasificar-causal"), {
    target: { value: "art19_b_seguridad_publica" },
  });
  await waitFor(() => expect(document.getElementById("reclasificar-plazo")).toBeTruthy());
  await fireEvent.input(document.getElementById("reclasificar-plazo"), {
    target: { value: "60" },
  });
}

/** Abre el drawer y el modal de reclasificación sobre el radicado. */
async function abrirModal() {
  currentPageStore.set({ url: new URL(`http://localhost/bandeja?radicado=${DOC.id}`) });
  render(BandejaPage, { props: { data: baseData() } });
  const boton = await screen.findByRole("button", { name: /reclasificar nivel/i });
  await fireEvent.click(boton);
  await waitFor(() => expect(document.getElementById("reclasificar-nivel")).toBeTruthy());
  return /** @type {HTMLSelectElement} */ (document.getElementById("reclasificar-nivel"));
}

describe("/bandeja — reclasificar exige el fundamento jurídico", () => {
  it("al elegir Reservada, el fundamento NO se ofrece como opcional", async () => {
    const nivel = await abrirModal();
    await elegirReservada(nivel);

    const etiquetas = screen.getAllByText(/fundamento jurídico/i).map((e) => e.textContent);
    expect(
      etiquetas.some((t) => /opcional/i.test(t)),
      `la etiqueta sigue diciendo opcional: ${etiquetas.join(" | ")}`,
    ).toBe(false);
  });

  it("sin fundamento, el envío se detiene y se explica por qué", async () => {
    const nivel = await abrirModal();
    await elegirReservada(nivel);
    await fireEvent.input(document.getElementById("reclasificar-motivo"), {
      target: { value: "Contiene datos personales sensibles." },
    });
    await fireEvent.click(screen.getByRole("button", { name: /confirmar reclasificación/i }));

    await waitFor(() =>
      expect(screen.getByText(/fundamento jurídico es obligatorio/i)).toBeInTheDocument(),
    );
    expect(enviados, "no debe gastarse una petición que el backend ya va a rechazar").toEqual([]);
  });

  it("con fundamento, la petición sale y lo lleva dentro", async () => {
    // Control positivo: la corrección no puede consistir en bloquear el acto.
    const nivel = await abrirModal();
    await elegirReservada(nivel);
    await fireEvent.input(document.getElementById("reclasificar-motivo"), {
      target: { value: "Contiene datos personales sensibles." },
    });
    await fireEvent.input(document.getElementById("reclasificar-fundamento"), {
      target: { value: "Ley 1712/2014, art. 18 literal a" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /confirmar reclasificación/i }));

    await waitFor(() => expect(enviados.length).toBe(1));
    expect(enviados[0].body.fundamento_juridico).toBe("Ley 1712/2014, art. 18 literal a");
    expect(enviados[0].body.nivel_seguridad).toBe(2);
  });

  it("volver a Pública NO exige fundamento", async () => {
    // Gemelo: desclasificar no es el mismo acto que reservar, y exigirle el
    // fundamento de una reserva que se está levantando sería el defecto al
    // revés — el que impide corregir una clasificación equivocada.
    const nivel = await abrirModal();
    await fireEvent.change(nivel, { target: { value: "1" } });
    await fireEvent.input(document.getElementById("reclasificar-motivo"), {
      target: { value: "Cesó la causa de reserva." },
    });
    await fireEvent.click(screen.getByRole("button", { name: /confirmar reclasificación/i }));

    await waitFor(() => expect(enviados.length).toBe(1));
    expect(enviados[0].body.nivel_seguridad).toBe(1);
  });
});
