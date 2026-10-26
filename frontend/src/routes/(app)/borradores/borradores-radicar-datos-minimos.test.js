// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Radicar un borrador captura los datos mínimos del radicado (B6-23, Ac. AGN
 * 060/2001 §5.2): tipo documental, origen (Salida) y metadatos del tipo.
 *
 * Hasta B6 el modal era un «¿Confirma?» y el proxy POSTeaba sin cuerpo: un
 * borrador cuya plantilla no declaraba un tipo del catálogo, o una Salida de
 * quien no tenía dependencia, se estrellaba contra el 422 del backend sin que la
 * pantalla pudiera hacer nada.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { plantillaMetadatos } from "$lib/testing/plantillaMetadatos.js";

const { default: Pagina } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { POST: RADICAR } = await import("./api/[id]/radicar/+server.js");

const TIPOS = [
  { code: "OFI", nombre: "Oficio" },
  { code: "RES", nombre: "Resolución" },
];
const DEPS = [
  { codigo: 10, nombre: "Gerencia", sigla: "GER" },
  { codigo: 20, nombre: "Jurídica", sigla: "JUR" },
];
const PLANTILLA_META = {
  activo: true,
  json_schema: {
    type: "object",
    required: ["reserva"],
    properties: { reserva: { type: "string", title: "Motivo de reserva" } },
  },
};

function borrador(extra = {}) {
  return {
    id: "b-1",
    estado: "aprobado",
    doc_type: "S",
    subject: "Asunto de prueba",
    plantilla_id: "p-1",
    dest_dept_code: 20,
    cuerpo: "<p>x</p>",
    cuerpo_html: "<p>x</p>",
    created_at: "2026-10-01T10:00:00Z",
    ...extra,
  };
}

function datos({ b = borrador(), plantillas, tiposError = null } = {}) {
  return {
    borradores: [b],
    plantillas: plantillas ?? [{ id: "p-1", nombre: "Oficio", tipo_documental: "OFI" }],
    dependencias: DEPS,
    dependenciasError: null,
    tiposDocumentales: TIPOS,
    tiposError,
    loadError: null,
    initialEstado: "",
  };
}

function sesion(depts = [{ depe_id: 10, is_active: true }], activa = 10) {
  clearSession();
  initSession({
    sub: "s1",
    user_id: "u1",
    email: "a@b.co",
    name: "A",
    roles: [],
    tenant_slug: "demo",
    permissions: ["PERM_RADI"],
    context: { user_id: "u1", active_depe_id: activa, available_depts: depts },
  });
}

function res(status, cuerpo) {
  return { ok: status < 400, status, json: async () => cuerpo };
}

/** fetch simulado: plantilla de metadatos por tipo y respuesta de radicar. */
function simular({
  metaPorTipo = {},
  radicar = res(201, { id: "r1", tracking_number: "2026-X-S-1" }),
} = {}) {
  const f = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/metadata-template")) {
      const tipo = new URL(u, "http://x").searchParams.get("tipo_documental");
      return res(200, metaPorTipo[tipo] ? [plantillaMetadatos(metaPorTipo[tipo])] : []);
    }
    if (u.endsWith("/radicar")) return radicar;
    if (u.includes("/firmantes")) return res(200, []);
    return res(404, {});
  });
  globalThis.fetch = f;
  return f;
}
const llamadaRadicar = (f) => f.mock.calls.find(([u]) => String(u).endsWith("/radicar"));

async function abrirModal() {
  await fireEvent.click(screen.getByRole("button", { name: /Editar borrador Asunto de prueba/i }));
  await fireEvent.click(await screen.findByRole("button", { name: /^Radicar$/ }));
  await screen.findByRole("dialog", { name: /Confirmar radicación/i });
}
const confirmar = () => screen.getByRole("button", { name: /Sí, radicar/i });

