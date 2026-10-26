// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Si el widget no se puede cargar (script bloqueado, navegador antiguo), la
 * página lo dice, nombra los canales alternos y NO envía nada: sin reto no hay
 * radicación en línea.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

vi.mock("altcha/altcha.css", () => ({}));
vi.mock("altcha/external", () => {
  throw new Error("módulo bloqueado");
});
vi.mock("altcha/i18n/es-419", () => ({}));
vi.mock("altcha/worker?worker", () => ({ default: class {} }));

import Pagina from "./+page.svelte";

const datos = {
  tipos: [{ code: "DP-GEN", nombre: "Derecho de petición" }],
  habilitada: true,
  politica: { publicada: true, verificable: true, version: "2026-03", url: "" },
};

afterEach(() => vi.restoreAllMocks());

describe("/radicacion — el widget no carga", () => {
  it("avisa con los canales alternos y no hace ninguna petición", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    render(Pagina, { data: datos });
    expect(await screen.findByText(/No se pudo cargar la verificación automática/)).toBeTruthy();
    expect(document.querySelector("#altcha-alternos")).toHaveTextContent(/presencial/i);

    fireEvent.input(screen.getByLabelText(/Nombre/i, { selector: "#nombre" }), {
      target: { value: "Ana" },
    });
    fireEvent.input(screen.getByLabelText("Asunto"), { target: { value: "a" } });
    fireEvent.input(screen.getByLabelText("Descripción"), { target: { value: "b" } });
    await fireEvent.click(screen.getByLabelText(/Autorizo a la entidad/i));
    await fireEvent.submit(document.querySelector("form"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/presencial/i));
    expect(f).not.toHaveBeenCalled();
  });
});
