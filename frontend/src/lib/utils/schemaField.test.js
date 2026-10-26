// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * schemaField.test.js — mapeo JSON Schema -> control de UI y coerción de
 * valores (T-12 Form Builder de creación de expediente).
 */
import { describe, it, expect } from "vitest";
import { resolveSchemaControl, coerceSchemaValue } from "./schemaField.js";

describe("resolveSchemaControl — mapeo tipo-de-schema -> control", () => {
  it("string sin enum/format -> text", () => {
    expect(resolveSchemaControl({ type: "string" })).toEqual({ kind: "text" });
  });

  it("string + enum -> select con las opciones del enum", () => {
    expect(resolveSchemaControl({ type: "string", enum: ["alta", "media", "baja"] })).toEqual({
      kind: "select",
      options: [
        { value: "alta", label: "alta" },
        { value: "media", label: "media" },
        { value: "baja", label: "baja" },
      ],
    });
  });

  it("string + format:date -> date", () => {
    expect(resolveSchemaControl({ type: "string", format: "date" })).toEqual({ kind: "date" });
  });

  it("string + format:email -> email", () => {
    expect(resolveSchemaControl({ type: "string", format: "email" })).toEqual({ kind: "email" });
  });

  it("string + format:date-time -> text (sin control datetime-local dedicado)", () => {
    expect(resolveSchemaControl({ type: "string", format: "date-time" })).toEqual({ kind: "text" });
  });

  it("number -> number", () => {
    expect(resolveSchemaControl({ type: "number" })).toEqual({ kind: "number" });
  });

  it("integer -> number", () => {
    expect(resolveSchemaControl({ type: "integer" })).toEqual({ kind: "number" });
  });

  it("boolean -> select Sí/No", () => {
    expect(resolveSchemaControl({ type: "boolean" })).toEqual({
      kind: "select",
      options: [
        { value: "true", label: "Sí" },
        { value: "false", label: "No" },
      ],
    });
  });

  it("type como array ['string','null'] toma el primer tipo no-null", () => {
    expect(resolveSchemaControl({ type: ["string", "null"] })).toEqual({ kind: "text" });
  });

  it("type como array ['null','number'] también resuelve al tipo no-null", () => {
    expect(resolveSchemaControl({ type: ["null", "number"] })).toEqual({ kind: "number" });
  });

  it("object sin propiedades -> no soportado (null)", () => {
    expect(resolveSchemaControl({ type: "object", properties: {} })).toBeNull();
  });

  it("array de escalares -> lista repetible (AREA-12-05)", () => {
    const c = resolveSchemaControl({ type: "array", items: { type: "string" } });
    expect(c).toEqual({ kind: "array", item: { kind: "text" } });
  });

  it("array de enum -> el elemento es un select", () => {
    const c = resolveSchemaControl({ type: "array", items: { enum: ["a", "b"] } });
    expect(c?.kind).toBe("array");
    expect(c?.item.kind).toBe("select");
  });

  it("array de objetos / de arrays / sin items -> no soportado (null)", () => {
    expect(
      resolveSchemaControl({
        type: "array",
        items: { type: "object", properties: { a: { type: "string" } } },
      }),
    ).toBeNull();
    expect(
      resolveSchemaControl({ type: "array", items: { type: "array", items: { type: "string" } } }),
    ).toBeNull();
    expect(resolveSchemaControl({ type: "array" })).toBeNull();
  });

  it("object de un nivel -> grupo con sus propiedades soportadas", () => {
    const c = resolveSchemaControl({
      type: "object",
      required: ["ciudad"],
      properties: { ciudad: { type: "string" }, piso: { type: "integer" }, raro: { $ref: "#/x" } },
    });
    expect(c?.kind).toBe("object");
    expect(c?.properties.map(([k]) => k)).toEqual(["ciudad", "piso"]);
    expect(c?.required).toEqual(["ciudad"]);
  });

  it("object con una propiedad OBLIGATORIA no soportada -> null (siempre acabaría en 422)", () => {
    expect(
      resolveSchemaControl({
        type: "object",
        required: ["raro"],
        properties: { raro: { $ref: "#/x" }, ok: { type: "string" } },
      }),
    ).toBeNull();
  });

  it("object dentro de object -> no soportado: solo un nivel", () => {
    expect(
      resolveSchemaControl({
        type: "object",
        required: ["interno"],
        properties: { interno: { type: "object", properties: { a: { type: "string" } } } },
      }),
    ).toBeNull();
  });

  it("$ref -> no soportado (null)", () => {
    expect(resolveSchemaControl({ $ref: "#/definitions/Foo" })).toBeNull();
  });

  it("oneOf/anyOf/allOf -> no soportado (null)", () => {
    expect(resolveSchemaControl({ oneOf: [{ type: "string" }, { type: "number" }] })).toBeNull();
    expect(resolveSchemaControl({ anyOf: [{ type: "string" }] })).toBeNull();
    expect(resolveSchemaControl({ allOf: [{ type: "string" }] })).toBeNull();
  });

  it("enum SIN `type` explícito -> select (JSON Schema lo permite; no debe desaparecer)", () => {
    expect(resolveSchemaControl({ enum: ["alta", "media", "baja"] })).toEqual({
      kind: "select",
      options: [
        { value: "alta", label: "alta" },
        { value: "media", label: "media" },
        { value: "baja", label: "baja" },
      ],
    });
  });

  it("schema sin `type` NI enum (o tipo desconocido) -> no soportado (null)", () => {
    expect(resolveSchemaControl({ description: "sin tipo" })).toBeNull();
    expect(resolveSchemaControl({ type: "unknown-type" })).toBeNull();
  });

  it("schema null/no-objeto -> no soportado (null)", () => {
    expect(resolveSchemaControl(null)).toBeNull();
    expect(resolveSchemaControl(undefined)).toBeNull();
  });
});

