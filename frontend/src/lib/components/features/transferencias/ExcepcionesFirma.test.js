// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ExcepcionesFirma — declarar y revocar la «firma inobtenible» del acta.
 *
 * Cada caso fija una mutación posible:
 *  - ofrecer declarar a quien es parte de la transferencia,
 *  - ofrecer declarar con administración < 3 o permiso de firma < 2,
 *  - no pedir TOTP con política `todos`,
 *  - re-declarar un rol con excepción revocada,
 *  - enviar sin la confirmación explícita,
 *  - mostrar el fundamento a quien el servidor no se lo entregó.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

const { default: ExcepcionesFirma } = await import("./ExcepcionesFirma.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const EXTERNO = "99999999-9999-4999-8999-999999999999";
const T = {
  id: "t-1",
  estado: "recibida",
  expediente_id: "e-1",
  creada_por: "u-elab",
  enviada_por: "u-env",
  decidida_por: "u-rec",
};
const RADICADOS = [
  { radicado_id: "11111111-1111-4111-8111-111111111111", tracking_number: "2026-X-E-000001" },
];
const ANEXO = "22222222-2222-4222-8222-222222222222";

function sesion(userId = EXTERNO, levels = { USUA_PERM_ADMIN: 3, PERM_FIRMA: 2 }) {
  initSession({
    sub: "kc-sub",
    user_id: userId,
    name: "Admin",
    tenant_slug: "demo",
    permissions: Object.keys(levels),
    permission_levels: levels,
  });
}

const verif = (extra = {}) => ({
  firmas_personales: [],
  excepciones: [],
  politica_xades: "off",
  ...extra,
});

function exc(rol, extra = {}) {
  return {
    id: `x-${rol}`,
    rol,
    motivo: "fallecimiento",
    fundamento: null,
    fundamento_compromiso: "ab".repeat(32),
    ordinal: 1,
    referencia_documental: "radicado:11111111-1111-4111-8111-111111111111",
    declarada_en: "2026-10-09T10:00:00Z",
    firmada: true,
    sello_valida: true,
    revocada: false,
    ...extra,
  };
}

let fetchMock;
function respuestas(map) {
  map = { "/api/anexos/": { body: [{ file_id: ANEXO, filename: "defuncion.pdf" }] }, ...map };
  fetchMock = vi.fn(async (url, init) => {
    for (const [fragmento, resp] of Object.entries(map)) {
      if (String(url).includes(fragmento)) {
        const r = typeof resp === "function" ? resp(url, init) : resp;
        return new Response(JSON.stringify(r.body ?? {}), { status: r.status ?? 200 });
      }
    }
    return new Response("{}", { status: 404 });
  });
  globalThis.fetch = fetchMock;
}

const originalFetch = globalThis.fetch;
beforeEach(() => {
  clearSession();
  respuestas({ "/api/anexos/": { body: [{ file_id: ANEXO, filename: "defuncion.pdf" }] } });
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

const montar = (props = {}) =>
  render(ExcepcionesFirma, {
    props: { transferencia: T, acta: null, verificacion: verif(), radicados: RADICADOS, ...props },
  });

const botonDeclarar = () => screen.queryByRole("button", { name: /declarar firma inobtenible/i });

async function llenarYConfirmar({ totp = null, anexo = true } = {}) {
  await fireEvent.click(botonDeclarar());
  await fireEvent.change(screen.getByLabelText(/parte que no puede firmar/i), {
    target: { value: "recibe" },
  });
  await fireEvent.change(screen.getByLabelText(/^motivo/i), { target: { value: "fallecimiento" } });
  await fireEvent.input(screen.getByLabelText(/^fundamento/i), {
    target: { value: "El receptor falleció el 3 de octubre de 2026." },
  });
  await fireEvent.change(screen.getByLabelText(/radicado de soporte/i), {
    target: { value: RADICADOS[0].radicado_id },
  });
  if (anexo) {
    await waitFor(() => expect(screen.getByRole("option", { name: "defuncion.pdf" })).toBeTruthy());
    await fireEvent.change(screen.getByLabelText(/anexo de soporte/i), {
      target: { value: ANEXO },
    });
  }
  if (totp)
    await fireEvent.input(screen.getByLabelText(/código de verificación/i), {
      target: { value: totp },
    });
}
const confirmar = () => fireEvent.click(screen.getByLabelText(/entiendo que esta declaración/i));
const enviar = () =>
  fireEvent.click(screen.getAllByRole("button", { name: /declarar firma inobtenible/i }).at(-1));

describe("ExcepcionesFirma — quién ve el acto", () => {
  it("un tercero con administración 3 y firma 2 ve el botón de declarar", () => {
    sesion();
    montar();
    expect(botonDeclarar()).toBeTruthy();
  });

  it.each([
    ["creó (elabora)", "u-elab"],
    ["envió (entrega)", "u-env"],
    ["recibió (recibe)", "u-rec"],
  ])("quien %s la transferencia NO ve el botón", (_, id) => {
    sesion(id);
    montar();
    expect(botonDeclarar()).toBeNull();
  });

  it("administración 2 no lo ve", () => {
    sesion(EXTERNO, { USUA_PERM_ADMIN: 2, PERM_FIRMA: 2 });
    montar();
    expect(botonDeclarar()).toBeNull();
  });

  it("permiso de firma 1 no lo ve", () => {
    sesion(EXTERNO, { USUA_PERM_ADMIN: 3, PERM_FIRMA: 1 });
    montar();
    expect(botonDeclarar()).toBeNull();
  });

  it("si todos los roles ya firmaron o tienen excepción, no hay nada que declarar", () => {
    sesion();
    montar({
      verificacion: verif({
        firmas_personales: [{ rol: "entrega" }],
        excepciones: [exc("recibe"), exc("elabora")],
      }),
    });
    expect(botonDeclarar()).toBeNull();
  });

  it("el modal ofrece un rol con excepción revocada (se declara de nuevo) y no uno vigente", async () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [exc("recibe", { revocada: true }), exc("entrega")],
      }),
    });
    await fireEvent.click(botonDeclarar());
    const rol = screen.getByLabelText(/parte que no puede firmar/i);
    const opciones = Array.from(rol.querySelectorAll("option")).map((o) => o.textContent);
    expect(opciones.join("|")).toMatch(/Receptor/);
    expect(opciones.join("|")).not.toMatch(/Remitente/);
  });
});

