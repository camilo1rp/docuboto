// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * UserPicker — el componente no puede llamar «revisor» a quien no lo es.
 *
 * Nació para la cadena de vistos buenos y se reutiliza en cuatro sitios que no
 * son una cadena: el responsable de un traslado, el de una asignación, y el
 * usuario origen y destino de una reasignación. En esos cuatro, el botón
 * anunciaba «Agregar a X a la cadena» y la columna se titulaba «Cadena de
 * revisión». Quien usa lector de pantalla oía un acto que no era el suyo.
 *
 * Lo que estos casos fijan es el criterio, no la redacción: el DEFAULT tiene que
 * ser el que nunca miente. Un uso futuro que olvide declarar su vocabulario dirá
 * «usuario» —cierto en los cinco sitios— y no «revisor», que solo lo es en uno.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import UserPicker from "./UserPicker.svelte";

const USERS = [
  { id: "u1", username: "JEFE", is_active: true },
  { id: "u2", username: "OPERADOR", is_active: true },
];

describe("UserPicker — el vocabulario se declara", () => {
  it("sin declarar nada no habla de revisores ni de cadena", () => {
    render(UserPicker, { props: { id: "p1", users: USERS, max: 1 } });
    expect(screen.getByLabelText("Agregar a JEFE como usuario")).toBeInTheDocument();
    expect(screen.queryByLabelText(/a la cadena/)).toBeNull();
    expect(screen.queryByText(/Cadena de revisión/)).toBeNull();
  });

  it("el padre declara el sustantivo y el botón lo usa", () => {
    render(UserPicker, {
      props: { id: "p2", users: USERS, max: 1, noun: "responsable", listTitle: "Responsable" },
    });
    expect(screen.getByLabelText("Agregar a JEFE como responsable")).toBeInTheDocument();
    expect(screen.getByText(/^Responsable \(0\)$/)).toBeInTheDocument();
  });

  it("la cadena de revisión conserva su lenguaje cuando lo declara", () => {
    render(UserPicker, {
      props: {
        id: "p3",
        users: USERS,
        max: 20,
        noun: "revisor",
        nounPlural: "revisores",
        listTitle: "Cadena de revisión",
        emptyText: "Agregue revisores en el orden de revisión.",
      },
    });
    expect(screen.getByLabelText("Agregar a JEFE a la cadena")).toBeInTheDocument();
    expect(screen.getByText("Agregue revisores en el orden de revisión.")).toBeInTheDocument();
  });

  it("con un solo hueco no ofrece flechas de reordenar", () => {
    render(UserPicker, { props: { id: "p4", users: USERS, max: 1, value: ["u1"] } });
    expect(screen.queryByLabelText(/^Subir a /)).toBeNull();
    expect(screen.queryByLabelText(/^Bajar a /)).toBeNull();
    expect(screen.getByLabelText("Quitar a JEFE")).toBeInTheDocument();
  });

  it("con varios huecos sí las ofrece, y el orden se anuncia", () => {
    render(UserPicker, { props: { id: "p5", users: USERS, max: 20, value: ["u1", "u2"] } });
    expect(screen.getByLabelText("Subir a OPERADOR a la posición 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Quitar a JEFE de la cadena")).toBeInTheDocument();
  });

  it("llegar al máximo NO borra la ayuda que escribió el padre", () => {
    render(UserPicker, {
      props: {
        id: "p6",
        users: USERS,
        max: 1,
        value: ["u1"],
        hint: "Seleccione como máximo un responsable del traslado.",
      },
    });
    expect(
      screen.getByText("Seleccione como máximo un responsable del traslado."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Máximo 1/)).toBeNull();
  });
});
