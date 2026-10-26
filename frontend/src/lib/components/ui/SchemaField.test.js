// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * SchemaField.test.js — campo dinámico derivado de una propiedad de JSON
 * Schema (T-12 Form Builder de creación de expediente).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import SchemaField from "./SchemaField.svelte";

describe("SchemaField — render por tipo de schema", () => {
  it("string -> <input type=text> con name=meta_<propKey>", () => {
    render(SchemaField, {
      props: { propKey: "motivo", schema: { type: "string", title: "Motivo" } },
    });
    const input = screen.getByLabelText("Motivo");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("type", "text");
    expect(input).toHaveAttribute("name", "meta_motivo");
    expect(input).toHaveAttribute("id", "meta-motivo");
  });

  it("sin `title`: usa la clave del schema como label", () => {
    render(SchemaField, { props: { propKey: "asunto_interno", schema: { type: "string" } } });
    expect(screen.getByLabelText("asunto_interno")).toBeInTheDocument();
  });

  it("number -> <input type=number>", () => {
    render(SchemaField, { props: { propKey: "monto", schema: { type: "number" } } });
    expect(screen.getByLabelText("monto")).toHaveAttribute("type", "number");
  });

  it("integer -> <input type=number>", () => {
    render(SchemaField, { props: { propKey: "cantidad", schema: { type: "integer" } } });
    expect(screen.getByLabelText("cantidad")).toHaveAttribute("type", "number");
  });

  it("string + format:date -> <input type=date>", () => {
    render(SchemaField, {
      props: { propKey: "fecha_limite", schema: { type: "string", format: "date" } },
    });
    expect(screen.getByLabelText("fecha_limite")).toHaveAttribute("type", "date");
  });

  it("boolean -> <select> con opciones Sí/No", () => {
    render(SchemaField, { props: { propKey: "urgente", schema: { type: "boolean" } } });
    const select = screen.getByLabelText("urgente");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Sí" })).toHaveValue("true");
    expect(screen.getByRole("option", { name: "No" })).toHaveValue("false");
  });

  it("string + enum -> <select> con las opciones del enum", () => {
    render(SchemaField, {
      props: {
        propKey: "prioridad",
        schema: { type: "string", enum: ["alta", "media", "baja"] },
      },
    });
    const select = screen.getByLabelText("prioridad");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "alta" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "media" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "baja" })).toBeInTheDocument();
  });

  it("required=true: aria-required en el control y SIN placeholder de select opcional", () => {
    render(SchemaField, {
      props: { propKey: "motivo", schema: { type: "string", enum: ["a", "b"] }, required: true },
    });
    const select = screen.getByLabelText("motivo", { exact: false });
    expect(select).toHaveAttribute("aria-required", "true");
    expect(screen.queryByRole("option", { name: "— seleccionar —" })).not.toBeInTheDocument();
  });

  it("required=false en un select: incluye el placeholder '— seleccionar —'", () => {
    render(SchemaField, {
      props: {
        propKey: "prioridad",
        schema: { type: "string", enum: ["a", "b"] },
        required: false,
      },
    });
    expect(screen.getByRole("option", { name: "— seleccionar —" })).toBeInTheDocument();
  });

  it("description del schema se usa como hint", () => {
    render(SchemaField, {
      props: {
        propKey: "motivo",
        schema: { type: "string", description: "Explica brevemente el motivo" },
      },
    });
    expect(screen.getByText("Explica brevemente el motivo")).toBeInTheDocument();
  });

  it("error se propaga a aria-invalid/aria-describedby vía FormField", () => {
    render(SchemaField, {
      props: { propKey: "motivo", schema: { type: "string" }, error: "Campo inválido" },
    });
    const input = screen.getByLabelText("motivo");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Campo inválido")).toBeInTheDocument();
  });
});

describe("SchemaField — constructos no soportados: fail-safe, no renderiza control", () => {
  it("type=object sin propiedades: no renderiza ningún input/select/textarea", () => {
    const { container } = render(SchemaField, {
      props: { propKey: "detalle", schema: { type: "object", properties: {} } },
    });
    expect(container.querySelector("input, select, textarea")).toBeNull();
  });

  it("array de objetos: no renderiza ningún control", () => {
    const { container } = render(SchemaField, {
      props: {
        propKey: "items",
        schema: { type: "array", items: { type: "object", properties: { a: { type: "string" } } } },
      },
    });
    expect(container.querySelector("input, select, textarea")).toBeNull();
  });

  it("$ref: no renderiza ningún control", () => {
    const { container } = render(SchemaField, {
      props: { propKey: "ref_field", schema: { $ref: "#/definitions/Foo" } },
    });
    expect(container.querySelector("input, select, textarea")).toBeNull();
  });
});