describe("ExcepcionesFirma — declarar", () => {
  it("no se puede enviar sin la confirmación explícita", async () => {
    sesion();
    montar();
    await llenarYConfirmar();
    expect(
      screen.getAllByRole("button", { name: /declarar firma inobtenible/i }).at(-1),
    ).toBeDisabled();
    await confirmar();
    expect(
      screen.getAllByRole("button", { name: /declarar firma inobtenible/i }).at(-1),
    ).not.toBeDisabled();
  });

  it("un fundamento de menos de 20 caracteres no habilita el envío", async () => {
    sesion();
    montar();
    await llenarYConfirmar();
    await fireEvent.input(screen.getByLabelText(/^fundamento/i), { target: { value: "corto" } });
    await confirmar();
    expect(
      screen.getAllByRole("button", { name: /declarar firma inobtenible/i }).at(-1),
    ).toBeDisabled();
  });

  it("envía exactamente los campos del contrato, con radicado y anexo ELEGIDOS", async () => {
    sesion();
    respuestas({
      "/api/anexos/": { body: [{ file_id: ANEXO, filename: "defuncion.pdf" }] },
      "/firma-inobtenible": { status: 201, body: { id: "x" } },
    });
    const { component } = montar();
    const cambio = vi.fn();
    component.$on("changed", cambio);
    await llenarYConfirmar({ anexo: true });
    await confirmar();
    await enviar();
    await waitFor(() => expect(cambio).toHaveBeenCalled());
    const llamada = fetchMock.mock.calls.find(([u]) =>
      String(u).endsWith("/acta/firma-inobtenible"),
    );
    expect(llamada[0]).toBe("/transferencias/api/t-1/acta/firma-inobtenible");
    expect(JSON.parse(llamada[1].body)).toEqual({
      rol: "recibe",
      motivo: "fallecimiento",
      fundamento: "El receptor falleció el 3 de octubre de 2026.",
      referencia_radicado_id: RADICADOS[0].radicado_id,
      referencia_anexo_id: ANEXO,
    });
  });

  it("el TOTP se ofrece siempre, opcional hasta que el servidor lo exige", async () => {
    sesion();
    montar();
    await fireEvent.click(botonDeclarar());
    expect(
      screen.getByLabelText(
        /código de verificación \(app autenticadora\), obligatorio si tiene certificado/i,
      ),
    ).toBeTruthy();
  });

  it("sin anexo elegido no se habilita el envío (obligatorio desde FIRMA-2)", async () => {
    sesion();
    montar();
    await llenarYConfirmar({ anexo: false });
    await confirmar();
    expect(
      screen.getAllByRole("button", { name: /declarar firma inobtenible/i }).at(-1),
    ).toBeDisabled();
  });

  it("el TOTP escrito viaja en la petición", async () => {
    sesion();
    respuestas({
      "/api/anexos/": { body: [{ file_id: ANEXO, filename: "defuncion.pdf" }] },
      "/acta/firma-inobtenible": { status: 201, body: {} },
    });
    montar();
    await llenarYConfirmar({ totp: "123456" });
    await confirmar();
    await enviar();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.find(([u]) => String(u).endsWith("/acta/firma-inobtenible")),
      ).toBeTruthy(),
    );
    const llamada = fetchMock.mock.calls.find(([u]) =>
      String(u).endsWith("/acta/firma-inobtenible"),
    );
    expect(JSON.parse(llamada[1].body).totp_code).toBe("123456");
  });

  it("si el servidor exige la firma personal (422), el TOTP pasa a obligatorio y se explica en español", async () => {
    sesion();
    respuestas({
      "/firma-inobtenible": {
        status: 422,
        body: { detail: { error: "firma_personal_declarante_requerida", detail: "x" } },
      },
    });
    montar();
    await llenarYConfirmar();
    await confirmar();
    await enviar();
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(/código de verificación/i),
    );
    expect(
      screen.getByLabelText(/^código de verificación \(app autenticadora\)(?!,)/i),
    ).toBeTruthy();
  });

  it.each([
    [403, "declarante_es_parte_de_la_transferencia", /no puede declarar ni revocar/],
    [409, "parte_ya_firmo", /ya firmó/],
    [503, "sello_institucional_no_disponible", /sello institucional no está disponible/],
    [409, "revocacion_sin_proteger", /aún no está sellada ni protegida/],
    [409, "excepcion_vigente", /ya tiene una excepción vigente/],
    [422, "declarante_sin_certificado", /certificado de firma activo/],
    [422, "soporte_sin_huella", /no tiene huella/],
    [503, "storage_no_disponible", /almacén de documentos no responde/],
    [401, "totp_incorrecto", /código de verificación no es correcto/],
  ])("%s %s se muestra en español y el modal sigue abierto", async (status, error, patron) => {
    sesion();
    respuestas({
      "/firma-inobtenible": { status, body: { detail: { error, detail: "english" } } },
    });
    montar();
    await llenarYConfirmar();
    await confirmar();
    await enviar();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(patron));
    expect(screen.getByLabelText(/parte que no puede firmar/i)).toBeTruthy();
  });
});

