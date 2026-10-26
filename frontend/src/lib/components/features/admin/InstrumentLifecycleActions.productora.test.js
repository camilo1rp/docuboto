// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * Ratificar una serie `migrada` (C-4): nació sin productora, así que el acto de
 * convalidar la declara (una sola vez). Control positivo y negativo del selector.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import InstrumentLifecycleActions from "./InstrumentLifecycleActions.svelte";

const DEPS = [
  { codigo: 12, nombre: "Secretaría General" },
  { codigo: 40, nombre: "Talento Humano" },
];
const migrada = (extra = {}) => ({
  id: "s",
  code: "10",
  version: 1,
  estado: "migrada",
  dependencias_productoras: [],
  ...extra,
});

afterEach(() => vi.restoreAllMocks());

async function abrirConvalidar(props) {
  render(InstrumentLifecycleActions, {
    props: {
      basePath: "/admin/trd/api",
      canWrite: true,
      instrumentLabel: "TRD",
      dependencias: DEPS,
      ...props,
    },
  });
  await fireEvent.click(screen.getByRole("button", { name: /convalidar|ratificar/i }));
}

describe("convalidar una serie migrada", () => {
  it("sin productora declarada: ofrece el selector y no deja confirmar sin elegir (control positivo)", async () => {
    await abrirConvalidar({ item: migrada() });
    expect(screen.getByRole("group", { name: /productora/i })).toBeInTheDocument();
    await fireEvent.input(screen.getByLabelText(/Acto administrativo/), {
      target: { value: "Res 1" },
    });
    await fireEvent.input(screen.getByLabelText(/Fecha de convalidación/), {
      target: { value: "2026-07-01" },
    });
    await fireEvent.input(screen.getByLabelText(/Instancia convalidante/), {
      target: { value: "Consejo" },
    });
    const confirmar = screen.getByRole("button", { name: "Confirmar convalidación" });
    expect(confirmar).toBeDisabled();

    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ id: "s", estado: "convalidada" }), { status: 200 }),
    );
    await fireEvent.click(screen.getByRole("checkbox", { name: /Talento Humano/ }));
    expect(confirmar).toBeEnabled();
    await fireEvent.click(confirmar);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(JSON.parse(globalThis.fetch.mock.calls[0][1].body).dependencias_productoras).toEqual([
      40,
    ]);
  });

  it("con productora ya declarada NO ofrece el selector (el backend daría 422 productora_ya_declarada)", async () => {
    await abrirConvalidar({
      item: migrada({
        dependencias_productoras: [{ dependencia_codigo: 12, dependencia_nombre: "SG" }],
      }),
    });
    expect(screen.queryByRole("group", { name: /productora/i })).not.toBeInTheDocument();
  });

  it("una serie aprobada tampoco lo ofrece (422 productora_solo_en_ratificacion)", async () => {
    await abrirConvalidar({ item: migrada({ estado: "aprobada" }) });
    expect(screen.queryByRole("group", { name: /productora/i })).not.toBeInTheDocument();
  });

  it("una TVD (otro instrumento) nunca lo ofrece", async () => {
    await abrirConvalidar({ item: migrada(), instrumentLabel: "TVD", basePath: "/admin/tvd/api" });
    expect(screen.queryByRole("group", { name: /productora/i })).not.toBeInTheDocument();
  });
});
