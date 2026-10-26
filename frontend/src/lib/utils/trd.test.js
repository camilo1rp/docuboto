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
 * El defecto no era que faltara el dato: era que estaba cargado y nadie lo leía.
 *
 * `+page.server.js` resuelve la serie completa en una faceta propia cuyo
 * comentario dice para qué sirve —«no solo un UUID crudo»— y ese resultado solo
 * llegaba al drawer de Disposición final. La ficha del expediente, lo primero
 * que se ve al abrirlo, seguía mostrando `83a44878-d169-4d4a-…`.
 *
 * Por eso hay dos pruebas de distinta naturaleza: una fija cómo se compone el
 * nombre, y la otra —la que importa— fija que la vista lo USE. Sin la segunda,
 * la primera podría estar en verde con la ficha pintando el UUID igual que antes.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { nombreSerieTrd, opcionesSerieTrd, vigenciaSerieTrd } from "./trd.js";

describe("nombreSerieTrd", () => {
  it("compone código y nombre", () => {
    expect(nombreSerieTrd({ code: "DEM-100", name: "Derechos de Petición" })).toBe(
      "DEM-100 — Derechos de Petición",
    );
  });

  it("se conforma con lo que haya", () => {
    expect(nombreSerieTrd({ code: "DEM-100" })).toBe("DEM-100");
    expect(nombreSerieTrd({ name: "Derechos de Petición" })).toBe("Derechos de Petición");
  });

  it("devuelve null cuando no hay nada que mostrar, para que quien llame decida", () => {
    expect(nombreSerieTrd(null)).toBeNull();
    expect(nombreSerieTrd({})).toBeNull();
    expect(nombreSerieTrd({ code: "   ", name: "" })).toBeNull();
  });
});

describe("la ficha del expediente usa el nombre, no el UUID", () => {
  const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const VISTA = path.join(SRC, "routes", "(app)", "expedientes", "[id]", "+page.svelte");
  const fuente = readFileSync(VISTA, "utf8");

  it("el bloque «Serie TRD» ofrece el nombre resuelto antes que el identificador", () => {
    const i = fuente.indexOf("<dt>Serie TRD</dt>");
    expect(i).toBeGreaterThan(-1);
    // El bloque llega hasta el SIGUIENTE campo de la lista, no hasta un número
    // fijo de caracteres. La ventana original de 400 dejó de alcanzar el
    // fallback al UUID en cuanto el bloque creció —al añadir la marca de
    // vigencia— y entonces el test comparaba contra un -1 en vez de contra la
    // posición real: un número mágico convierte «no está dentro de mi ventana»
    // en «no existe», que son cosas distintas, y el segundo es un falso verde
    // esperando a ocurrir.
    const fin = fuente.indexOf("<dt>", i + 10);
    const bloque = fuente.slice(i, fin > -1 ? fin : i + 2000);
    expect(bloque).toContain("serieTrdNombre");
    // El UUID sigue como último recurso, y por eso no se prohíbe aquí: lo que
    // se prohíbe es que sea la ÚNICA salida.
    expect(bloque.indexOf("serieTrdNombre")).toBeLessThan(bloque.indexOf("trd_serie_id"));
  });

  it("el nombre se deriva del detalle que el servidor ya resolvió", () => {
    expect(fuente).toMatch(/serieTrdNombre\s*=[\s\S]{0,200}trdSerieDetail/);
  });
});

/**
 * El desplegable que mostraba la misma serie dos veces.
 *
 * Con el circuito de convalidación abierto, DEM-100 existe a la vez como v1
 * convalidada y v2 en borrador. Etiquetadas solo con «código — nombre» son dos
 * filas idénticas y quien clasifica elige a ciegas entre la que rige y la que
 * no. Estaba resuelto en el asistente de radicación y NO en el modal de
 * metadatos del expediente: por eso el criterio es ahora uno solo.
 */
