// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Software libre bajo AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

/**
 * Un borrador legado (solo `dest_dept`, sin código) no debe perder su destino
 * al editarse: el PATCH solo lleva `dest_dept_code` si el operador tocó el
 * selector, y la pantalla avisa cuál era el destino anterior.
 */
import { describe, it, expect } from "vitest";
const { readFileSync } = process.getBuiltinModule("fs");

const PAGINA = readFileSync("src/routes/(app)/borradores/+page.svelte", "utf-8");
const SERVER = readFileSync("src/routes/(app)/borradores/+page.server.js", "utf-8");

describe("borradores: destino legado", () => {
  it("el PATCH omite dest_dept_code si el selector no cambió", () => {
    expect(PAGINA).toMatch(/\.\.\.\(fDestDept !== fDestDeptInicial/);
  });
  it("avisa del destino anterior", () => {
    expect(PAGINA).toMatch(
      /Destino anterior: «\$\{destinoAnterior\}» — elija la dependencia del catálogo/,
    );
  });
  it("el load devuelve el error del catálogo y la pantalla lo muestra", () => {
    expect(SERVER).toMatch(/dependenciasError/);
    expect(PAGINA).toMatch(/data\.dependenciasError/);
  });
});
