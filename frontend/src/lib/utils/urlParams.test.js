// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El valor por defecto tiene que aplicarse cuando el parámetro falta.
 *
 * `Number(url.searchParams.get("stopk"))` parece razonable y no lo es: `get`
 * devuelve `null` si el parámetro no está, y **`Number(null)` es 0**. El
 * guardia `Number.isFinite(...) ? ... : 5` toma entonces la rama buena —0 es
 * finito— y el clamp al mínimo lo sube a 1. El default nunca se alcanzaba.
 *
 * Se veía así: entrar en la pestaña Semántica pedía UN resultado en vez de
 * cinco, y el desplegable «Cantidad de resultados» aparecía en blanco porque 1
 * no es ninguna de sus opciones. Una búsqueda por significado que devuelve un
 * solo documento pierde justo lo que la hace útil — ofrecer varios parecidos
 * para que la persona elija.
 */

import { describe, it, expect } from "vitest";
import { enteroDeLaUrl } from "$lib/utils/urlParams.js";

const RANGO = { min: 1, max: 50, porDefecto: 5 };
const url = (qs) => new URL(`http://localhost/busqueda${qs}`);

describe("enteroDeLaUrl", () => {
  it("sin el parámetro aplica el valor por defecto, no el mínimo", () => {
    // El defecto exacto: devolvía 1.
    expect(enteroDeLaUrl(url(""), "stopk", RANGO)).toBe(5);
  });

  it("con el parámetro vacío también", () => {
    expect(enteroDeLaUrl(url("?stopk="), "stopk", RANGO)).toBe(5);
  });

  it("con un valor válido lo respeta", () => {
    expect(enteroDeLaUrl(url("?stopk=20"), "stopk", RANGO)).toBe(20);
  });

  it("acota por arriba y por abajo", () => {
    // Gemelo: aplicar el default cuando falta no puede convertirse en ignorar
    // los límites cuando sí viene — `stopk=9999` traería el archivo entero.
    expect(enteroDeLaUrl(url("?stopk=9999"), "stopk", RANGO)).toBe(50);
    expect(enteroDeLaUrl(url("?stopk=0"), "stopk", RANGO)).toBe(1);
    expect(enteroDeLaUrl(url("?stopk=-3"), "stopk", RANGO)).toBe(1);
  });

  it("un valor que no es número cae al defecto", () => {
    expect(enteroDeLaUrl(url("?stopk=muchos"), "stopk", RANGO)).toBe(5);
  });

  it("y un decimal se trunca en vez de colarse", () => {
    expect(enteroDeLaUrl(url("?stopk=7.9"), "stopk", RANGO)).toBe(7);
  });
});