describe("ExcepcionesFirma — lista y revocación", () => {
  it("distingue la vigente de la revocada y no ofrece revocar la ya revocada", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [
          exc("entrega"),
          exc("recibe", { revocada: true, revocada_en: "2026-10-09T12:00:00Z" }),
        ],
      }),
    });
    expect(screen.getByText("Vigente")).toBeTruthy();
    expect(screen.getByText(/Revocada: ya no cubre/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /revocar excepción/i })).toHaveLength(1);
  });

  it("sin fundamento del servidor muestra la huella y NO inventa el texto", () => {
    sesion(EXTERNO, { USUA_PERM_ADMIN: 1, PERM_FIRMA: 2 });
    montar({ verificacion: verif({ excepciones: [exc("entrega")] }) });
    expect(screen.getByText(/Reservado: solo lo ve un administrador de nivel 3/)).toBeTruthy();
    expect(screen.getByText("ab".repeat(32))).toBeTruthy();
    expect(screen.queryByRole("button", { name: /verificar fundamento/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /revocar excepción/i })).toBeNull();
  });

  it("el fundamento se carga bajo demanda, una sola vez, y se descarta al ocultarlo", async () => {
    sesion();
    respuestas({
      "/fundamento?acto=constancia": { body: { fundamento: "Falleció el titular del cargo." } },
    });
    montar({ verificacion: verif({ excepciones: [exc("entrega")] }) });
    expect(screen.queryByText("Falleció el titular del cargo.")).toBeNull();
    await fireEvent.click(screen.getByRole("button", { name: /^mostrar fundamento/i }));
    await waitFor(() => expect(screen.getByText("Falleció el titular del cargo.")).toBeTruthy());
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/fundamento?"))).toHaveLength(
      1,
    );
    await fireEvent.click(screen.getByRole("button", { name: /^ocultar fundamento/i }));
    expect(screen.queryByText("Falleció el titular del cargo.")).toBeNull();
  });

  it("sin administración 3 no hay botón de mostrar fundamento", () => {
    sesion(EXTERNO, { USUA_PERM_ADMIN: 2, PERM_FIRMA: 2 });
    montar({ verificacion: verif({ excepciones: [exc("entrega")] }) });
    expect(screen.queryByRole("button", { name: /mostrar fundamento/i })).toBeNull();
  });

  it("una constancia sin firma se marca como no válida", () => {
    sesion();
    montar({ verificacion: verif({ excepciones: [exc("entrega", { firmada: false })] }) });
    expect(screen.getByText(/Constancia sin firma: no se da por válida/)).toBeTruthy();
  });

  it("quien es parte no ve revocar", () => {
    sesion("u-env");
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    expect(screen.queryByRole("button", { name: /revocar excepción/i })).toBeNull();
  });

  it("revocar pide fundamento de 20+ caracteres y confirmación, y envía rol y fundamento", async () => {
    sesion();
    respuestas({ "/revocar": { status: 201, body: {} } });
    const { component } = montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    const cambio = vi.fn();
    component.$on("changed", cambio);
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    const boton = () => screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1);
    expect(boton()).toBeDisabled();
    await fireEvent.change(screen.getByLabelText(/causa de la revocación/i), {
      target: { value: "error_de_declaracion" },
    });
    await fireEvent.input(screen.getByLabelText(/fundamento de la revocación/i), {
      target: { value: "Se declaró por error: el receptor sí puede firmar." },
    });
    expect(boton()).toBeDisabled();
    await fireEvent.click(screen.getByLabelText(/confirmo que quiero revocar/i));
    await fireEvent.click(boton());
    await waitFor(() => expect(cambio).toHaveBeenCalled());
    const llamada = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/revocar"));
    expect(llamada[0]).toBe("/transferencias/api/t-1/acta/firma-inobtenible/revocar");
    expect(JSON.parse(llamada[1].body)).toEqual({
      rol: "recibe",
      causa: "error_de_declaracion",
      fundamento: "Se declaró por error: el receptor sí puede firmar.",
    });
  });

  it("409 excepcion_ya_revocada se explica en español", async () => {
    sesion();
    respuestas({
      "/revocar": {
        status: 409,
        body: { detail: { error: "excepcion_ya_revocada", detail: "x" } },
      },
    });
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    await fireEvent.change(screen.getByLabelText(/causa de la revocación/i), {
      target: { value: "error_de_declaracion" },
    });
    await fireEvent.input(screen.getByLabelText(/fundamento de la revocación/i), {
      target: { value: "Se declaró por error: el receptor sí puede firmar." },
    });
    await fireEvent.click(screen.getByLabelText(/confirmo que quiero revocar/i));
    await fireEvent.click(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/ya fue revocada/));
  });
});