describe("borradores — radicar con datos mínimos", () => {
  const original = globalThis.fetch;
  beforeEach(() => sesion());
  afterEach(() => {
    globalThis.fetch = original;
  });

  it("Salida con tipo válido y una dependencia: el POST lleva tipo y origen", async () => {
    const f = simular();
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    expect(screen.getByLabelText(/Tipo documental/i).value).toBe("OFI");
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    await waitFor(() => expect(llamadaRadicar(f)).toBeTruthy());
    const cuerpo = JSON.parse(llamadaRadicar(f)[1].body);
    expect(cuerpo).toMatchObject({ tipo_documental: "OFI", origin_dept_code: 10 });
  });

  it("plantilla con tipo fuera del catálogo: select vacío, aviso y botón deshabilitado", async () => {
    simular();
    render(Pagina, {
      props: {
        data: datos({ plantillas: [{ id: "p-1", nombre: "X", tipo_documental: "Oficio libre" }] }),
      },
    });
    await abrirModal();
    expect(screen.getByLabelText(/Tipo documental/i).value).toBe("");
    expect(screen.getByText(/«Oficio libre», que no está en el catálogo/)).toBeTruthy();
    expect(confirmar().disabled).toBe(true);
    expect(screen.getByText(/Elija el tipo documental para poder radicar/)).toBeTruthy();
  });

  it("Salida sin dependencia activa: no hay botón de radicar y se explica", async () => {
    sesion([], null);
    simular();
    render(Pagina, { props: { data: datos() } });
    await fireEvent.click(
      screen.getByRole("button", { name: /Editar borrador Asunto de prueba/i }),
    );
    await screen.findByText(/No tiene una dependencia activa asignada/i);
    expect(screen.queryByRole("button", { name: /^Radicar$/ })).toBeNull();
  });

  it("un Interno no pide origen y muestra su destino; sin destino bloquea", async () => {
    simular();
    const { unmount } = render(Pagina, {
      props: { data: datos({ b: borrador({ doc_type: "I" }) }) },
    });
    await abrirModal();
    expect(screen.queryByLabelText(/Dependencia de origen/i)).toBeNull();
    expect(screen.getByText("Jurídica")).toBeTruthy();
    unmount();
    render(Pagina, {
      props: { data: datos({ b: borrador({ doc_type: "I", dest_dept_code: null }) }) },
    });
    await fireEvent.click(
      screen.getByRole("button", { name: /Editar borrador Asunto de prueba/i }),
    );
    expect(await screen.findByText(/no tiene dependencia destino: sin ella/i)).toBeTruthy();
    await fireEvent.click(await screen.findByRole("button", { name: /^Radicar$/ }));
    await screen.findByRole("dialog", { name: /Confirmar radicación/i });
    expect(confirmar().disabled).toBe(true);
    expect(confirmar().getAttribute("aria-describedby")).toBe("rad-motivo");
    expect(document.getElementById("rad-motivo").textContent).toMatch(
      /no tiene dependencia destino/i,
    );
  });

  it("sin contexto de dependencias cargado: aviso distinto, no afirma que no tenga dependencia", async () => {
    clearSession();
    initSession({
      sub: "s1",
      user_id: "u1",
      email: "a@b.co",
      name: "A",
      roles: [],
      tenant_slug: "demo",
      permissions: ["PERM_RADI"],
      context: null,
    });
    simular();
    render(Pagina, { props: { data: datos() } });
    await fireEvent.click(
      screen.getByRole("button", { name: /Editar borrador Asunto de prueba/i }),
    );
    await screen.findByText(/No se pudo cargar su contexto de dependencias/i);
    expect(screen.queryByText(/No tiene una dependencia activa asignada/i)).toBeNull();
  });

  it("422 tipo_documental_desconocido: mensaje en español junto al tipo y foco en él", async () => {
    simular({
      radicar: res(422, { error: "tipo_documental_desconocido", detail: "x", status: 422 }),
    });
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    expect(await screen.findAllByText(/no existe o está inactivo en el catálogo/)).toBeTruthy();
    await waitFor(() => expect(document.activeElement?.id).toBe("rad-tipo"));
  });

  it("422 datos_minimos_incompletos sin campo atribuible: el foco va al aviso general", async () => {
    simular({
      radicar: res(422, {
        error: "datos_minimos_incompletos",
        detail: "Faltan datos mínimos.",
        field: "dest_dept_code",
        status: 422,
      }),
    });
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    await waitFor(() => expect(document.activeElement?.id).toBe("rad-error-general"));
  });

  it("con varias dependencias se elige el origen (precarga la activa)", async () => {
    sesion(
      [
        { depe_id: 10, is_active: true },
        { depe_id: 20, is_active: true },
      ],
      20,
    );
    const f = simular();
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    const origen = screen.getByLabelText(/Dependencia de origen/i);
    expect(origen.value).toBe("20");
    await fireEvent.change(origen, { target: { value: "10" } });
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    await waitFor(() => expect(llamadaRadicar(f)).toBeTruthy());
    expect(JSON.parse(llamadaRadicar(f)[1].body).origin_dept_code).toBe(10);
  });

  it("tipo con plantilla `required`: aparece el campo y sin valor no se envía", async () => {
    const f = simular({ metaPorTipo: { OFI: PLANTILLA_META } });
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    const campo = await screen.findByLabelText(/Motivo de reserva/i);
    expect(confirmar().disabled).toBe(true);
    expect(screen.getByText(/Complete el metadato obligatorio «Motivo de reserva»/)).toBeTruthy();
    await fireEvent.click(confirmar());
    expect(llamadaRadicar(f)).toBeUndefined();
    await fireEvent.input(campo, { target: { value: "Ley 1712" } });
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    await waitFor(() => expect(llamadaRadicar(f)).toBeTruthy());
    expect(JSON.parse(llamadaRadicar(f)[1].body).metadata).toEqual({ reserva: "Ley 1712" });
  });

  it("422 metadata_validation_failed: error junto al control, modal abierto y 2.º ciclo limpio", async () => {
    const f = simular({
      metaPorTipo: { OFI: PLANTILLA_META },
      radicar: res(422, {
        error: "metadata_validation_failed",
        detail: "El valor no es válido.",
        field: "reserva",
        status: 422,
      }),
    });
    render(Pagina, { props: { data: datos() } });
    await abrirModal();
    const campo = await screen.findByLabelText(/Motivo de reserva/i);
    await fireEvent.input(campo, { target: { value: "x" } });
    await waitFor(() => expect(confirmar().disabled).toBe(false));
    await fireEvent.click(confirmar());
    await waitFor(() =>
      expect(screen.getAllByText("El valor no es válido.").length).toBeGreaterThan(0),
    );
    // El modal sigue abierto y lo capturado se conserva.
    expect(screen.getByRole("dialog", { name: /Confirmar radicación/i })).toBeTruthy();
    expect(screen.getByLabelText(/Motivo de reserva/i).value).toBe("x");
    expect(screen.getByLabelText(/Motivo de reserva/i).getAttribute("aria-invalid")).toBe("true");
    expect(f).toBeTruthy();

    // 2.º ciclo: cerrar y reabrir deja el estado limpio.
    await fireEvent.click(screen.getByRole("button", { name: /Cancelar/i }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /Confirmar radicación/i })).toBeNull(),
    );
    await fireEvent.click(screen.getByRole("button", { name: /^Radicar$/ }));
    await screen.findByRole("dialog", { name: /Confirmar radicación/i });
    const campoNuevo = await screen.findByLabelText(/Motivo de reserva/i);
    expect(campoNuevo.value).toBe("");
    expect(campoNuevo.getAttribute("aria-invalid")).not.toBe("true");
    expect(screen.queryByText("El valor no es válido.")).toBeNull();
    expect(screen.queryByText("Revise el metadato señalado.")).toBeNull();
  });
});

