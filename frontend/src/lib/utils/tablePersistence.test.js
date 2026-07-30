// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect, beforeEach } from "vitest";
import { buildStorageKey, readJson, writeJson, removeKey } from "./tablePersistence.js";

describe("buildStorageKey — namespacing por tenant + usuario + tabla", () => {
  it("compone la clave con los cuatro segmentos", () => {
    expect(
      buildStorageKey({ tenant: "icetex", user: "u-1", tableId: "bandeja", kind: "columns" }),
    ).toBe("op-datatable:icetex:u-1:bandeja:columns");
  });

  it("cae a 'anon' cuando falta tenant o usuario (no colisiona con literales null/undefined)", () => {
    expect(
      buildStorageKey({ tenant: null, user: undefined, tableId: "bandeja", kind: "views" }),
    ).toBe("op-datatable:anon:anon:bandeja:views");
  });

  it("dos tenants distintos con la misma tabla obtienen claves distintas (no hay fuga entre tenants)", () => {
    const keyA = buildStorageKey({
      tenant: "icetex",
      user: "u-1",
      tableId: "bandeja",
      kind: "columns",
    });
    const keyB = buildStorageKey({
      tenant: "otro-tenant",
      user: "u-1",
      tableId: "bandeja",
      kind: "columns",
    });
    expect(keyA).not.toBe(keyB);
  });

  it("dos usuarios del mismo tenant obtienen claves distintas (preferencia por-usuario)", () => {
    const keyA = buildStorageKey({
      tenant: "icetex",
      user: "u-1",
      tableId: "bandeja",
      kind: "columns",
    });
    const keyB = buildStorageKey({
      tenant: "icetex",
      user: "u-2",
      tableId: "bandeja",
      kind: "columns",
    });
    expect(keyA).not.toBe(keyB);
  });
});

describe("readJson/writeJson/removeKey — round-trip en localStorage (jsdom)", () => {
  const key = "op-datatable:icetex:u-1:bandeja:columns";

  beforeEach(() => {
    window.localStorage.clear();
  });

  it("readJson devuelve null cuando la clave no existe", () => {
    expect(readJson(key)).toBeNull();
  });

  it("writeJson + readJson hacen round-trip de un array", () => {
    writeJson(key, ["estado", "fecha"]);
    expect(readJson(key)).toEqual(["estado", "fecha"]);
  });

  it("readJson no lanza y devuelve null ante JSON corrupto en la clave", () => {
    window.localStorage.setItem(key, "{esto no es json");
    expect(readJson(key)).toBeNull();
  });

  it("removeKey borra el valor persistido", () => {
    writeJson(key, ["estado"]);
    removeKey(key);
    expect(readJson(key)).toBeNull();
  });

  it("writeJson devuelve true al escribir con éxito", () => {
    expect(writeJson(key, { name: "vista-1" })).toBe(true);
  });
});