describe("ExcepcionesFirma — FIRMA-2: cadena, soporte y verificación", () => {
  it("muestra el ordinal de una re-declaración, el radicado, la huella del anexo y las personas", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [
          exc("recibe", {
            ordinal: 2,
            sustituye_a: "x-prev",
            referencia_radicado_numero: "2026-X-E-000001",
            referencia_anexo_sha256: "cd".repeat(32),
            declarante_nombre: "Ana Ruiz",
            declarante_cargo: "Jefe de archivo",
            declarante_fuente: "certificado",
            parte_fuente: "no_resuelta",
          }),
        ],
      }),
    });
    expect(screen.getByTestId("exc-ordinal").textContent).toMatch(/n\.º 2/);
    expect(screen.getByText("2026-X-E-000001")).toBeTruthy();
    expect(screen.getByText("cd".repeat(32))).toBeTruthy();
    expect(screen.getByText(/Ana Ruiz, Jefe de archivo \(certificado\)/)).toBeTruthy();
    expect(screen.getByText(/No resuelta al declarar/)).toBeTruthy();
  });

  it("referencias en null (lector sin nivel) dicen «soporte no visible con su nivel»", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [exc("recibe", { referencia_documental: null, referencia_radicado_id: null })],
      }),
    });
    expect(screen.getByTestId("soporte-no-visible").textContent).toMatch(
      /soporte no visible con su nivel/i,
    );
  });

  it("muestra el estado WORM de la revocación", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [exc("recibe", { revocada: true, revocacion_worm_protegido: false })],
      }),
    });
    expect(screen.getByTestId("revocacion-worm").textContent).toMatch(/Protección pendiente/);
  });

  it("el botón de verificar el fundamento es para administración 3 y muestra las tres copias", async () => {
    sesion();
    respuestas({
      "/fundamento/verificacion": {
        body: { verificable: true, columna: true, xml: true, asiento: null, coincide: false },
      },
    });
    montar({ verificacion: verif({ excepciones: [exc("entrega")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /verificar fundamento/i }));
    await waitFor(() => expect(screen.getByTestId("verif-resultado")).toBeTruthy());
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/transferencias/api/t-1/acta/firma-inobtenible/x-entrega/fundamento/verificacion",
    );
    expect(screen.getByText(/NO coincide/)).toBeTruthy();
    expect(screen.getByText(/Asiento de auditoría: no comprobada/)).toBeTruthy();
  });

  it("un 403 de la verificación se muestra sin afirmar coincidencia", async () => {
    sesion();
    respuestas({
      "/fundamento/verificacion": { status: 403, body: { error: "forbidden", detail: "no" } },
    });
    montar({ verificacion: verif({ excepciones: [exc("entrega")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /verificar fundamento/i }));
    await waitFor(() => expect(document.body.textContent).toMatch(/error 403|no/i));
    expect(screen.queryByTestId("verif-resultado")).toBeNull();
  });

  it("revocar envía el TOTP si se escribió y, si el servidor lo exige (422), pasa a obligatorio", async () => {
    sesion();
    respuestas({
      "/revocar": {
        status: 422,
        body: { detail: { error: "firma_personal_revocante_requerida", detail: "x" } },
      },
    });
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    await fireEvent.change(screen.getByLabelText(/causa de la revocación/i), {
      target: { value: "error_de_declaracion" },
    });
    await fireEvent.input(screen.getByLabelText(/fundamento de la revocación/i), {
      target: { value: "Se declaró por error: el receptor sí puede firmar." },
    });
    await fireEvent.click(screen.getByLabelText(/confirmo que quiero revocar/i));
    await fireEvent.click(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/firmar también/i));
    expect(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1)).toBeDisabled();
    await fireEvent.input(
      screen.getByLabelText(/^código de verificación \(app autenticadora\)(?!,)/i),
      {
        target: { value: "654321" },
      },
    );
    expect(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1)).not.toBeDisabled();
    await fireEvent.click(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
    expect(JSON.parse(fetchMock.mock.calls.at(-1)[1].body).totp_code).toBe("654321");
  });
});

