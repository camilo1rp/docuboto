// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import FormField from "./FormField.svelte";

describe("FormField — label asociado y accesibilidad básica", () => {
  it("asocia el label al control vía for/id", () => {
    render(FormField, { props: { id: "asunto", label: "Asunto" } });
    const input = screen.getByLabelText("Asunto");
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe("INPUT");
  });

  it("required agrega el indicador visual y aria-required", () => {
    render(FormField, { props: { id: "asunto", label: "Asunto", required: true } });
    const input = screen.getByLabelText("Asunto", { exact: false });
    expect(input).toHaveAttribute("aria-required", "true");
  });

  it("sin error: aria-invalid=false y no se muestra mensaje de error", () => {
    render(FormField, { props: { id: "asunto", label: "Asunto" } });
    const input = screen.getByLabelText("Asunto");
    expect(input).toHaveAttribute("aria-invalid", "false");
    // El contenedor de error existe (live region estable) pero vacío
    expect(document.getElementById("asunto-error")).toHaveTextContent("");
  });

  it("con error: marca aria-invalid=true y muestra el mensaje asociado por aria-describedby", () => {
    render(FormField, {
      props: { id: "asunto", label: "Asunto", error: "El asunto es obligatorio" },
    });
    const input = screen.getByLabelText("Asunto");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("El asunto es obligatorio")).toBeInTheDocument();
    expect(input.getAttribute("aria-describedby")).toContain("asunto-error");
  });

  it("hint: se muestra y se referencia en aria-describedby cuando no hay error", () => {
    render(FormField, {
      props: { id: "asunto", label: "Asunto", hint: "Máximo 200 caracteres" },
    });
    const input = screen.getByLabelText("Asunto");
    expect(screen.getByText("Máximo 200 caracteres")).toBeInTheDocument();
    expect(input.getAttribute("aria-describedby")).toContain("asunto-hint");
  });

  it("hint se oculta cuando hay error (el error tiene prioridad visual)", () => {
    render(FormField, {
      props: {
        id: "asunto",
        label: "Asunto",
        hint: "Máximo 200 caracteres",
        error: "Campo obligatorio",
      },
    });
    expect(screen.queryByText("Máximo 200 caracteres")).not.toBeInTheDocument();
    expect(screen.getByText("Campo obligatorio")).toBeInTheDocument();
  });

  it("type=select renderiza un <select> con las opciones dadas", () => {
    render(FormField, {
      props: {
        id: "tipo",
        label: "Tipo documental",
        type: "select",
        options: [
          { value: "oficio", label: "Oficio" },
          { value: "memo", label: "Memorando" },
        ],
      },
    });
    const select = screen.getByLabelText("Tipo documental");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Oficio" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Memorando" })).toBeInTheDocument();
  });

  it("type=textarea renderiza un <textarea>", () => {
    render(FormField, { props: { id: "obs", label: "Observaciones", type: "textarea" } });
    const textarea = screen.getByLabelText("Observaciones");
    expect(textarea.tagName).toBe("TEXTAREA");
  });

  it("disabled deshabilita el control", () => {
    render(FormField, { props: { id: "asunto", label: "Asunto", disabled: true } });
    expect(screen.getByLabelText("Asunto")).toBeDisabled();
  });

  it("el atributo name usa id por defecto (necesario para FormData nativo)", () => {
    render(FormField, { props: { id: "asunto", label: "Asunto" } });
    expect(screen.getByLabelText("Asunto")).toHaveAttribute("name", "asunto");
  });

  it("el atributo name se puede sobrescribir independientemente de id", () => {
    render(FormField, { props: { id: "field-name", name: "name", label: "Nombre" } });
    expect(screen.getByLabelText("Nombre")).toHaveAttribute("name", "name");
  });

  // El `name` se añadió a los 6 controles (text/email/password/number/date/
  // select/textarea) pero los dos tests anteriores solo cubren `type="text"`.
  // select y textarea son justo los que usan los formularios reales con
  // form actions nativos (p. ej. el modal de creación de expedientes).
  it("type=select: el atributo name usa id por defecto", () => {
    render(FormField, {
      props: {
        id: "tipo",
        label: "Tipo documental",
        type: "select",
        options: [{ value: "oficio", label: "Oficio" }],
      },
    });
    expect(screen.getByLabelText("Tipo documental")).toHaveAttribute("name", "tipo");
  });

  it("type=select: el atributo name se puede sobrescribir independientemente de id", () => {
    render(FormField, {
      props: {
        id: "trd-serie-select",
        name: "trd_serie_id",
        label: "Serie TRD",
        type: "select",
        options: [{ value: "s1", label: "Serie 1" }],
      },
    });
    expect(screen.getByLabelText("Serie TRD")).toHaveAttribute("name", "trd_serie_id");
  });

  it("type=textarea: el atributo name usa id por defecto", () => {
    render(FormField, { props: { id: "obs", label: "Observaciones", type: "textarea" } });
    expect(screen.getByLabelText("Observaciones")).toHaveAttribute("name", "obs");
  });

  it("type=textarea: el atributo name se puede sobrescribir independientemente de id", () => {
    render(FormField, {
      props: {
        id: "field-description",
        name: "description",
        label: "Descripción",
        type: "textarea",
      },
    });
    expect(screen.getByLabelText("Descripción")).toHaveAttribute("name", "description");
  });
});