describe("SchemaField — array de escalares (AREA-12-05)", () => {
  const schema = { type: "array", title: "Etiquetas", items: { type: "string" } };

  it("renderiza una lista repetible: agregar y quitar elementos", async () => {
    const { container } = render(SchemaField, { props: { propKey: "tags", schema } });
    expect(screen.getByLabelText("Etiquetas 1")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: /agregar etiquetas/i }));
    expect(screen.getByLabelText("Etiquetas 2")).toBeInTheDocument();

    await fireEvent.input(screen.getByLabelText("Etiquetas 1"), { target: { value: "a" } });
    await fireEvent.input(screen.getByLabelText("Etiquetas 2"), { target: { value: "b" } });
    expect(container.querySelector('input[type="hidden"][name="meta_tags"]').value).toBe(
      '["a","b"]',
    );

    await fireEvent.click(screen.getByRole("button", { name: "Quitar Etiquetas 1" }));
    expect(container.querySelector('input[type="hidden"][name="meta_tags"]').value).toBe('["b"]');
  });

  it("los controles interiores no llevan name: solo viaja el JSON", () => {
    const { container } = render(SchemaField, { props: { propKey: "tags", schema } });
    const nombres = [...container.querySelectorAll("input")].map((i) => i.getAttribute("name"));
    expect(nombres.filter((n) => n === "meta_tags")).toHaveLength(1);
  });

  it("repuebla desde `value` y se vacía cuando el padre lo limpia (reabrir el formulario)", async () => {
    const { component } = render(SchemaField, {
      props: { propKey: "tags", schema, value: '["x","y"]' },
    });
    expect(screen.getByLabelText("Etiquetas 2")).toHaveValue("y");
    component.$set({ value: "" });
    await Promise.resolve();
    expect(screen.queryByLabelText("Etiquetas 2")).toBeNull();
    expect(screen.getByLabelText("Etiquetas 1")).toHaveValue("");
  });

  it("muestra el error del campo", () => {
    render(SchemaField, { props: { propKey: "tags", schema, error: "Falta una etiqueta" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Falta una etiqueta");
  });
});

describe("SchemaField — object de un nivel (AREA-12-05)", () => {
  const schema = {
    type: "object",
    title: "Ubicación",
    required: ["ciudad"],
    properties: {
      ciudad: { type: "string", title: "Ciudad" },
      piso: { type: "integer", title: "Piso" },
    },
  };

  it("delega en SchemaField por propiedad y arma el JSON", async () => {
    const { container } = render(SchemaField, { props: { propKey: "ubic", schema } });
    expect(screen.getByRole("group", { name: /ubicación/i })).toBeInTheDocument();

    await fireEvent.input(screen.getByLabelText(/Ciudad/), { target: { value: "Bogota" } });
    await fireEvent.input(screen.getByLabelText(/Piso/), { target: { value: "3" } });

    const oculto = container.querySelector('input[type="hidden"][name="meta_ubic"]');
    expect(JSON.parse(oculto.value)).toEqual({ ciudad: "Bogota", piso: 3 });
  });

  it("marca obligatoria la propiedad required y pinta su error por propiedad", () => {
    render(SchemaField, {
      props: { propKey: "ubic", schema, subErrors: { ciudad: "La ciudad es obligatoria" } },
    });
    expect(screen.getByLabelText(/Ciudad/)).toBeRequired();
    expect(screen.getByLabelText(/Piso/)).not.toBeRequired();
    expect(screen.getByText("La ciudad es obligatoria")).toBeInTheDocument();
  });

  it("un solo input con name meta_<clave>: los interiores no se envían sueltos", () => {
    const { container } = render(SchemaField, { props: { propKey: "ubic", schema } });
    const nombres = [...container.querySelectorAll("input, select")]
      .map((i) => i.getAttribute("name"))
      .filter(Boolean);
    expect(nombres).toEqual(["meta_ubic"]);
  });
});