describe("ExcepcionesFirma — contrato final FIRMA-2", () => {
  it("revocar no se habilita sin causa", async () => {
    sesion();
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    await fireEvent.input(screen.getByLabelText(/fundamento de la revocación/i), {
      target: { value: "Se declaró por error: el receptor sí puede firmar." },
    });
    await fireEvent.click(screen.getByLabelText(/confirmo que quiero revocar/i));
    expect(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1)).toBeDisabled();
  });

  it("muestra la huella con su algoritmo en español, NUNCA la sal, y la causa", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [
          exc("recibe", {
            fundamento_compromiso_algoritmo: "SHA-256-salted",
            fundamento_sal: "ef".repeat(32),
            revocada: true,
            revocacion_causa: "parte_puede_firmar",
          }),
        ],
      }),
    });
    expect(screen.getByText(/huella SHA-256 con sal propia/)).toBeTruthy();
    expect(screen.getByText("Huella del fundamento")).toBeTruthy();
    expect(document.body.textContent).not.toContain("ef".repeat(32));
    expect(screen.getByText("La parte sí puede firmar")).toBeTruthy();
  });

  it("enlace roto se señala; WORM retenido hasta se muestra", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [
          exc("recibe", {
            enlace_valido: false,
            enlace_motivo: "ordinal_no_consecutivo, sustituida_no_revocada",
            worm_retain_until: "2036-10-09T00:00:00Z",
          }),
        ],
      }),
    });
    expect(screen.getByTestId("enlace-roto").textContent).toMatch(
      /la numeración no es consecutiva; la declaración que sustituye no está revocada/,
    );
    expect(screen.getByTestId("worm-retain")).toBeTruthy();
  });

  it("«Reintentar protección» solo con WORM no confirmado y administración 3; llama una sola vez", async () => {
    sesion();
    respuestas({
      "/worm/reintentar": { body: { repuestos: 1, artefactos: { sello: "protegido" } } },
    });
    montar({ verificacion: verif({ excepciones: [exc("recibe", { worm_protegido: false })] }) });
    await fireEvent.click(
      screen.getByRole("button", { name: /reintentar protección de la constancia/i }),
    );
    await waitFor(() => expect(screen.getByTestId("reintento-resultado")).toBeTruthy());
    expect(screen.getByTestId("reintento-resultado").textContent).toMatch(/confirmada/);
    expect(
      fetchMock.mock.calls.filter(([u]) => String(u).includes("/worm/reintentar")),
    ).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/x-recibe\/worm\/reintentar$/);
  });

  it("protegido, o sin administración 3, no hay botón de reintento", () => {
    sesion();
    const { unmount } = montar({
      verificacion: verif({ excepciones: [exc("recibe", { worm_protegido: true })] }),
    });
    expect(screen.queryByRole("button", { name: /reintentar protección/i })).toBeNull();
    unmount();
    clearSession();
    sesion(EXTERNO, { USUA_PERM_ADMIN: 2, PERM_FIRMA: 2 });
    montar({ verificacion: verif({ excepciones: [exc("recibe", { worm_protegido: false })] }) });
    expect(screen.queryByRole("button", { name: /reintentar protección/i })).toBeNull();
  });

  it("la protección de la revocación usa su propia ruta", async () => {
    sesion();
    respuestas({
      "/revocacion/worm/reintentar": {
        body: { repuestos: 1, artefactos: { sello: "agotado" } },
      },
    });
    montar({
      verificacion: verif({
        excepciones: [exc("recibe", { revocada: true, revocacion_worm_protegido: false })],
      }),
    });
    await fireEvent.click(
      screen.getByRole("button", { name: /reintentar protección de la revocación/i }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("reintento-resultado").textContent).toMatch(/Quedan sin proteger/),
    );
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/x-recibe\/revocacion\/worm\/reintentar$/);
  });
});

