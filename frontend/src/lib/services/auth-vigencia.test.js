// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth-vigencia.test.js — `tokenVigente`: presencia de cookie ≠ sesión viva.
 *
 * La sesión del frontend descansaba en que el navegador tirase la cookie en el
 * mismo instante en que Keycloak da el token por muerto. No coinciden: el
 * `maxAge` corre desde que el navegador RECIBE la cookie y el `exp` desde que
 * Keycloak ACUÑA el token, así que la cookie sobrevive siempre. Con la cookie
 * viva y el token muerto, la guarda daba la sesión por buena y todas las
 * pantallas se dibujaban con un 401 dentro.
 */
import { describe, it, expect } from "vitest";
import { tokenVigente, MARGEN_EXPIRACION_S } from "./auth.server.js";

/** JWT sin firmar: `tokenVigente` solo lee claims, la firma la valida el backend. */
function jwt(payload) {
  const b64 = (o) =>
    Buffer.from(JSON.stringify(o)).toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.firma`;
}

const enSegundos = (delta) => Math.floor(Date.now() / 1000) + delta;

describe("tokenVigente", () => {
  it("un token con vida por delante está vigente", () => {
    expect(tokenVigente(jwt({ exp: enSegundos(300) }))).toBe(true);
  });

  it("un token ya caducado NO está vigente", () => {
    expect(tokenVigente(jwt({ exp: enSegundos(-1) }))).toBe(false);
  });

  it("se da por agotado ANTES de expirar, por el margen", () => {
    // Sin margen, un token con dos segundos de vida se declara bueno y muere en
    // el viaje al gateway: la pantalla ya está montada cuando llega el 401.
    expect(tokenVigente(jwt({ exp: enSegundos(MARGEN_EXPIRACION_S - 2) }))).toBe(false);
    expect(tokenVigente(jwt({ exp: enSegundos(MARGEN_EXPIRACION_S + 60) }))).toBe(true);
  });

  it("sin token, sin `exp`, o ilegible: agotado", () => {
    // Fail-closed. Afirmar que sirve un token que no se puede leer es
    // exactamente el defecto que este módulo corrige.
    expect(tokenVigente(undefined)).toBe(false);
    expect(tokenVigente("")).toBe(false);
    expect(tokenVigente(jwt({ sub: "u-1" }))).toBe(false);
    expect(tokenVigente("esto-no-es-un-jwt")).toBe(false);
  });
});
