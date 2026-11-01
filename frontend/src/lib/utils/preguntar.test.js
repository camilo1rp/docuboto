// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, expect, it } from "vitest";
import { avisoTransmision, enlaceCita } from "./preguntar.js";

const PROVEEDOR = { id: "example-provider", name: "Example Provider" };
const RAD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ANEXO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const FILE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

describe("avisoTransmision", () => {
  it("nombra al proveedor, la ruta de la instalación y los modelos", () => {
    const texto = avisoTransmision(
      PROVEEDOR,
      {
        hosting: "installation",
        model_aliases: ["triage-small", "triage-large"],
        subprocessors: [],
      },
      "Pública",
    );
    expect(texto).toBe(
      "La pregunta y este radicado (Pública) se transmiten a " +
        "Example Provider (example-provider) por la ruta de la instalación. " +
        "Los modelos de esta ruta son triage-small y triage-large.",
    );
  });

  it("nombra la ruta alojada y cada subencargado", () => {
    const texto = avisoTransmision(
      PROVEEDOR,
      {
        hosting: "hosted",
        model_aliases: ["triage-large"],
        subprocessors: [{ id: "host-a", name: "Example Host" }],
      },
      "Reservada",
    );
    expect(texto).toMatch(/ruta alojada/);
    expect(texto).toMatch(/Example Host \(host-a\)/);
  });

  it("no inventa un aviso si falta el alojamiento o el proveedor", () => {
    const ruta = { hosting: "installation", model_aliases: ["m"], subprocessors: [] };
    expect(avisoTransmision(PROVEEDOR, { ...ruta, hosting: "cloud" }, "Pública")).toBeNull();
    expect(avisoTransmision({ id: "", name: "Example Provider" }, ruta, "Pública")).toBeNull();
    expect(avisoTransmision(null, ruta, "Pública")).toBeNull();
  });
});

describe("enlaceCita", () => {
  const anexos = [{ id: ANEXO, file_id: FILE, filename: "contrato.pdf" }];

  it("enlaza el anexo del radicado abierto y la página", () => {
    const cita = { attachment_ref: ANEXO, page: 3, record_id: RAD, source_ref: "2026-E-1" };
    expect(enlaceCita(cita, RAD, anexos)).toEqual({
      href: `/bandeja/api/${RAD}/anexos/${FILE}#page=3`,
      nombre: "contrato.pdf, página 3",
    });
  });

  it("sin anexo, enlaza el radicado citado", () => {
    expect(enlaceCita({ record_id: RAD, source_ref: "2026-E-7", page: null }, RAD, [])).toEqual({
      href: `/bandeja?radicado=${RAD}`,
      nombre: "2026-E-7",
    });
  });

  it("un identificador que no es UUID no se vuelve ruta", () => {
    const cita = { attachment_ref: "../otro", record_id: "no-es-uuid" };
    expect(enlaceCita(cita, RAD, anexos)).toBeNull();
  });
});