describe("coerceSchemaValue — coerción del valor crudo de FormData al tipo declarado", () => {
  it("string vacío -> undefined (se omite, deja que el backend exija required)", () => {
    expect(coerceSchemaValue({ type: "string" }, "")).toBeUndefined();
  });

  it("string -> se devuelve tal cual", () => {
    expect(coerceSchemaValue({ type: "string" }, "hola mundo")).toBe("hola mundo");
  });

  it("number válido -> Number coercionado", () => {
    expect(coerceSchemaValue({ type: "number" }, "42.5")).toBe(42.5);
  });

  it("integer válido -> Number coercionado", () => {
    expect(coerceSchemaValue({ type: "integer" }, "42")).toBe(42);
  });

  it("number inválido (no numérico) -> undefined, NO NaN", () => {
    expect(coerceSchemaValue({ type: "number" }, "abc")).toBeUndefined();
  });

  it("boolean 'true' -> true", () => {
    expect(coerceSchemaValue({ type: "boolean" }, "true")).toBe(true);
  });

  it("boolean 'false' -> false", () => {
    expect(coerceSchemaValue({ type: "boolean" }, "false")).toBe(false);
  });

  it("boolean con valor no reconocido -> undefined", () => {
    expect(coerceSchemaValue({ type: "boolean" }, "tal-vez")).toBeUndefined();
  });

  it("date -> string ISO tal cual", () => {
    expect(coerceSchemaValue({ type: "string", format: "date" }, "2026-07-15")).toBe("2026-07-15");
  });

  it("constructo no soportado (object sin properties) -> siempre undefined, aunque llegue un raw", () => {
    expect(coerceSchemaValue({ type: "object" }, '{"a":1}')).toBeUndefined();
  });

  describe("compuestos (AREA-12-05)", () => {
    const LISTA_NUM = { type: "array", items: { type: "number" } };
    const GRUPO = {
      type: "object",
      required: ["ciudad"],
      properties: {
        ciudad: { type: "string" },
        piso: { type: "integer" },
        tags: { type: "array", items: { type: "string" } },
      },
    };

    it("array: coerciona cada elemento al tipo de `items`", () => {
      expect(coerceSchemaValue(LISTA_NUM, '["1","2.5"]')).toEqual([1, 2.5]);
    });

    it("array: descarta vacíos e inválidos; sin elementos -> undefined", () => {
      expect(coerceSchemaValue(LISTA_NUM, '["1","","abc"]')).toEqual([1]);
      expect(coerceSchemaValue(LISTA_NUM, '["",""]')).toBeUndefined();
      expect(coerceSchemaValue(LISTA_NUM, "[]")).toBeUndefined();
    });

    it("JSON ilegible o de otra forma -> undefined (no se envía basura)", () => {
      expect(coerceSchemaValue(LISTA_NUM, "[1,")).toBeUndefined();
      expect(coerceSchemaValue(LISTA_NUM, '{"a":1}')).toBeUndefined();
      expect(coerceSchemaValue(GRUPO, "[1]")).toBeUndefined();
    });

    it("object: tipa cada propiedad y deja pasar la lista anidada", () => {
      expect(
        coerceSchemaValue(GRUPO, JSON.stringify({ ciudad: "Bogota", piso: "3", tags: ["a", "b"] })),
      ).toEqual({ ciudad: "Bogota", piso: 3, tags: ["a", "b"] });
    });

    it("object: solo claves que el schema declara (allowlist) y omite las vacías", () => {
      expect(
        coerceSchemaValue(GRUPO, JSON.stringify({ ciudad: "Cali", piso: "", extra: "x" })),
      ).toEqual({ ciudad: "Cali" });
      expect(coerceSchemaValue(GRUPO, JSON.stringify({ extra: "x" }))).toBeUndefined();
    });
  });
});
