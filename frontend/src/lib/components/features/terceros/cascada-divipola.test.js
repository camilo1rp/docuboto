// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cascada geográfica DIVIPOLA — E01, RF-RAD-05.
 *
 * Lo que importa aquí es lo que la cascada IMPIDE: que quede un municipio
 * colgando de otro departamento. La base lo rechazaría con un CHECK, pero el
 * mensaje no diría qué pasó, y para entonces el radicado ya se estaba
 * enviando.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/svelte";

import { _reiniciarCacheDivipola } from "$lib/utils/divipola.js";

const DEPARTAMENTOS = [
  { codigo: "05", nombre: "Antioquia" },
  { codigo: "11", nombre: "Bogotá, D.C." },
];
const MUNICIPIOS = {
  "05": [
    { codigo: "05001", departamento_codigo: "05", nombre: "Medellín" },
    { codigo: "05266", departamento_codigo: "05", nombre: "Envigado" },
  ],
  11: [{ codigo: "11001", departamento_codigo: "11", nombre: "Bogotá" }],
};

const pedidas = [];

beforeEach(() => {
  pedidas.length = 0;
  // Sin esto, el caso del catálogo caído heredaría la carga buena del caso
  // anterior y nunca lo vería caído — pasaría en verde sin probar nada.
  _reiniciarCacheDivipola();
  vi.stubGlobal("fetch", async (url) => {
    pedidas.push(url);
    const dep = new URL(url, "http://x").searchParams.get("departamento");
    return {
      ok: true,
      json: async () => (dep ? (MUNICIPIOS[dep] ?? []) : DEPARTAMENTOS),
    };
  });
});

const { default: Cascada } = await import("./CascadaDivipola.svelte");

const opciones = (id) => [...document.getElementById(id).options].map((o) => o.textContent.trim());

describe("cascada DIVIPOLA", () => {
  it("el municipio no se puede elegir antes que el departamento, y lo dice", async () => {
    render(Cascada, { idPrefijo: "t" });
    await waitFor(() => expect(opciones("t-departamento")).toContain("Antioquia"));

    expect(document.getElementById("t-municipio").disabled).toBe(true);
    expect(document.body.textContent).toContain("Elige primero el departamento");
  });

  it("elegir departamento trae SUS municipios", async () => {
    render(Cascada, { idPrefijo: "t" });
    await waitFor(() => expect(opciones("t-departamento")).toContain("Antioquia"));

    const select = document.getElementById("t-departamento");
    await fireEvent.change(select, { target: { value: "05" } });

    await waitFor(() => expect(opciones("t-municipio")).toContain("Medellín"));
    expect(opciones("t-municipio")).not.toContain("Bogotá");
  });

  it("cambiar de departamento BORRA el municipio anterior", async () => {
    // El defecto que una cascada mal programada produce en silencio: se cambia
    // el departamento y el municipio del anterior sigue viajando.
    //
    // Se mide sobre lo que se EMITE, no sobre el `<select>`: vaciar la lista de
    // opciones ya deja el desplegable en blanco, así que mirar el DOM daría por
    // buena una cascada que sigue mandando «05001» con el departamento 11 — se
    // comprobó revirtiendo la limpieza, y el test seguía verde.
    const visto = [];
    const { component } = render(Cascada, { idPrefijo: "t" });
    component.$on("cambio", (e) => visto.push(e.detail));
    await waitFor(() => expect(opciones("t-departamento")).toContain("Antioquia"));

    await fireEvent.change(document.getElementById("t-departamento"), {
      target: { value: "05" },
    });
    await waitFor(() => expect(opciones("t-municipio")).toContain("Medellín"));
    await fireEvent.change(document.getElementById("t-municipio"), {
      target: { value: "05001" },
    });
    expect(visto.at(-1).municipio_codigo).toBe("05001");

    await fireEvent.change(document.getElementById("t-departamento"), {
      target: { value: "11" },
    });

    await waitFor(() => expect(visto.at(-1).departamento_codigo).toBe("11"));
    expect(visto.at(-1).municipio_codigo).toBeNull();
    expect(opciones("t-municipio")).not.toContain("Medellín");
  });

  it("emite el CÓDIGO junto al nombre: es el código el que manda", async () => {
    const visto = [];
    const { component } = render(Cascada, { idPrefijo: "t" });
    component.$on("cambio", (e) => visto.push(e.detail));
    await waitFor(() => expect(opciones("t-departamento")).toContain("Antioquia"));

    await fireEvent.change(document.getElementById("t-departamento"), {
      target: { value: "05" },
    });
    await waitFor(() => expect(opciones("t-municipio")).toContain("Medellín"));
    await fireEvent.change(document.getElementById("t-municipio"), {
      target: { value: "05001" },
    });

    const ultimo = visto.at(-1);
    expect(ultimo.municipio_codigo).toBe("05001");
    expect(ultimo.departamento_codigo).toBe("05");
    expect(ultimo.municipio).toBe("Medellín");
  });

  it("si el catálogo no responde, se puede seguir sin municipio", async () => {
    // Degradación honesta: el municipio es un dato de clasificación, no una
    // condición del envío —el físico lo que exige es la DIRECCIÓN—, así que
    // un catálogo caído no puede cerrar la ventanilla.
    vi.stubGlobal("fetch", async () => ({ ok: false, json: async () => ({}) }));
    render(Cascada, { idPrefijo: "t" });

    await waitFor(() =>
      expect(document.body.textContent).toContain("No se pudo cargar el catálogo geográfico"),
    );
    expect(document.body.textContent).toContain("Puedes seguir sin municipio");
  });
});