describe("ExcepcionesFirma — sobre firmado", () => {
  it("«Descargar sobre» pide el XML una vez, lo descarga y no lo pinta", async () => {
    sesion();
    respuestas({ "/sobre": { body: { xml_firmado: "<secreto/>" } } });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    montar({ verificacion: verif({ excepciones: [exc("recibe", { sobre: true })] }) });
    await fireEvent.click(screen.getByRole("button", { name: /^descargar sobre/i }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/sobre"))).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/x-recibe\/sobre\?acto=constancia$/);
    expect(document.body.textContent).not.toMatch(/secreto/);
  });

  it("sin sobre o sin administración 3 no hay botón; un 404 se explica", async () => {
    sesion();
    const { unmount } = montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    expect(screen.queryByRole("button", { name: /descargar sobre/i })).toBeNull();
    unmount();
    respuestas({ "/sobre": { status: 404, body: { detail: { error: "sobre_not_found" } } } });
    montar({ verificacion: verif({ excepciones: [exc("recibe", { sobre: true })] }) });
    await fireEvent.click(screen.getByRole("button", { name: /^descargar sobre/i }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/no tiene sobre/));
  });

  it("un sobre sin Object-Lock habilita el reintento aunque el sello esté protegido", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [exc("recibe", { worm_protegido: true, worm_sobre_protegido: false })],
      }),
    });
    expect(
      screen.getByRole("button", { name: /reintentar protección de la constancia/i }),
    ).toBeTruthy();
  });

  it("la verificación del fundamento muestra el sobre como cuarta copia", async () => {
    sesion();
    respuestas({
      "/fundamento/verificacion": {
        body: {
          verificable: true,
          columna: true,
          xml: true,
          asiento: true,
          sobre: null,
          coincide: false,
        },
      },
    });
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /verificar fundamento/i }));
    await waitFor(() => expect(screen.getByText(/Sobre firmado: no comprobada/)).toBeTruthy());
  });
});

