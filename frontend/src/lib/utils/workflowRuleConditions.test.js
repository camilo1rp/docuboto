// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import {
  coerceConditionValue,
  buildConditionsPayload,
  operadoresPermitidos,
  operadorPermitido,
  opcionesOperador,
  tipoValor,
} from "./workflowRuleConditions.js";

describe("coerceConditionValue", () => {
  it("op=in: separa por comas y recorta espacios", () => {
    expect(coerceConditionValue("in", " oficio , circular ,  ")).toEqual(["oficio", "circular"]);
  });

  it("op=gt/lt/gte/lte: convierte a número cuando es válido", () => {
    expect(coerceConditionValue("gt", "10")).toBe(10);
    expect(coerceConditionValue("lte", "3.5")).toBe(3.5);
  });

  it("op numérico con valor no numérico: conserva el string (fail-safe, no inventa 0)", () => {
    expect(coerceConditionValue("gt", "no-es-numero")).toBe("no-es-numero");
  });

  it("op=eq/contains/regex: string recortado tal cual", () => {
    expect(coerceConditionValue("eq", "  E  ")).toBe("E");
    expect(coerceConditionValue("regex", "^OFICIO-\\d+$")).toBe("^OFICIO-\\d+$");
  });
});

describe("buildConditionsPayload", () => {
  it("descarta filas con field/op fuera del catálogo cerrado del backend", () => {
    const out = buildConditionsPayload([
      { field: "doc_type", op: "eq", value: "E" },
      { field: "campo_inventado", op: "eq", value: "x" },
      { field: "subject", op: "operador_inventado", value: "x" },
    ]);
    expect(out).toEqual([{ field: "doc_type", op: "eq", value: "E" }]);
  });

  it("descarta filas incompletas (value vacío)", () => {
    const out = buildConditionsPayload([{ field: "subject", op: "contains", value: "   " }]);
    expect(out).toEqual([]);
  });

  it("no es un array: devuelve []", () => {
    expect(buildConditionsPayload(null)).toEqual([]);
    expect(buildConditionsPayload(undefined)).toEqual([]);
  });

  it("coerciona value según el operador de cada fila", () => {
    const out = buildConditionsPayload([
      { field: "pages", op: "gte", value: "5" },
      { field: "dest_dept", op: "in", value: "TIC,Jurídica" },
      { field: "dest_dept_code", op: "in", value: "12, 7" },
      { field: "origin_dept_code", op: "eq", value: "5" },
    ]);
    expect(out).toEqual([
      { field: "pages", op: "gte", value: 5 },
      { field: "dest_dept", op: "in", value: ["TIC", "Jurídica"] },
      { field: "dest_dept_code", op: "in", value: [12, 7] },
      { field: "origin_dept_code", op: "eq", value: 5 },
    ]);
  });
});

describe("operadores por campo (ADR-032)", () => {
  it("los campos *_dept_code solo admiten eq/ne/in", () => {
    expect(operadoresPermitidos("dest_dept_code")).toEqual(["eq", "ne", "in"]);
    expect(operadorPermitido("origin_dept_code", "contains")).toBe(false);
  });
  it("los campos de dependencia por nombre solo admiten contains/regex", () => {
    expect(operadoresPermitidos("dest_dept")).toEqual(["contains", "regex"]);
    expect(operadorPermitido("origin_dept", "eq")).toBe(false);
  });
  it("opcionesOperador conserva un operador heredado marcado como obsoleto", () => {
    const o = opcionesOperador("dest_dept", "eq");
    expect(o[0].value).toBe("eq");
    expect(o[0].label).toMatch(/obsoleto/);
    expect(opcionesOperador("subject").length).toBe(10);
  });
  it("regex se lee «Coincide con el patrón»", () => {
    expect(opcionesOperador("subject").find((o) => o.value === "regex")?.label).toBe(
      "Coincide con el patrón",
    );
  });
  it("tipoValor distingue select / multi / text", () => {
    expect(tipoValor("dest_dept_code", "eq")).toBe("select");
    expect(tipoValor("dest_dept_code", "in")).toBe("multi");
    expect(tipoValor("subject", "in")).toBe("text");
  });
});
