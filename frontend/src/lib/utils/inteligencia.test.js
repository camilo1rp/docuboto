// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La declaración que se propone tiene que contener cada texto que el backend
 * exige. Si falta uno, el acto responde 422 `declaracion_incompleta`.
 */
import { describe, it, expect } from "vitest";
import {
  declaracionNivel,
  declaracionProveedor,
  exigidosNivel,
  exigidosProveedor,
  fraseInventario,
  rutaAdmitida,
} from "./inteligencia.js";

const VISTA = {
  provider: { id: "example-provider", name: "Example Provider" },
  subprocessors: [{ id: "model-host-a", name: "Example Model Host" }],
  routing: [
    {
      level: "public",
      hosting: "hosted",
      model_aliases: ["triage-small"],
      subprocessors: ["model-host-a"],
      prompt_and_output_retention: "none",
      trains_on_customer_data: false,
    },
    {
      level: "reserved",
      hosting: "installation",
      model_aliases: ["drafting-large"],
      subprocessors: [],
      prompt_and_output_retention: "limited",
      trains_on_customer_data: true,
    },
  ],
};

describe("declaración del proveedor", () => {
  it("incluye el nombre y el id del proveedor y de cada subencargado", () => {
    const texto = declaracionProveedor(VISTA);
    for (const pieza of exigidosProveedor(VISTA)) expect(texto).toContain(pieza);
  });
});

describe("declaración de un nivel", () => {
  it("incluye la ruta, los modelos y los subencargados de ese nivel", () => {
    const texto = declaracionNivel(VISTA, 1);
    for (const pieza of exigidosNivel(VISTA, 1)) expect(texto).toContain(pieza);
  });

  it("una ruta en la instalación se admite aunque declare retención", () => {
    expect(rutaAdmitida(VISTA.routing[1])).toBe(true);
  });

  it("una ruta alojada que entrena no se admite", () => {
    const alojada = {
      ...VISTA.routing[0],
      trains_on_customer_data: true,
    };
    expect(rutaAdmitida(alojada)).toBe(false);
    expect(rutaAdmitida(VISTA.routing[0])).toBe(true);
  });
});

describe("fraseInventario", () => {
  it("distingue cero, una cuenta cerrada y una cuenta a medias", () => {
    expect(fraseInventario({ records: 0, complete: true })).toMatch(/no conserva/);
    expect(fraseInventario({ records: 1, complete: true })).toBe(
      "El proveedor conserva 1 radicado.",
    );
    expect(fraseInventario({ records: 4, complete: false })).toMatch(/al menos 4/);
  });
});
