// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * RagSuggestButton.test.js — F6 frontend (E21 Inc.3).
 *
 * Cubre las TRES reglas duras de la tarea aplicadas a este componente:
 *   1. Sin citas (o sin `answer`), NUNCA se ofrece insertar — y si se ofrece,
 *      el HTML insertado SIEMPRE trae la marca "generado por IA" + citas.
 *   2. (N/A aquí — es de TrdSuggestChips)
 *   3. Un fallo del servicio de conocimiento se queda dentro del panel del
 *      componente; nunca lanza ni bloquea nada fuera de él.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import RagSuggestButton from "./RagSuggestButton.svelte";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("RagSuggestButton", () => {
  it("el botón está deshabilitado si la consulta es demasiado corta", () => {
    render(RagSuggestButton, { props: { endpoint: "/radicar/api/rag/suggest", query: "a" } });
    expect(screen.getByRole("button", { name: /sugerir con antecedentes/i })).toBeDisabled();
  });

  it("respuesta CON citas: ofrece insertar, y el HTML insertado trae marca + citas", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        answer: "El plazo de respuesta es de 15 días hábiles.",
        citations: [
          {
            cited_text: "el plazo es de 15 días",
            source_type: "radicado",
            source_ref: "2024-ICETEX-E-000123",
            score: 0.91,
          },
        ],
        proveedor: "local_st",
        modelo: "claude",
        confianza: 0.8,
        suggestion_id: "55555555-5555-5555-5555-555555555555",
        advisory: "aviso",
      }),
    );
    globalThis.fetch = fetchMock;

    const { component } = render(RagSuggestButton, {
      props: { endpoint: "/radicar/api/rag/suggest", query: "asunto suficientemente largo" },
    });
    let inserted = null;
    component.$on("insert", (e) => (inserted = e.detail));

    await fireEvent.click(screen.getByRole("button", { name: /sugerir con antecedentes/i }));
    await waitFor(() => screen.getByRole("button", { name: /insertar en el documento/i }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/radicar/api/rag/suggest",
      expect.objectContaining({ method: "POST" }),
    );

    await fireEvent.click(screen.getByRole("button", { name: /insertar en el documento/i }));

    expect(inserted).not.toBeNull();
    expect(inserted.html).toContain("Sugerencia generada por IA");
    expect(inserted.html).toContain("2024-ICETEX-E-000123");
    expect(inserted.html).toContain("El plazo de respuesta es de 15 días hábiles.");
  });

  it("respuesta SIN citas (answer null, Patrón A): NUNCA ofrece insertar", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        answer: null,
        citations: [],
        proveedor: "disabled",
        modelo: null,
        confianza: 0,
        suggestion_id: null,
        advisory: "aviso",
      }),
    );

    const { component } = render(RagSuggestButton, {
      props: { endpoint: "/radicar/api/rag/suggest", query: "asunto suficientemente largo" },
    });
    let insertCalled = false;
    component.$on("insert", () => (insertCalled = true));

    await fireEvent.click(screen.getByRole("button", { name: /sugerir con antecedentes/i }));
    await waitFor(() => screen.getByText(/no se pudo fundamentar una respuesta con citas/i));

    expect(screen.queryByRole("button", { name: /insertar en el documento/i })).toBeNull();
    expect(insertCalled).toBe(false);
  });

  it("respuesta con answer pero SIN citas: tampoco ofrece insertar (afirmación sin fuente)", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        answer: "texto sin respaldo",
        citations: [],
        proveedor: "local_st",
        modelo: "claude",
        confianza: 0,
        suggestion_id: null,
        advisory: "aviso",
      }),
    );

    render(RagSuggestButton, {
      props: { endpoint: "/radicar/api/rag/suggest", query: "asunto suficientemente largo" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /sugerir con antecedentes/i }));
    await waitFor(() => screen.getByText(/no se pudo fundamentar una respuesta con citas/i));
    expect(screen.queryByRole("button", { name: /insertar en el documento/i })).toBeNull();
  });

  // Revisión UX lote 2, punto 5: el panel aparece tras el click sin ningún
  // anuncio a lector de pantalla — necesita aria-live="polite".
  it('el panel de la sugerencia es una región aria-live="polite"', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        answer: "texto",
        citations: [
          {
            cited_text: "cita",
            source_type: "radicado",
            source_ref: "2024-ICETEX-E-000001",
            score: 0.5,
          },
        ],
        proveedor: "local_st",
        modelo: "claude",
        confianza: 0.5,
        suggestion_id: "55555555-5555-5555-5555-555555555555",
        advisory: "aviso",
      }),
    );
    render(RagSuggestButton, {
      props: { endpoint: "/radicar/api/rag/suggest", query: "asunto suficientemente largo" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /sugerir con antecedentes/i }));
    const panel = await waitFor(() =>
      screen.getByRole("region", { name: /sugerencia generada por ia/i }),
    );
    expect(panel).toHaveAttribute("aria-live", "polite");
  });

  it("regla dura #3: un fallo del servicio se queda en el panel, el componente no lanza", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });

    render(RagSuggestButton, {
      props: { endpoint: "/radicar/api/rag/suggest", query: "asunto suficientemente largo" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /sugerir con antecedentes/i }));
    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert").textContent).toMatch(/no se pudo conectar/i);
    // El botón sigue siendo usable (no quedó bloqueado por la excepción).
    expect(screen.getByRole("button", { name: /sugerir con antecedentes/i })).toBeEnabled();
  });
});
