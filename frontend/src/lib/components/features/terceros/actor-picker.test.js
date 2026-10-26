// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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

const { default: Picker } = await import("./ActorPicker.svelte");

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

describe("el mismo selector sirve al REMITENTE de una Entrada", () => {
  // Por qué existe este bloque: el componente solo cubría destinatarios, y esa
  // asimetría era la causa de los duplicados. El remitente de una Entrada se
  // tecleaba en dos campos de texto libre, así que el directorio solo podía
  // aprender de las Salidas — y de 182 radicados, 104 eran Entradas.

  it("habla de remitente, no de destinatario", () => {
    render(Picker, { props: { destinatarios: [], rol: "remitente", max: 1 } });
    expect(screen.getByLabelText(/Buscar remitente/i)).toBeInTheDocument();
    expect(texto()).toMatch(/Toda Entrada tiene/i);
  });

  it("marca el actor añadido con el rol que se le pidió", async () => {
    // La aserción que importa: lo que se emite, no lo que se pinta. El `rol`
    // viaja al backend y decide si esa persona es de quien viene el documento o
    // a quien va — pintarlo bien y emitir "destinatario" sería peor que no
    // tener el selector.
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify([{ id: "33333333-3333-4333-8333-333333333333", nombre: "Luz Mora" }]),
          {
            status: 200,
          },
        ),
    );
    const { component } = render(Picker, {
      props: { destinatarios: [], rol: "remitente", max: 1 },
    });
    /** @type {any[]} */
    let emitido = [];
    component.$on("cambio", (e) => (emitido = e.detail));

    await fireEvent.input(screen.getByLabelText(/Buscar remitente/i), {
      target: { value: "Luz" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /^Buscar$/i }));
    const añadir = await screen.findByRole("button", { name: /Añadir/i });
    await fireEvent.click(añadir);

    expect(emitido).toHaveLength(1);
    expect(emitido[0].rol).toBe("remitente");
  });

  it("con el cupo lleno RETIRA el buscador, en vez de dejar buscar y luego negar", () => {
    render(Picker, {
      props: { destinatarios: [destinatario()], rol: "remitente", max: 1 },
    });
    expect(screen.queryByLabelText(/Buscar remitente/i)).toBeNull();
    expect(texto()).toMatch(/Ya hay remitente/i);
  });

  it("y RETIRA también los resultados ya listados, con sus botones «Añadir»", async () => {
    // Este es el defecto que el test de arriba no veía, y se encontró pulsando:
    // al llenarse el cupo desaparecía el formulario de búsqueda, pero la lista
    // de la búsqueda ANTERIOR seguía en pantalla con sus «Añadir» vivos. Dos
    // clics y se llegaba a «Remitente de este radicado (2)» sobre una Entrada.
    //
    // Y el sitio donde se enteraba uno era el peor posible:
    // `ux_radicado_actor_remitente` es UNIQUE por radicado, así que el segundo
    // remitente no falla al elegirlo sino al pulsar «Radicar», con los anexos
    // subidos y el formulario entero relleno.
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify([
            { id: "44444444-4444-4444-8444-444444444444", nombre: "Rosa Lima" },
            { id: "55555555-5555-4555-8555-555555555555", nombre: "Rosa Lima" },
          ]),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );
    render(Picker, { props: { destinatarios: [], rol: "remitente", max: 1 } });

    await fireEvent.click(screen.getByRole("button", { name: /^Buscar$/i }));
    const anadir = await screen.findAllByRole("button", { name: /^Añadir$/i });
    expect(anadir).toHaveLength(2);

    await fireEvent.click(anadir[0]);

    expect(texto()).toMatch(/Remitente de este radicado \(1\)/);
    expect(screen.queryAllByRole("button", { name: /^Añadir$/i })).toHaveLength(0);
  });
});
