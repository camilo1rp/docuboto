// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * asistenteAcceso — el asistente exige las DOS condiciones (bandera de la entidad y
 * PERM_CONOCIMIENTO); cada rama negativa tiene su control positivo.
 */
import { describe, it, expect } from "vitest";
import {
  asistenteActivo,
  puedeUsarConocimiento,
  puedeAceptarSugerencia,
  tabsBusquedaPermitidas,
  mensajeConocimiento,
} from "./asistenteAcceso.js";

const usuario = (habilitado, nivel) => ({
  asistente_habilitado: habilitado,
  permission_levels: nivel === undefined ? {} : { PERM_CONOCIMIENTO: nivel },
});

describe("asistenteActivo", () => {
  it("solo `true` estricto enciende (control positivo)", () => {
    expect(asistenteActivo({ asistente_habilitado: true })).toBe(true);
  });
  it.each([[false], [undefined], [null], ["true"], [1]])("%s apaga", (v) => {
    expect(asistenteActivo({ asistente_habilitado: v })).toBe(false);
  });
  it("sin usuario apaga", () => {
    expect(asistenteActivo(null)).toBe(false);
  });
});

describe("puedeUsarConocimiento", () => {
  it("bandera + nivel 1: puede consultar (control positivo)", () => {
    expect(puedeUsarConocimiento(usuario(true, 1))).toBe(true);
  });
  it("bandera sin permiso: no", () => {
    expect(puedeUsarConocimiento(usuario(true))).toBe(false);
    expect(puedeUsarConocimiento(usuario(true, 0))).toBe(false);
  });
  it("ROOT con bandera puede; ROOT sin bandera no", () => {
    const root = (h) => ({ asistente_habilitado: h, permission_levels: { USUA_PERM_ROOT: 5 } });
    expect(puedeUsarConocimiento(root(true))).toBe(true);
    expect(puedeUsarConocimiento(root(false))).toBe(false);
  });
  it("permiso sin bandera: no (aunque tenga nivel 5)", () => {
    expect(puedeUsarConocimiento(usuario(false, 5))).toBe(false);
  });
});

describe("puedeAceptarSugerencia", () => {
  it("nivel 2 con bandera: sí (control positivo)", () => {
    expect(puedeAceptarSugerencia(usuario(true, 2))).toBe(true);
  });
  it("nivel 1 consulta pero no acepta", () => {
    expect(puedeUsarConocimiento(usuario(true, 1))).toBe(true);
    expect(puedeAceptarSugerencia(usuario(true, 1))).toBe(false);
  });
  it("nivel 2 sin bandera: no", () => {
    expect(puedeAceptarSugerencia(usuario(false, 2))).toBe(false);
  });
});

describe("tabsBusquedaPermitidas", () => {
  it("ambos requisitos: las tres pestañas (control positivo)", () => {
    expect(tabsBusquedaPermitidas(usuario(true, 1))).toEqual([
      "exacta",
      "semantica",
      "antecedentes",
    ]);
  });
  it("sin bandera o sin permiso: solo exacta", () => {
    expect(tabsBusquedaPermitidas(usuario(false, 1))).toEqual(["exacta"]);
    expect(tabsBusquedaPermitidas(usuario(true))).toEqual(["exacta"]);
    expect(tabsBusquedaPermitidas(null)).toEqual(["exacta"]);
  });
});

describe("mensajeConocimiento", () => {
  const anidado = (error) => ({ detail: { error, detail: "texto del backend", status: 403 } });
  it("asistente_deshabilitado nombra a la entidad y dónde activarlo", () => {
    expect(mensajeConocimiento(anidado("asistente_deshabilitado"), "x")).toMatch(
      /entidad.*activad/,
    );
  });
  it("forbidden nombra PERM_CONOCIMIENTO", () => {
    expect(mensajeConocimiento(anidado("forbidden"), "x")).toMatch(/PERM_CONOCIMIENTO/);
  });
  it("asistente_estado_no_disponible es un aviso de reintento", () => {
    expect(mensajeConocimiento(anidado("asistente_estado_no_disponible"), "x")).toMatch(
      /No se pudo comprobar/,
    );
  });
  it("otro error cae al mensaje genérico", () => {
    expect(mensajeConocimiento({}, "por defecto")).toBe("por defecto");
  });
});
