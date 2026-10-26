// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * SchemaValue — metadatos guardados en SOLO LECTURA (AREA-12-05, B6-25).
 *
 * Desde AREA-12-05 la captura admite listas de escalares y objetos de un nivel;
 * la lectura seguía haciendo `String(v)` con la clave cruda: un objeto salía como
 * «[object Object]» y la etiqueta (`title`) de la plantilla nunca se veía.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import SchemaValue from "./SchemaValue.svelte";
import { formatearValorSchema } from "$lib/utils/schemaField.js";

const SCHEMA = {
  type: "object",
  properties: {
    reserva: { type: "string", title: "Motivo de reserva" },
    publico: { type: "boolean", title: "Es público" },
    vence: { type: "string", format: "date", title: "Vence" },
    palabras: { type: "array", title: "Palabras clave", items: { type: "string" } },
    ubicacion: {
      type: "object",
      title: "Ubicación",
      properties: { ciudad: { type: "string", title: "Ciudad" }, piso: { type: "number" } },
    },
    estado: {
      type: "string",
      enum: ["a", "b"],
      enumNames: ["Activo", "Bloqueado"],
      title: "Estado",
    },
  },
};

const VALOR = {
  reserva: "Ley 1712",
  publico: true,
  vence: "2026-09-19",
  palabras: ["uno", "dos"],
  ubicacion: { ciudad: "Pasto", piso: 3 },
  estado: "b",
};

describe("SchemaValue", () => {
  it("un object nunca sale como [object Object] y muestra los title interiores", () => {
    const { container } = render(SchemaValue, { props: { schema: SCHEMA, value: VALOR } });
    expect(container.textContent).not.toContain("[object Object]");
    expect(screen.getByText("Ubicación")).toBeTruthy();
    expect(screen.getByText("Ciudad")).toBeTruthy();
    expect(screen.getByText("Pasto")).toBeTruthy();
    // Propiedad interior sin title: cae a su clave.
    expect(screen.getByText("piso")).toBeTruthy();
  });

  it("usa el title, no la clave cruda", () => {
    render(SchemaValue, { props: { schema: SCHEMA, value: VALOR } });
    expect(screen.getByText("Motivo de reserva")).toBeTruthy();
    expect(screen.queryByText("reserva")).toBeNull();
  });

  it("boolean → Sí, enum → su etiqueta, fecha pura sin desplazar el día", () => {
    render(SchemaValue, { props: { schema: SCHEMA, value: VALOR } });
    expect(screen.getByText("Sí")).toBeTruthy();
    expect(screen.getByText("Bloqueado")).toBeTruthy();
    expect(screen.getByText("19/09/2026")).toBeTruthy();
  });

  it("array → un <li> por elemento", () => {
    const { container } = render(SchemaValue, { props: { schema: SCHEMA, value: VALOR } });
    const items = [...container.querySelectorAll("ul li")].map((l) => l.textContent.trim());
    expect(items).toEqual(["uno", "dos"]);
  });

  it("una clave que la plantilla no declara sale en «Otros datos registrados»", () => {
    render(SchemaValue, {
      props: { schema: SCHEMA, value: { reserva: "x", antigua: { a: 1 } } },
    });
    expect(screen.getByText("Otros datos registrados")).toBeTruthy();
    expect(screen.getByText("antigua")).toBeTruthy();
    expect(screen.getByText("a")).toBeTruthy();
  });

  it("las claves reservadas salen en «Datos del sistema» con etiqueta legible, no como «otros»", () => {
    const { container } = render(SchemaValue, {
      props: {
        schema: SCHEMA,
        value: { reserva: "x", from_borrador: "b-123", canal: "ventanilla", consentimiento: true },
      },
    });
    expect(screen.getByRole("heading", { name: "Datos del sistema" })).toBeTruthy();
    expect(screen.getByText("Borrador de origen")).toBeTruthy();
    expect(container.querySelector("code").textContent).toBe("b-123");
    expect(screen.getByText("Canal de ingreso")).toBeTruthy();
    expect(screen.getByText("Consentimiento")).toBeTruthy();
    expect(screen.queryByText("from_borrador")).toBeNull();
    expect(screen.queryByText("Otros datos registrados")).toBeNull();
  });

  it("sin plantilla o con fallo de carga: se dice y los datos NO se ocultan", () => {
    render(SchemaValue, {
      props: {
        schema: null,
        value: { reserva: "x" },
        loadError: "No se pudo cargar la plantilla.",
      },
    });
    expect(screen.getByText("No se pudo cargar la plantilla.")).toBeTruthy();
    expect(screen.getByText("reserva")).toBeTruthy();
    expect(screen.getByText("x")).toBeTruthy();
  });

  it("no interpreta HTML: el valor se escapa", () => {
    const { container } = render(SchemaValue, {
      props: { schema: SCHEMA, value: { reserva: "<img src=x onerror=alert(1)>" } },
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });

  it("formatearValorSchema: vacío y profundidad excesiva no inventan texto", () => {
    expect(formatearValorSchema(undefined, "")).toEqual({ kind: "text", text: "—" });
    const hondo = { a: { b: { c: { d: { e: 1 } } } } };
    const r = formatearValorSchema(undefined, hondo);
    expect(JSON.stringify(r)).not.toContain("[object Object]");
  });
});
