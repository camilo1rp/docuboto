// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/terceros — «Fichas por conciliar» (C17): la cola, las tres acciones, el
 * motivo cuando se exige y el gate por PERM_RADI nivel 3 (espejo del backend).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import Pagina from "./+page.svelte";

const { initSession, clearSession } = await import("$lib/stores/auth.js");

const ID = "11111111-1111-4111-8111-111111111111";
const DESTINO = "22222222-2222-4222-8222-222222222222";
const originalFetch = globalThis.fetch;

function sesion(nivel) {
  return {
    sub: "kc-1",
    user_id: "u-1",
    tenant_slug: "demo",
    roles: [],
    permissions: nivel === undefined ? [] : ["PERM_RADI"],
    permission_levels: nivel === undefined ? {} : { PERM_RADI: nivel },
  };
}

function provisional(extra = {}) {
  return {
    id: ID,
    nombre: "Ana Ruiz",
    entidad: null,
    email: "ana@example.org",
    telefono: null,
    direccion: null,
    documento_declarado_tipo: "CC",
    documento_declarado_numero: "1020304050",
    creado_at: "2026-10-01T10:00:00Z",
    radicados: [{ tracking_number: "2026-DEMO-E-000007", creado_at: "2026-10-01T10:00:00Z" }],
    candidata: { id: DESTINO, nombre: "Ana María Ruiz", email: null, municipio: "Bogotá" },
    ...extra,
  };
}

const respuesta = (status, cuerpo, total) => ({
  ok: status < 400,
  status,
  headers: { get: (k) => (k.toLowerCase() === "x-total-count" && total ? String(total) : null) },
  json: async () => cuerpo,
});

/** Backend simulado: cola paginada + resultado configurable de `conciliar`. */
function backend({ cola = [provisional()], total, conciliar = respuesta(200, { ok: true }) } = {}) {
  const llamadas = [];
  const f = vi.fn(async (url, init) => {
    llamadas.push([String(url), init]);
    if (String(url).includes("accion=por_conciliar")) {
      return respuesta(200, cola, total ?? cola.length);
    }
    if (String(url).includes("accion=conciliar")) return conciliar;
    if (String(url).includes("accion=buscar")) {
      return respuesta(200, [
        {
          id: DESTINO,
          nombre: "Ana María Ruiz",
          numero_documento: "1020304050",
          municipio: "Bogotá",
        },
        {
          id: "33333333-3333-4333-8333-333333333333",
          nombre: "Ana Ruiz Gómez",
          numero_documento: "99",
        },
      ]);
    }
    return respuesta(200, []);
  });
  globalThis.fetch = f;
  return { f, llamadas };
}

const datos = { terceros: [], duplicados: [], terceroError: null, duplicadoError: null };

beforeEach(() => clearSession());
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("Fichas por conciliar — gate", () => {
  it("con PERM_RADI nivel 3 muestra la sección y pide la cola", async () => {
    initSession(sesion(3));
    const { f } = backend();
    render(Pagina, { data: datos });
    expect(await screen.findByRole("heading", { name: /Fichas por conciliar/ })).toBeTruthy();
    expect(await screen.findByText("Ana Ruiz")).toBeTruthy();
    expect(f.mock.calls[0][0]).toBe("/admin/terceros/api?accion=por_conciliar&page=1&size=20");
  });

  it.each([[1], [2], [undefined]])(
    "con nivel %s NO muestra la sección ni pide la cola (control positivo: con 3 sí)",
    async (nivel) => {
      initSession(sesion(nivel));
      const { f } = backend();
      render(Pagina, { data: datos });
      await Promise.resolve();
      expect(screen.queryByRole("heading", { name: /Fichas por conciliar/ })).toBeNull();
      expect(f.mock.calls.some(([u]) => String(u).includes("por_conciliar"))).toBe(false);
    },
  );
});

