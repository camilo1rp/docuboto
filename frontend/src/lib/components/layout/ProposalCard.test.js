// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ProposalCard — la tarjeta con la que una persona aprueba un acto que el
 * asistente preparó (E18/ADR-020, bloque 10).
 *
 * Lo que estos tests fijan es la forma canónica de `confirmBeforeWrite.test.js`
 * llevada a este caso: la acción solo se dispara desde el botón que está DENTRO
 * de la superficie que muestra los datos, y descartar no invoca nada. Aquí la
 * tarjeta ES esa superficie —no un disparador que abre otra— porque apilar un
 * modal encima obligaría a confirmar dos veces lo mismo, y una confirmación que
 * se repite se pulsa sin leer.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import ProposalCard from "./ProposalCard.svelte";

const REVERSIBLE = {
  id: "p1",
  tool: "vincular_radicado",
  operacion: "Vincular un radicado a un expediente",
  campos: [
    { etiqueta: "Expediente", valor: "EXP-2026-0001" },
    { etiqueta: "Número de radicado", valor: "2026-DEMO-E-000001" },
  ],
  irreversible: false,
  frase_requerida: null,
  token: "op1.aaa.bbb",
};

const IRREVERSIBLE = {
  ...REVERSIBLE,
  id: "p2",
  tool: "radicar_documento",
  operacion: "Radicar un documento de entrada, salida o interno",
  campos: [{ etiqueta: "Asunto", valor: "Tutela de Juan Pérez" }],
  irreversible: true,
  frase_requerida: "CONFIRMO",
  token: "op1.ccc.ddd",
};

describe("ProposalCard — nada se ejecuta hasta que la persona confirma", () => {
  it("muestra el acto con los datos exactos y no confirma sola", () => {
    const onConfirmar = vi.fn();
    render(ProposalCard, { props: { propuesta: REVERSIBLE, onConfirmar } });

    expect(screen.getByText("Vincular un radicado a un expediente")).toBeInTheDocument();
    expect(screen.getByText("2026-DEMO-E-000001")).toBeInTheDocument();
    expect(onConfirmar).not.toHaveBeenCalled();
  });

  it("confirmar entrega el token de la propuesta, no otra cosa", async () => {
    const onConfirmar = vi.fn();
    render(ProposalCard, { props: { propuesta: REVERSIBLE, onConfirmar } });

    await fireEvent.click(screen.getByTestId("proposal-confirm"));

    expect(onConfirmar).toHaveBeenCalledTimes(1);
    expect(onConfirmar).toHaveBeenCalledWith({ token: "op1.aaa.bbb", frase: "" });
  });

  it("descartar no invoca la confirmación", async () => {
    const onConfirmar = vi.fn();
    const onCancelar = vi.fn();
    render(ProposalCard, { props: { propuesta: REVERSIBLE, onConfirmar, onCancelar } });

    await fireEvent.click(screen.getByTestId("proposal-cancel"));

    expect(onCancelar).toHaveBeenCalledTimes(1);
    expect(onConfirmar).not.toHaveBeenCalled();
  });

  it("un acto reversible NO pide frase", () => {
    render(ProposalCard, { props: { propuesta: REVERSIBLE } });
    expect(screen.queryByTestId("proposal-phrase")).not.toBeInTheDocument();
    expect(screen.getByTestId("proposal-confirm")).not.toBeDisabled();
  });
});

describe("ProposalCard — un acto irreversible exige teclear la frase", () => {
  it("avisa de que no se deshace y deja el botón bloqueado", () => {
    render(ProposalCard, { props: { propuesta: IRREVERSIBLE } });

    expect(screen.getByText(/no se deshace/i)).toBeInTheDocument();
    expect(screen.getByTestId("proposal-phrase")).toBeInTheDocument();
    expect(screen.getByTestId("proposal-confirm")).toBeDisabled();
  });

  it("una frase equivocada no habilita el botón", async () => {
    render(ProposalCard, { props: { propuesta: IRREVERSIBLE } });

    await fireEvent.input(screen.getByTestId("proposal-phrase"), {
      target: { value: "sí, adelante" },
    });
    await tick();

    expect(screen.getByTestId("proposal-confirm")).toBeDisabled();
  });

  it("con la frase exacta se habilita y viaja al confirmar", async () => {
    const onConfirmar = vi.fn();
    render(ProposalCard, { props: { propuesta: IRREVERSIBLE, onConfirmar } });

    await fireEvent.input(screen.getByTestId("proposal-phrase"), {
      target: { value: "CONFIRMO" },
    });
    await tick();

    const btn = screen.getByTestId("proposal-confirm");
    expect(btn).not.toBeDisabled();
    await fireEvent.click(btn);

    expect(onConfirmar).toHaveBeenCalledWith({ token: "op1.ccc.ddd", frase: "CONFIRMO" });
  });

  it("el bloqueo del botón es cortesía, no la barrera", () => {
    // Dicho en un test para que nadie lo tome por lo que no es: la frase la
    // revalida el backend a partir del TOKEN FIRMADO, que es quien sabe si el
    // acto es irreversible. Deshabilitar aquí solo evita mandar a la persona
    // contra un error previsible; el navegador es la parte hostil (D-05).
    render(ProposalCard, { props: { propuesta: IRREVERSIBLE } });
    expect(screen.getByTestId("proposal-phrase")).toBeInTheDocument();
  });
});

describe("ProposalCard — el estado que ve la persona no miente", () => {
  it("mientras se ejecuta no se puede volver a pulsar", () => {
    render(ProposalCard, { props: { propuesta: REVERSIBLE, estado: "ejecutando" } });
    expect(screen.getByTestId("proposal-confirm")).toBeDisabled();
  });

  it("hecho: desaparecen los botones — no se confirma dos veces", () => {
    render(ProposalCard, { props: { propuesta: REVERSIBLE, estado: "hecho" } });
    expect(screen.queryByTestId("proposal-confirm")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/hecho/i);
  });

  it("descartado dice explícitamente que no se hizo nada", () => {
    render(ProposalCard, { props: { propuesta: REVERSIBLE, estado: "cancelado" } });
    expect(screen.getByText(/no se hizo nada/i)).toBeInTheDocument();
  });

  it("error: muestra el motivo del backend, no un genérico", () => {
    render(ProposalCard, {
      props: {
        propuesta: REVERSIBLE,
        estado: "error",
        detalleError: "La propuesta caducó.",
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("La propuesta caducó.");
  });
});

describe("ProposalCard — el contenido es DATO, nunca marcado", () => {
  it("un valor con HTML se muestra literal y no se interpreta", () => {
    // Los valores los compuso el modelo a partir de la conversación, que puede
    // incluir el contenido de un radicado de Entrada: texto de un tercero. Es
    // el último sitio donde convendría relajar el escapado, porque un texto
    // interpretado aquí serviría para que alguien apruebe una cosa creyendo
    // aprobar otra.
    const { container } = render(ProposalCard, {
      props: {
        propuesta: {
          ...REVERSIBLE,
          campos: [{ etiqueta: "Asunto", valor: "<img src=x onerror=alert(1)>Inocuo" }],
        },
      },
    });

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>Inocuo")).toBeInTheDocument();
  });
});
