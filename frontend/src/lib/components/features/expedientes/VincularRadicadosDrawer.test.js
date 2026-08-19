// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * VincularRadicadosDrawer.test.js — cierra dos hallazgos de la revisión UX de
 * Fase 2 (expedientes):
 *
 *   Crítico 2: la selección no debe perderse ni desincronizarse entre
 *   búsquedas ("Vincular (4)" que en realidad vincula 1, o no hace nada).
 *
 *   Alto A1: el paginador debe reflejar el total real del backend (`total`),
 *   no el tamaño de la página servida, y `pageChange` debe re-consultar.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import VincularRadicadosDrawer from "./VincularRadicadosDrawer.svelte";

function hit(id, overrides = {}) {
  return {
    id,
    tracking_number: `2024-E-${id}`,
    doc_type: "E",
    subject: `Asunto ${id}`,
    dest_dept: "Jurídica",
    status: "registered",
    ...overrides,
  };
}

function mockSearchResponse(results, total) {
  return {
    ok: true,
    json: async () => ({ results, total }),
  };
}

describe("VincularRadicadosDrawer — selección persistente entre búsquedas (Crítico 2)", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function search(query) {
    await fireEvent.input(screen.getByRole("textbox", { name: "Buscar radicados" }), {
      target: { value: query },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => expect(screen.queryByText(/Buscando|Sin resultados/)).not.toBeNull);
  }

  it("una segunda búsqueda NO descarta lo ya seleccionado en la primera", async () => {
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("a"), hit("b")], 2));
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });

    await search("primera");
    await waitFor(() => screen.getByText("2024-E-a"));
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 1"));
    expect(screen.getByText(/1 radicado seleccionado/)).toBeInTheDocument();

    // Segunda búsqueda: resultados completamente distintos.
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("c"), hit("d")], 2));
    await search("segunda");
    await waitFor(() => screen.getByText("2024-E-c"));

    // El radicado "a" sigue en la selección revisable aunque ya no esté en
    // la página visible — antes de la corrección, esta selección se perdía
    // en silencio y el botón/contador quedaban desincronizados.
    expect(screen.getByText(/1 radicado seleccionado/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Vincular$/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Quitar 2024-E-a de la selección")).toBeInTheDocument();
  });

  it("el botón Vincular nunca ofrece un conteo mayor que lo que realmente vinculará", async () => {
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("a"), hit("b")], 2));
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });
    await search("qq");
    await waitFor(() => screen.getByText("2024-E-a"));

    await fireEvent.click(screen.getByLabelText("Seleccionar fila 1"));
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 2"));

    // Nueva búsqueda que no repite ninguno de los ya seleccionados.
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("e")], 1));
    await search("otra");
    await waitFor(() => screen.getByText("2024-E-e"));

    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ vinculados: 2, omitidos: 0, total: 2 }),
    });
    await fireEvent.click(screen.getByRole("button", { name: /Vincular \(2\)/ }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(3));
    const batchCall = global.fetch.mock.calls.find((c) =>
      String(c[0]).includes("/radicados/batch"),
    );
    expect(batchCall).toBeTruthy();
    const body = JSON.parse(batchCall[1].body);
    // Los DOS radicados de la primera búsqueda se vinculan realmente — no se
    // descartan en silencio por ya no estar en `results` tras la 2ª búsqueda.
    expect(body.radicados.map((r) => r.radicado_id).sort()).toEqual(["a", "b"]);
  });

  it("permite quitar un elemento de la lista revisable de seleccionados", async () => {
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("a")], 1));
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });
    await search("qq");
    await waitFor(() => screen.getByText("2024-E-a"));
    await fireEvent.click(screen.getByLabelText("Seleccionar fila 1"));
    expect(screen.getByText(/1 radicado seleccionado/)).toBeInTheDocument();

    await fireEvent.click(screen.getByLabelText("Quitar 2024-E-a de la selección"));
    expect(screen.getByText(/0 radicados seleccionados/)).toBeInTheDocument();
  });
});

describe("VincularRadicadosDrawer — paginación real contra el total del backend (Alto A1)", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("usa `total` del backend para el paginador, no el tamaño de la página", async () => {
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("a"), hit("b")], 137));
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });

    await fireEvent.input(screen.getByRole("textbox", { name: "Buscar radicados" }), {
      target: { value: "muchos" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => screen.getByText("2024-E-a"));

    // 2 resultados en esta página, pero 137 en total — el paginador debe
    // reflejar 137, no "1–2 de 2" con las flechas deshabilitadas.
    expect(screen.getByText(/de 137/)).toBeInTheDocument();
    expect(screen.getByLabelText("Página siguiente")).not.toBeDisabled();
  });

  it("al cambiar de página vuelve a consultar el backend con el `page` correcto", async () => {
    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("a")], 60));
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });
    await fireEvent.input(screen.getByRole("textbox", { name: "Buscar radicados" }), {
      target: { value: "qq" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => screen.getByText("2024-E-a"));

    global.fetch.mockResolvedValueOnce(mockSearchResponse([hit("z")], 60));
    await fireEvent.click(screen.getByLabelText("Página siguiente"));
    await waitFor(() => screen.getByText("2024-E-z"));

    const secondCallUrl = String(global.fetch.mock.calls[1][0]);
    expect(secondCallUrl).toContain("page=2");
  });
});

describe("VincularRadicadosDrawer — el estado se lee en español", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * La tabla pintaba `status` tal como lo nombra la API: quien buscaba un
   * radicado para vincularlo leía «in_progress», mientras la bandeja —la misma
   * información, dos clics más allá— dice «En trámite». `etiquetaEstadoRadicado`
   * ya existía, con su propio test; lo que faltaba era leerla aquí. Es el mismo
   * patrón que el UUID de la serie TRD en la ficha del expediente: el dato
   * resuelto existe y el sitio visible no lo usa.
   */
  it("traduce el estado que devuelve la API", async () => {
    global.fetch.mockResolvedValueOnce(
      mockSearchResponse([hit("a", { status: "in_progress" })], 1),
    );
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });
    await fireEvent.input(screen.getByRole("textbox", { name: "Buscar radicados" }), {
      target: { value: "2024" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => expect(screen.getByText("En trámite")).toBeInTheDocument());
    expect(screen.queryByText("in_progress")).toBeNull();
  });

  it("un estado que el mapa no conoce se muestra crudo, no en blanco", async () => {
    global.fetch.mockResolvedValueOnce(
      mockSearchResponse([hit("b", { status: "en_revision" })], 1),
    );
    render(VincularRadicadosDrawer, { props: { open: true, expedienteId: "exp-1" } });
    await fireEvent.input(screen.getByRole("textbox", { name: "Buscar radicados" }), {
      target: { value: "2024" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await waitFor(() => expect(screen.getByText("en_revision")).toBeInTheDocument());
  });
});