describe("Fichas por conciliar — cola", () => {
  beforeEach(() => initSession(sesion(3)));

  it("distingue la identidad provisional y muestra documento declarado, radicados y candidata", async () => {
    backend();
    render(Pagina, { data: datos });
    expect(await screen.findByText("Identidad provisional")).toBeTruthy();
    expect(screen.getByText(/CC 1020304050/)).toBeTruthy();
    expect(screen.getByText("2026-DEMO-E-000007")).toBeTruthy();
    expect(screen.getByText("Ana María Ruiz")).toBeTruthy();
    // Con candidata no se ofrece «Confirmar como ficha nueva».
    expect(screen.queryByRole("button", { name: /Confirmar como ficha nueva/ })).toBeNull();
  });

  it("sin candidata ofrece confirmar como ficha nueva", async () => {
    backend({ cola: [provisional({ candidata: null })] });
    render(Pagina, { data: datos });
    expect(await screen.findByRole("button", { name: /Confirmar como ficha nueva/ })).toBeTruthy();
    expect(screen.getByText(/No hay ficha confirmada con ese documento/)).toBeTruthy();
  });

  it("estado vacío explícito", async () => {
    backend({ cola: [] });
    render(Pagina, { data: datos });
    expect(await screen.findByText("No hay fichas por conciliar")).toBeTruthy();
  });

  it("estado de error con reintento", async () => {
    globalThis.fetch = vi.fn(async () => respuesta(500, { detail: "caído" }));
    render(Pagina, { data: datos });
    expect(await screen.findByText(/caído/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
  });

  it("pagina con X-Total-Count: Siguiente pide la página 2", async () => {
    const { f } = backend({ total: 45 });
    render(Pagina, { data: datos });
    expect(await screen.findByText(/1–1 de 45/)).toBeTruthy();
    const siguiente = screen.getByRole("button", { name: "Siguiente" });
    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
    // 1 fila de 20 simulada: el servidor dice que hay más porque el total lo supera.
    await waitFor(() => expect(siguiente).not.toBeDisabled());
    await fireEvent.click(siguiente);
    await waitFor(() =>
      expect(f.mock.calls.some(([u]) => String(u).includes("page=2&size=20"))).toBe(true),
    );
  });
});

describe("Fichas por conciliar — acciones", () => {
  beforeEach(() => initSession(sesion(3)));

  async function abrir(nombreBoton) {
    render(Pagina, { data: datos });
    await fireEvent.click(await screen.findByRole("button", { name: nombreBoton }));
    return await screen.findByRole("dialog");
  }

  function cuerpoConciliar(llamadas) {
    const l = llamadas.find(([u]) => u.includes("accion=conciliar"));
    return l ? JSON.parse(l[1].body) : null;
  }

  const AVISO =
    /No escriba números de documento ni datos personales: el motivo queda en el registro de auditoría, que no se puede borrar\./;

  async function elegirMedio(dialogo, valor = "presencial_documento") {
    await fireEvent.change(within(dialogo).getByLabelText(/Cómo se comprobó/), {
      target: { value: valor },
    });
  }

  async function elegirCandidata(dialogo) {
    await fireEvent.click(within(dialogo).getByRole("button", { name: "Elegir esta ficha" }));
  }

  const botonFinal = (dialogo, nombre) =>
    within(dialogo).getAllByRole("button", { name: nombre }).pop();

  it("vincular: NO preselecciona la candidata; hay que elegirla, indicar el medio y teclear la frase", async () => {
    const { llamadas } = backend();
    const dialogo = await abrir(/Vincular a la ficha existente/);
    // Sin preselección: nada elegido hasta que quien concilia lo decide.
    expect(within(dialogo).queryByTestId("destino-elegido")).toBeNull();
    expect(within(dialogo).getByTestId("candidata-modal")).toHaveTextContent("Ana María Ruiz");
    // El selector ofrece las cuatro etiquetas en español.
    const opciones = Array.from(
      within(dialogo)
        .getByLabelText(/Cómo se comprobó/)
        .querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(opciones).toEqual(
      expect.arrayContaining([
        "Presentó el documento en persona",
        "Respondió por el canal ya registrado",
        "Llamada al teléfono ya registrado",
        "Otro (explique)",
      ]),
    );

    await elegirCandidata(dialogo);
    expect(within(dialogo).getByTestId("destino-elegido")).toHaveTextContent("Ana María Ruiz");
    expect(within(dialogo).getByText("Documento coincide")).toBeTruthy();
    // Sin motivo (ni aviso): el documento coincide y el medio no es «otro».
    expect(within(dialogo).queryByLabelText(/Motivo/)).toBeNull();

    const enviar = botonFinal(dialogo, /Vincular a la ficha existente/);
    await fireEvent.input(within(dialogo).getByLabelText(/Escribe «vincular»/), {
      target: { value: "vincular" },
    });
    // Control: con ficha y frase pero SIN medio, sigue deshabilitado.
    expect(enviar).toBeDisabled();
    await elegirMedio(dialogo);
    expect(enviar).not.toBeDisabled();
    await fireEvent.click(enviar);

    await waitFor(() => expect(cuerpoConciliar(llamadas)).not.toBeNull());
    expect(cuerpoConciliar(llamadas)).toEqual({
      accion: "vincular",
      destino_id: DESTINO,
      medio_verificacion: "presencial_documento",
      motivo: null,
    });
    const llamada = llamadas.find(([u]) => u.includes("accion=conciliar"));
    expect(llamada[0]).toBe(`/admin/terceros/api?accion=conciliar&id=${ID}`);
  });

  it("con medio «Otro» el motivo es obligatorio (≥ 10) y aparece el aviso de auditoría", async () => {
    const { llamadas } = backend();
    const dialogo = await abrir(/Vincular a la ficha existente/);
    await elegirCandidata(dialogo);
    await fireEvent.input(within(dialogo).getByLabelText(/Escribe «vincular»/), {
      target: { value: "vincular" },
    });
    const enviar = botonFinal(dialogo, /Vincular a la ficha existente/);
    await elegirMedio(dialogo, "respuesta_canal_registrado");
    expect(within(dialogo).queryByText(AVISO)).toBeNull();
    expect(enviar).not.toBeDisabled();

    await elegirMedio(dialogo, "otro");
    expect(within(dialogo).getByText(AVISO)).toBeTruthy();
    expect(enviar).toBeDisabled();
    const motivo = within(dialogo).getByLabelText(/Motivo/);
    await fireEvent.input(motivo, { target: { value: "corto" } });
    expect(enviar).toBeDisabled();
    await fireEvent.input(motivo, { target: { value: "Lo confirmó el jefe de la dependencia" } });
    expect(enviar).not.toBeDisabled();
    await fireEvent.click(enviar);
    await waitFor(() => expect(cuerpoConciliar(llamadas)).not.toBeNull());
    expect(cuerpoConciliar(llamadas)).toMatchObject({
      medio_verificacion: "otro",
      motivo: "Lo confirmó el jefe de la dependencia",
    });
  });

  it("vincular a una ficha de documento distinto exige motivo de al menos 10 caracteres", async () => {
    const { llamadas } = backend();
    const dialogo = await abrir(/Vincular a la ficha existente/);
    await fireEvent.input(within(dialogo).getByLabelText(/Buscar la ficha/), {
      target: { value: "ruiz" },
    });
    await fireEvent.click(within(dialogo).getByRole("button", { name: "Buscar" }));
    const otra = await within(dialogo).findByText("Ana Ruiz Gómez");
    await fireEvent.click(within(otra.closest("li")).getByRole("button", { name: "Elegir" }));
    expect(within(dialogo).getByText("Documento distinto")).toBeTruthy();

    await fireEvent.input(within(dialogo).getByLabelText(/Escribe «vincular»/), {
      target: { value: "vincular" },
    });
    await elegirMedio(dialogo);
    const enviar = botonFinal(dialogo, /Vincular a la ficha existente/);
    expect(enviar).toBeDisabled(); // falta el motivo
    const motivo = within(dialogo).getByLabelText(/Motivo/);
    expect(within(dialogo).getByText(AVISO)).toBeTruthy();
    await fireEvent.input(motivo, { target: { value: "corto" } });
    expect(enviar).toBeDisabled();
    await fireEvent.input(motivo, { target: { value: "El ciudadano confirmó por teléfono" } });
    expect(enviar).not.toBeDisabled();
    await fireEvent.click(enviar);
    await waitFor(() => expect(cuerpoConciliar(llamadas)).not.toBeNull());
    expect(cuerpoConciliar(llamadas).destino_id).toBe("33333333-3333-4333-8333-333333333333");
    expect(cuerpoConciliar(llamadas).motivo).toBe("El ciudadano confirmó por teléfono");
  });

  it("confirmar (sin candidata) exige el medio, no pide motivo ni destino", async () => {
    const { llamadas } = backend({ cola: [provisional({ candidata: null })] });
    const dialogo = await abrir(/Confirmar como ficha nueva/);
    expect(within(dialogo).queryByLabelText(/Motivo/)).toBeNull();
    const enviar = botonFinal(dialogo, /Confirmar como ficha nueva/);
    expect(enviar).toBeDisabled();
    await elegirMedio(dialogo, "llamada_telefono_registrado");
    expect(enviar).not.toBeDisabled();
    await fireEvent.click(enviar);
    await waitFor(() => expect(cuerpoConciliar(llamadas)).not.toBeNull());
    expect(cuerpoConciliar(llamadas)).toEqual({
      accion: "confirmar",
      destino_id: null,
      medio_verificacion: "llamada_telefono_registrado",
      motivo: null,
    });
  });

  it("descartar NO ofrece ni envía medio; exige motivo de al menos 10 caracteres y muestra el aviso", async () => {
    const { llamadas } = backend();
    const dialogo = await abrir(/^Descartar$/);
    expect(within(dialogo).queryByLabelText(/Cómo se comprobó/)).toBeNull();
    expect(within(dialogo).getByText(AVISO)).toBeTruthy();
    const enviar = botonFinal(dialogo, /Descartar la identidad/);
    expect(enviar).toBeDisabled();
    await fireEvent.input(within(dialogo).getByLabelText(/Motivo/), {
      target: { value: "corto" },
    });
    expect(enviar).toBeDisabled();
    await fireEvent.input(within(dialogo).getByLabelText(/Motivo/), {
      target: { value: "Sospecha de suplantación" },
    });
    await fireEvent.click(enviar);
    await waitFor(() => expect(cuerpoConciliar(llamadas)).not.toBeNull());
    const cuerpo = cuerpoConciliar(llamadas);
    expect(cuerpo).toEqual({
      accion: "descartar",
      destino_id: null,
      motivo: "Sospecha de suplantación",
    });
    expect("medio_verificacion" in cuerpo).toBe(false);
  });

  it("tras conciliar recarga la cola", async () => {
    const { f } = backend({ cola: [provisional({ candidata: null })] });
    const dialogo = await abrir(/Confirmar como ficha nueva/);
    const antes = f.mock.calls.filter(([u]) => String(u).includes("por_conciliar")).length;
    await elegirMedio(dialogo);
    await fireEvent.click(botonFinal(dialogo, /Confirmar como ficha nueva/));
    await waitFor(() =>
      expect(f.mock.calls.filter(([u]) => String(u).includes("por_conciliar")).length).toBe(
        antes + 1,
      ),
    );
  });

  it.each([
    [409, "documento_ya_registrado", /Ya existe una ficha confirmada con ese documento/],
    [409, "tercero_ya_conciliado", /ya fue conciliada/],
    [409, "destino_invalido", /fusionado con otra/],
    [404, "tercero_no_encontrado", /dejó de ser visible/],
    [422, "motivo_requerido", /Escriba el motivo: esta decisión lo exige/],
    [422, "medio_verificacion_requerido", /medio de verificación/],
    [422, "medio_verificacion_no_aplica", /Descartar no lleva medio/],
    [422, "documento_sin_tipo", /sin tipo/],
  ])(
    "%i %s: mensaje en español dentro del modal, sin perder lo escrito",
    async (st, codigo, mensaje) => {
      backend({
        cola: [provisional({ candidata: null })],
        conciliar: respuesta(st, { detail: { error: codigo, detail: "x", status: st } }),
      });
      const dialogo = await abrir(/Confirmar como ficha nueva/);
      await elegirMedio(dialogo);
      await fireEvent.click(botonFinal(dialogo, /Confirmar como ficha nueva/));
      expect(await within(dialogo).findByRole("alert")).toHaveTextContent(mensaje);
      // El modal sigue abierto y el medio elegido se conserva.
      expect(screen.getByRole("dialog")).toBeTruthy();
      expect(within(dialogo).getByLabelText(/Cómo se comprobó/).value).toBe("presencial_documento");
    },
  );

  it("destino_invalido deja sin destino: hay que elegir otra ficha", async () => {
    backend({
      conciliar: respuesta(409, { detail: { error: "destino_invalido", status: 409 } }),
    });
    const dialogo = await abrir(/Vincular a la ficha existente/);
    await elegirCandidata(dialogo);
    await fireEvent.input(within(dialogo).getByLabelText(/Escribe «vincular»/), {
      target: { value: "vincular" },
    });
    await elegirMedio(dialogo);
    await fireEvent.click(botonFinal(dialogo, /Vincular a la ficha existente/));
    await within(dialogo).findByRole("alert");
    expect(within(dialogo).queryByTestId("destino-elegido")).toBeNull();
    expect(botonFinal(dialogo, /Vincular a la ficha existente/)).toBeDisabled();
  });
});
