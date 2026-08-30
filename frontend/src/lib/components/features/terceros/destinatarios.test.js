// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * destinatarios.test.js — El selector de destinatarios (E01, RF-RAD-05).
 *
 * Lo que esta pantalla no se puede permitir:
 *
 *  - **Ofrecer un canal cuyo dato falta.** Sin correo no hay envío electrónico y
 *    sin dirección no hay físico: ofrecer la opción haría descubrir el límite
 *    chocando con el backend, cuando el dato se puede completar aquí mismo.
 *  - **Callar que un destinatario no tiene por dónde recibir nada.**
 *  - **Dejar creer que se creó una ficha nueva** cuando el alta devolvió una que
 *    ya existía: es exactamente la confusión de la que salieron los treinta
 *    «José Pérez» del Orfeo original.
 *  - **Sugerir que corregir el contacto cambia el catálogo**: vale solo para
 *    este envío, y queda congelado con él.
 */
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));

const { default: Picker } = await import("./DestinatariosPicker.svelte");

const texto = () => document.body.textContent.replace(/\s+/g, " ");

const destinatario = (o = {}) => ({
  tercero_id: "11111111-1111-4111-8111-111111111111",
  rol: "destinatario",
  nombre: "José Pérez",
  entidad: null,
  email: "jose@ejemplo.co",
  telefono: null,
  direccion: "Calle 1 #2-3",
  canal: "",
  ...o,
});

function opcionesDeCanal() {
  const select = document.getElementById("canal-0");
  return [...select.options].map((o) => o.textContent.trim());
}

describe("selector de destinatarios", () => {
  it("con correo y dirección ofrece los tres canales", () => {
    render(Picker, { props: { destinatarios: [destinatario()] } });
    const opciones = opcionesDeCanal();
    expect(opciones).toContain("Correo electrónico");
    expect(opciones).toContain("Físico");
    expect(opciones).toContain("Ambos");
  });

  it("SIN dirección no ofrece envío físico ni «ambos»", () => {
    render(Picker, { props: { destinatarios: [destinatario({ direccion: null })] } });
    const opciones = opcionesDeCanal();
    expect(opciones).toContain("Correo electrónico");
    expect(opciones).not.toContain("Físico");
    expect(opciones).not.toContain("Ambos");
  });

  it("SIN correo no ofrece envío electrónico ni «ambos»", () => {
    render(Picker, { props: { destinatarios: [destinatario({ email: null })] } });
    const opciones = opcionesDeCanal();
    expect(opciones).not.toContain("Correo electrónico");
    expect(opciones).toContain("Físico");
    expect(opciones).not.toContain("Ambos");
  });

  it("sin correo NI dirección lo DICE: no hay canal posible", () => {
    render(Picker, {
      props: { destinatarios: [destinatario({ email: null, direccion: null })] },
    });
    expect(texto()).toMatch(/no se le puede enviar por ningún canal/i);
  });

  it("si el canal quedó marcado y falta el dato, avisa nombrando qué falta", () => {
    // Ocurre cuando alguien elige el canal y luego vacía el dato al corregir el
    // contacto: la opción ya no se ofrece, pero el valor sigue puesto.
    render(Picker, {
      props: { destinatarios: [destinatario({ direccion: null, canal: "fisico" })] },
    });
    expect(texto()).toMatch(/le falta una dirección física/i);
    expect(texto()).toMatch(/sin ese dato el envío no se puede hacer/i);
  });

  it("el buscador va primero y explica por qué", () => {
    render(Picker, { props: { destinatarios: [] } });
    expect(screen.getByRole("button", { name: /^Buscar$/i })).toBeInTheDocument();
    expect(texto()).toMatch(/no encontrar la ficha que ya existe/i);
  });

  it("el alta solo aparece tras una búsqueda sin resultados", async () => {
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => [] }));
    render(Picker, { props: { destinatarios: [] } });

    expect(screen.queryByRole("button", { name: /Registrar un tercero nuevo/i })).toBeNull();
    await fireEvent.click(screen.getByRole("button", { name: /^Buscar$/i }));
    expect(
      await screen.findByRole("button", { name: /Registrar un tercero nuevo/i }),
    ).toBeInTheDocument();
  });

  it("corregir el contacto DICE que no cambia la ficha del catálogo", async () => {
    render(Picker, { props: { destinatarios: [destinatario()] } });
    await fireEvent.click(screen.getByRole("button", { name: /Corregir contacto/i }));
    expect(texto()).toMatch(/solo para este radicado/i);
    expect(texto()).toMatch(/no modifica la ficha del catálogo/i);
  });

  it("la lista dice cuántos destinatarios hay: uno o varios", () => {
    render(Picker, {
      props: {
        destinatarios: [
          destinatario(),
          destinatario({ tercero_id: "22222222-2222-4222-8222-222222222222", nombre: "Ana Ruiz" }),
        ],
      },
    });
    expect(texto()).toMatch(/Destinatarios de este radicado \(2\)/);
  });
});
