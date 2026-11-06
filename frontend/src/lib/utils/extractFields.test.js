// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import {
  opcionesCerradas,
  camposBase,
  camposPlantilla,
  campoDesdeSchema,
  aplicarExtracto,
} from "./extractFields.js";

const DIRECCION = [
  { value: "E", label: "Entrada" },
  { value: "S", label: "Salida" },
  { value: "I", label: "Interno" },
];

describe("opcionesCerradas", () => {
  it("quita el placeholder vacío del select", () => {
    expect(
      opcionesCerradas([
        { value: "", label: "— seleccionar —" },
        { value: "1", label: "Jurídica" },
      ]),
    ).toEqual([{ value: "1", label: "Jurídica" }]);
  });
});

describe("camposBase", () => {
  it("manda choice con opciones reales y texto/número sin lista", () => {
    const fields = camposBase({
      direccion: DIRECCION,
      tipos: [
        { value: "", label: "— sin clasificar —" },
        { value: "RES", label: "Resolución" },
      ],
      niveles: [{ value: "1", label: "1 — Pública" }],
      dependencias: [
        { value: "", label: "— seleccionar —" },
        { value: "100", label: "Jurídica" },
      ],
    });
    const byKey = Object.fromEntries(fields.map((f) => [f.key, f]));
    expect(byKey.direction).toMatchObject({ kind: "choice" });
    expect(byKey.direction.choices).toEqual(DIRECCION);
    expect(byKey.doc_class.choices).toEqual([{ value: "RES", label: "Resolución" }]);
    expect(byKey.origin_dept_code.choices).toEqual([{ value: "100", label: "Jurídica" }]);
    expect(byKey.subject).toEqual({ key: "subject", kind: "text", label: "Asunto" });
    expect(byKey.pages).toEqual({ key: "pages", kind: "number", label: "Folios" });
  });

  it("omite un choice si el catálogo no tiene opciones", () => {
    const fields = camposBase({
      direccion: DIRECCION,
      tipos: [{ value: "", label: "— sin clasificar —" }],
      niveles: [],
      dependencias: [],
    });
    const keys = fields.map((f) => f.key);
    expect(keys).toContain("direction");
    expect(keys).not.toContain("doc_class");
    expect(keys).not.toContain("nivel_seguridad");
  });
});

describe("campoDesdeSchema / camposPlantilla", () => {
  it("enum y boolean viajan como choice", () => {
    expect(campoDesdeSchema("prioridad", { enum: ["alta", "media"], title: "Prioridad" })).toEqual({
      key: "prioridad",
      kind: "choice",
      label: "Prioridad",
      choices: [
        { value: "alta", label: "alta" },
        { value: "media", label: "media" },
      ],
    });
    expect(campoDesdeSchema("urgente", { type: "boolean", title: "Urgente" })).toMatchObject({
      key: "urgente",
      kind: "choice",
      choices: [
        { value: "true", label: "Sí" },
        { value: "false", label: "No" },
      ],
    });
  });

  it("texto, número y fecha no llevan opciones; object/array se omiten", () => {
    expect(campoDesdeSchema("motivo", { type: "string", title: "Motivo" })).toEqual({
      key: "motivo",
      kind: "text",
      label: "Motivo",
    });
    expect(campoDesdeSchema("cantidad", { type: "integer", title: "Cantidad" })).toEqual({
      key: "cantidad",
      kind: "number",
      label: "Cantidad",
    });
    expect(campoDesdeSchema("vence", { type: "string", format: "date", title: "Vence" })).toEqual({
      key: "vence",
      kind: "date",
      label: "Vence",
    });
    expect(campoDesdeSchema("detalle", { type: "object", properties: {} })).toBeNull();
    expect(
      camposPlantilla({ motivo: { type: "string" }, detalle: { type: "object" } }),
    ).toHaveLength(1);
  });
});

describe("aplicarExtracto", () => {
  const fields = camposBase({
    direccion: DIRECCION,
    tipos: [{ value: "RES", label: "Resolución" }],
    niveles: [{ value: "1", label: "Pública" }],
    dependencias: [{ value: "100", label: "Jurídica" }],
  });

  it("acepta un choice legal y un texto presente", () => {
    const { applied, marked } = aplicarExtracto(
      { direction: "E", subject: "Petición", pages: 3 },
      fields,
    );
    expect(applied).toEqual({ direction: "E", subject: "Petición", pages: "3" });
    expect(marked).toEqual(["direction", "subject", "pages"]);
  });

  it("un valor fuera de la lista queda vacío", () => {
    const { applied, marked } = aplicarExtracto({ direction: "X", subject: "Ok" }, fields);
    expect(applied.direction).toBeUndefined();
    expect(applied.subject).toBe("Ok");
    expect(marked).toEqual(["subject"]);
  });

  it("un campo de texto ausente no se marca", () => {
    const { applied, marked } = aplicarExtracto({ direction: "E" }, fields);
    expect(applied.subject).toBeUndefined();
    expect(marked).toEqual(["direction"]);
  });
});