describe("ExcepcionesFirma — aviso del fundamento", () => {
  const AVISO = /indique la causal y el soporte, no el diagnóstico/i;

  it("declarar muestra el aviso y lo asocia al campo por aria-describedby", async () => {
    sesion();
    montar();
    await fireEvent.click(botonDeclarar());
    const campo = screen.getByLabelText(/^fundamento/i);
    const aviso = screen.getByText(AVISO);
    expect(aviso.textContent).toMatch(/no se puede borrar/);
    expect(campo.getAttribute("aria-describedby")).toContain(aviso.id);
  });

  it("revocar muestra el mismo aviso asociado al campo", async () => {
    sesion();
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    const campo = screen.getByLabelText(/fundamento de la revocación/i);
    const aviso = screen.getByText(AVISO);
    expect(campo.getAttribute("aria-describedby")).toContain(aviso.id);
  });
});

describe("ExcepcionesFirma — foco, jerga y accesibilidad", () => {
  it("tras revocar, el foco va al título de la sección", async () => {
    sesion();
    respuestas({ "/revocar": { status: 201, body: {} } });
    montar({ verificacion: verif({ excepciones: [exc("recibe")] }) });
    await fireEvent.click(screen.getByRole("button", { name: /revocar excepción/i }));
    await fireEvent.change(screen.getByLabelText(/causa de la revocación/i), {
      target: { value: "otro" },
    });
    await fireEvent.input(screen.getByLabelText(/fundamento de la revocación/i), {
      target: { value: "Se declaró por error: el receptor sí puede firmar." },
    });
    await fireEvent.click(screen.getByLabelText(/confirmo que quiero revocar/i));
    await fireEvent.click(screen.getAllByRole("button", { name: /revocar excepción/i }).at(-1));
    await waitFor(() => expect(document.activeElement?.id).toBe("exc-titulo"));
    expect(document.getElementById("exc-titulo").getAttribute("tabindex")).toBe("-1");
  });

  it("tras un reintento exitoso avisa del cambio para recargar el acta", async () => {
    sesion();
    respuestas({
      "/worm/reintentar": { body: { repuestos: 1, artefactos: { sobre: "protegido" } } },
    });
    const { component } = montar({
      verificacion: verif({ excepciones: [exc("recibe", { worm_protegido: false })] }),
    });
    const cambio = vi.fn();
    component.$on("changed", cambio);
    await fireEvent.click(
      screen.getByRole("button", { name: /reintentar protección de la constancia/i }),
    );
    await waitFor(() => expect(cambio).toHaveBeenCalledTimes(1));
  });

  it("el reintento fallido explica el estado en español y no dice «sobre (agotado)»", async () => {
    sesion();
    respuestas({
      "/worm/reintentar": { body: { repuestos: 1, artefactos: { sobre: "agotado" } } },
    });
    montar({ verificacion: verif({ excepciones: [exc("recibe", { worm_protegido: false })] }) });
    await fireEvent.click(
      screen.getByRole("button", { name: /reintentar protección de la constancia/i }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("reintento-resultado").textContent).toMatch(
        /sobre firmado: se agotaron los reintentos/,
      ),
    );
  });

  it("las advertencias usan el diccionario y los botones repetidos nombran rol y declaración", () => {
    sesion();
    montar({
      verificacion: verif({
        excepciones: [
          exc("recibe", { advertencias: ["tsa_sin_esscertid_anterior_al_corte"], ordinal: 2 }),
        ],
      }),
    });
    expect(document.body.textContent).toMatch(/no identifica el certificado de la autoridad/);
    expect(
      screen.getByRole("button", {
        name: /revocar excepción — receptor\(a\) \(recibe\), declaración 2/i,
      }),
    ).toBeTruthy();
  });
});