describe("opcionesSerieTrd", () => {
  const SERIES = [
    { id: "b", code: "DEM-100", name: "Derechos de Petición", version: 2, estado: "borrador" },
    { id: "a", code: "DEM-100", name: "Derechos de Petición", version: 1, estado: "convalidada" },
  ];

  it("distingue las versiones de una misma serie", () => {
    const etiquetas = opcionesSerieTrd(SERIES).map((o) => o.label);
    expect(new Set(etiquetas).size).toBe(etiquetas.length);
    expect(etiquetas).toContain("DEM-100 v1 — Derechos de Petición (vigente)");
    expect(etiquetas).toContain("DEM-100 v2 — Derechos de Petición (borrador)");
  });

  it("pone primero la que rige", () => {
    expect(opcionesSerieTrd(SERIES)[0].value).toBe("a");
  });

  it("no esconde las que no rigen: una entidad nueva solo tiene borradores", () => {
    const soloBorradores = [
      { id: "x", code: "A-1", name: "Serie", version: 1, estado: "borrador" },
    ];
    expect(opcionesSerieTrd(soloBorradores)).toHaveLength(1);
  });

  it("un estado desconocido va al final, no entre las vigentes", () => {
    const conRaro = [
      { id: "r", code: "A-1", name: "Serie", version: 9, estado: "inventado" },
      ...SERIES,
    ];
    expect(opcionesSerieTrd(conRaro).at(-1).value).toBe("r");
  });

  it("tolera una lista vacía o ausente", () => {
    expect(opcionesSerieTrd([])).toEqual([]);
    expect(opcionesSerieTrd(undefined)).toEqual([]);
  });
});

describe("vigenciaSerieTrd — bajo qué versión está el expediente, y si rige", () => {
  it("pone la versión junto al código, como el desplegable", () => {
    const v = vigenciaSerieTrd({
      code: "DEM-100",
      name: "Derechos de Petición",
      version: 1,
      estado: "derogada",
    });
    expect(v.etiqueta).toBe("DEM-100 v1 — Derechos de Petición");
  });

  it("dice que NO rige una versión derogada, con la palabra que ya usa el proyecto", () => {
    const v = vigenciaSerieTrd({ code: "DEM-100", version: 1, estado: "derogada" });
    expect(v.rige).toBe(false);
    expect(v.estadoTexto).toBe("derogada");
  });

  it("una convalidada rige, y una migrada también", () => {
    expect(vigenciaSerieTrd({ code: "X", estado: "convalidada" }).rige).toBe(true);
    expect(vigenciaSerieTrd({ code: "X", estado: "migrada" }).rige).toBe(true);
  });

  it("borrador y aprobada NO rigen: convalidar es lo que hace regir", () => {
    // Ac. AGN 001/2024 (compila el 004/2019): un instrumento gobierna la
    // retención cuando lo convalida la instancia competente, no cuando alguien
    // lo aprueba internamente.
    expect(vigenciaSerieTrd({ code: "X", estado: "borrador" }).rige).toBe(false);
    expect(vigenciaSerieTrd({ code: "X", estado: "aprobada" }).rige).toBe(false);
  });

  it("sin estado NO afirma que rige: callar es mejor que asegurar de más", () => {
    const v = vigenciaSerieTrd({ code: "DEM-100", name: "Derechos de Petición" });
    expect(v.rige).toBe(false);
    expect(v.estadoTexto).toBeNull();
    expect(v.etiqueta).toBe("DEM-100 — Derechos de Petición");
  });

  it("no revienta con una serie ausente", () => {
    expect(vigenciaSerieTrd(null).etiqueta).toBeNull();
    expect(vigenciaSerieTrd(undefined).rige).toBe(false);
  });
});

describe("la ficha del expediente avisa cuando el instrumento ya no rige", () => {
  // El defecto concreto: EXP-2026-0001 apuntaba a la v1 DEROGADA y la ficha
  // mostraba «DEM-100 — Derechos de Petición» a secas, sin versión ni estado,
  // mientras el desplegable de metadatos —mismo dato, otra pantalla— sí lo
  // decía. Tercera aparición del mismo descuido.
  const SRC_FICHA = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const FICHA = readFileSync(
    path.join(SRC_FICHA, "routes", "(app)", "expedientes", "[id]", "+page.svelte"),
    "utf8",
  );

  it("usa el helper compartido y no compone la etiqueta por su cuenta", () => {
    expect(FICHA).toContain("vigenciaSerieTrd");
  });

  it("el bloque «Serie TRD» muestra el estado cuando el instrumento no rige", () => {
    const i = FICHA.indexOf("<dt>Serie TRD</dt>");
    expect(i).toBeGreaterThan(-1);
    const bloque = FICHA.slice(i, i + 1600);
    expect(bloque).toContain("serieTrdVigencia.estadoTexto");
    expect(bloque).toContain("!serieTrdVigencia.rige");
  });

  it("el aviso NO bloquea el trabajo: no aparece `disabled` en ese bloque", () => {
    // La decisión es del archivista. Avisar y prohibir son cosas distintas, y
    // esconder la serie dejaría la ficha sin decir bajo qué instrumento está.
    const i = FICHA.indexOf("<dt>Serie TRD</dt>");
    const bloque = FICHA.slice(i, i + 1600);
    expect(bloque).not.toContain("disabled");
  });
});
