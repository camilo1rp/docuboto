// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * tokens-existen.test.js — Todo `var(--op-*)` referencia un token que existe.
 *
 * **El modo de fallo es mudo, y ya se pagó tres veces.** Una variable CSS que no
 * está definida hace que el navegador **descarte la declaración entera**: no hay
 * error, no hay aviso, la propiedad simplemente no se aplica. `svelte-check`,
 * `eslint` y `prettier` no miran dentro de `var()`, y el `build` pasa igual. Solo
 * se ve mirando la pantalla — y solo si uno sabe cómo tenía que verse.
 *
 * Es hermano de `tokens-mono.test.js`, que nació del caso más traicionero de esta
 * familia: `font-family: var(--op-font-mono, monospace)` en nueve sitios, donde
 * el token **sí existía** pero valía `12px`, así que la declaración era inválida
 * y el respaldo tampoco entraba (un respaldo solo actúa si la variable no está
 * definida, no si su valor no sirve). Aquel guardián vigila el mal uso de un
 * token real; este vigila la referencia a uno que no existe.
 *
 * Lo que lo motivó: al construir `/admin/correo` escribí la escala de espaciado
 * de memoria —`--op-space-md`, `--op-space-lg`— cuando en este sistema es
 * numérica (`--op-space-1..8`). Once declaraciones muertas. Y el censo destapó
 * **veintiuna más que no eran mías**, la mayoría en `ProposalCard.svelte`: la
 * tarjeta con la que el asistente propone un acto, casi entera sin estilo.
 *
 * **Se exige que el token exista incluso cuando hay respaldo.** Un respaldo sobre
 * un nombre inventado funciona de casualidad y esconde el error: así estaba
 * `var(--op-radius-pill, 9999px)`, que se veía bien y no participaba del sistema
 * de diseño. Un respaldo legítimo se escribe sobre un token que existe.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

/** Ficheros donde puede haber CSS del proyecto (mismo recorrido que su hermano). */
function fuentes() {
  return execSync(String.raw`find src \( -name '*.svelte' -o -name '*.scss' -o -name '*.css' \)`, {
    encoding: "utf-8",
  })
    .trim()
    .split("\n");
}

/** Tokens DEFINIDOS en cualquier sitio: `--op-x: valor`. */
function definidos(archivos) {
  const encontrados = new Set();
  for (const f of archivos) {
    for (const m of readFileSync(f, "utf8").matchAll(/(--op-[A-Za-z0-9_-]+)\s*:/g)) {
      encontrados.add(m[1]);
    }
  }
  return encontrados;
}

/** Tokens USADOS vía `var(--op-x)`, con su sitio para poder señalarlo. */
function usados(archivos) {
  /** @type {Map<string, string[]>} */
  const mapa = new Map();
  for (const f of archivos) {
    const lineas = readFileSync(f, "utf8").split("\n");
    lineas.forEach((linea, i) => {
      for (const m of linea.matchAll(/var\(\s*(--op-[A-Za-z0-9_-]+)/g)) {
        const sitio = `${f}:${i + 1}`;
        mapa.set(m[1], [...(mapa.get(m[1]) ?? []), sitio]);
      }
    });
  }
  return mapa;
}

describe("tokens del sistema de diseño Orpyca", () => {
  const archivos = fuentes();

  it("el censo ve el proyecto entero (anti-vacuidad)", () => {
    // Sin esto, un glob roto daría verde habiendo mirado cero archivos — que es
    // exactamente cómo un censo se convierte en una afirmación falsa.
    expect(archivos.length).toBeGreaterThan(80);
    expect(definidos(archivos).size).toBeGreaterThan(50);
    expect(usados(archivos).size).toBeGreaterThan(40);
  });

  it("ningún `var(--op-*)` apunta a un token inexistente", () => {
    const declarados = definidos(archivos);
    const huerfanos = [...usados(archivos).entries()]
      .filter(([token]) => !declarados.has(token))
      .map(([token, sitios]) => `${token} — ${sitios.length} uso(s), p. ej. ${sitios[0]}`);

    expect(
      huerfanos,
      "Estas declaraciones las descarta el navegador entera y en silencio. " +
        "Los tokens reales están en src/styles/_tokens.scss — la escala de " +
        "espaciado es numérica (--op-space-1..8), no md/lg.",
    ).toEqual([]);
  });
});
