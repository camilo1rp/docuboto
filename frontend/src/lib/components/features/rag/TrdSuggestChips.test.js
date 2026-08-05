// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * TrdSuggestChips.test.js — F6 frontend (E21 Inc.2 Patrón A).
 *
 * Cubre la regla dura #2 de la tarea: la serie/tipo sugerido NUNCA se autoaplica.
 * El componente solo dispara `apply` cuando el usuario pulsa el chip; renderizar
 * sugerencias no debe, por sí solo, mutar ningún campo del formulario padre.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import TrdSuggestChips from "./TrdSuggestChips.svelte";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("TrdSuggestChips", () => {
  it("con query corta no llama al proxy y no muestra chips", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
    render(TrdSuggestChips, { props: { endpoint: "/radicar/api/rag/antecedentes", query: "ab" } });
    // Da tiempo a que el debounce (si se disparara) se resuelva.
    await new Promise((r) => setTimeout(r, 500));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renderiza chips a partir de `suggestions` y NUNCA aplica sola la sugerencia", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        suggestions: [
          { doc_class: "CDP", count: 3, best_score: 0.9, example_ref: "2024-ICETEX-E-000001" },
        ],
        antecedentes: [],
        advisory: "aviso",
      }),
    );

    let applied = null;
    const { component } = render(TrdSuggestChips, {
      props: { endpoint: "/radicar/api/rag/antecedentes", query: "asunto de prueba largo" },
    });
    component.$on("apply", (e) => (applied = e.detail));

    const chip = await waitFor(() => screen.getByRole("button", { name: /CDP/ }));
    // Solo por haberse renderizado la sugerencia, no debe haberse aplicado nada.
    expect(applied).toBeNull();

    await fireEvent.click(chip);
    expect(applied).toEqual({ docClass: "CDP" });
  });

  // Revisión UX lote 2, punto 5: los chips aparecen tras el debounce sin
  // anuncio a lector de pantalla — necesitan aria-live="polite".
  it('el contenedor de chips es una región aria-live="polite"', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        suggestions: [
          { doc_class: "CDP", count: 3, best_score: 0.9, example_ref: "2024-ICETEX-E-000001" },
        ],
        antecedentes: [],
        advisory: "aviso",
      }),
    );
    render(TrdSuggestChips, {
      props: { endpoint: "/radicar/api/rag/antecedentes", query: "asunto de prueba largo" },
    });
    const group = await waitFor(() =>
      screen.getByRole("group", { name: /tipo documental sugerido/i }),
    );
    expect(group).toHaveAttribute("aria-live", "polite");
  });

  it("un fallo del proxy no muestra chips ni lanza (feature decorativa/opcional)", async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ error: "connection_error" }, 503));
    render(TrdSuggestChips, {
      props: { endpoint: "/radicar/api/rag/antecedentes", query: "asunto de prueba largo" },
    });
    await new Promise((r) => setTimeout(r, 500));
    expect(screen.queryByRole("button")).toBeNull();
  });
});