describe("borradores — proxy de radicar", () => {
  const original = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = original;
  });
  const sesionProxy = { accessToken: "tok", user: { tenant_slug: "demo", sub: "u1" } };

  function llamar(body) {
    const f = vi.fn(async () => ({ status: 201, json: async () => ({ id: "r1" }) }));
    globalThis.fetch = f;
    const request = new Request("http://x/borradores/api/b-1/radicar", {
      method: "POST",
      body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
    });
    return RADICAR({ locals: sesionProxy, params: { id: "b-1" }, request }).then((r) => ({ r, f }));
  }

  it("reenvía solo las cuatro claves admitidas", async () => {
    const { f } = await llamar({
      tipo_documental: "OFI",
      origin_dept_code: 10,
      dest_dept_code: 20,
      metadata: { a: 1 },
      nivel_seguridad: 3,
      documento: "otro",
    });
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({
      tipo_documental: "OFI",
      origin_dept_code: 10,
      dest_dept_code: 20,
      metadata: { a: 1 },
    });
  });

  it("rechaza con 400 una clave reservada en metadata y un cuerpo desmesurado", async () => {
    for (const k of ["from_borrador", "canal", "consentimiento"]) {
      const { r, f } = await llamar({ metadata: { [k]: "x" } });
      expect(r.status).toBe(400);
      expect(f).not.toHaveBeenCalled();
    }
    const { r, f } = await llamar(JSON.stringify({ metadata: { a: "x".repeat(70 * 1024) } }));
    expect(r.status).toBe(413);
    expect(f).not.toHaveBeenCalled();
  });

  it("sin cuerpo no manda cuerpo (retrocompatible)", async () => {
    const { f } = await llamar(undefined);
    expect(f.mock.calls[0][1].body).toBeUndefined();
  });

  it("rechaza con 400 un código fuera de rango, un tipo no cadena y metadatos no objeto", async () => {
    for (const malo of [
      { origin_dept_code: 0 },
      { dest_dept_code: 1000000 },
      { origin_dept_code: "10" },
      { tipo_documental: 5 },
      { tipo_documental: "x".repeat(101) },
      { metadata: [1] },
    ]) {
      const { r, f } = await llamar(malo);
      expect(r.status).toBe(400);
      expect(f).not.toHaveBeenCalled();
    }
    const { r } = await llamar("{no es json");
    expect(r.status).toBe(400);
  });
});
