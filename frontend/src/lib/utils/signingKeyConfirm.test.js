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
  REENROLL_CONFIRM_PHRASE,
  ADMIN_REVOKE_CONFIRM_PHRASE,
  isReenrollConfirmed,
  isAdminRevokeConfirmed,
} from "./signingKeyConfirm.js";

describe("isReenrollConfirmed", () => {
  it("acepta la frase exacta", () => {
    expect(isReenrollConfirmed(REENROLL_CONFIRM_PHRASE)).toBe(true);
  });

  it("acepta con espacios alrededor (trim)", () => {
    expect(isReenrollConfirmed(`  ${REENROLL_CONFIRM_PHRASE}  `)).toBe(true);
  });

  it.each([[undefined], [null], [""], ["reenrolar"], ["REENROLAR "], ["otra cosa"]])(
    "rechaza %j salvo el trim exacto",
    (v) => {
      if (v === `${REENROLL_CONFIRM_PHRASE} `) return; // cubierto arriba
      expect(isReenrollConfirmed(v)).toBe(false);
    },
  );
});

describe("isAdminRevokeConfirmed", () => {
  it("acepta la frase exacta", () => {
    expect(isAdminRevokeConfirmed(ADMIN_REVOKE_CONFIRM_PHRASE)).toBe(true);
  });

  it.each([[undefined], [null], [""], ["revocar"], ["REVOCAR!"]])(
    "rechaza %j",
    (v) => {
      expect(isAdminRevokeConfirmed(v)).toBe(false);
    },
  );

  it("las dos frases son distintas (no se pueden confundir)", () => {
    expect(REENROLL_CONFIRM_PHRASE).not.toBe(ADMIN_REVOKE_CONFIRM_PHRASE);
  });
});
