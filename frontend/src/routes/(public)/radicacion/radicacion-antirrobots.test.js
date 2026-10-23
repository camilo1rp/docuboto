// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicacion-antirrobots.test.js — el widget ALTCHA en la ventanilla (C16).
 *
 * - El worker sale del propio origen y se fija DESPUÉS de importar el widget
 *   (que instala el suyo, basado en `blob:`, y la CSP lo bloquearía).
 * - El payload viaja en `datos.altcha`; el reto NO entra en la huella de la clave
 *   de idempotencia.
 * - 409/422 `antirrobots_*` reinician el widget y conservan lo escrito; el 503
 *   nombra los canales alternos.
 */
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/svelte";

const { WorkerPropio } = vi.hoisted(() => ({ WorkerPropio: class WorkerPropio {} }));

vi.mock("altcha/altcha.css", () => ({}));
vi.mock("altcha/external", () => {
  // Imita lo que hace el real al importarse: pisa `altchaCreateWorker` con el de blob.
  /** @type {any} */ (globalThis).altchaCreateWorker = () => "worker-de-blob";
  return {};
});
vi.mock("altcha/i18n/es-419", () => ({}));
vi.mock("altcha/worker?worker", () => ({ default: WorkerPropio }));

import Pagina from "./+page.svelte";
import { PAYLOAD, emitir, pintar } from "./altchaTestUtil.js";

const datos = {
  tipos: [{ code: "DP-GEN", nombre: "Derecho de petición" }],
  habilitada: true,
  politica: { publicada: true, verificable: true, version: "2026-03", url: "" },
};

function llenar(asunto = "Solicitud") {
  fireEvent.input(screen.getByLabelText(/Nombre/i, { selector: "#nombre" }), {
    target: { value: "Ana Ruiz" },
  });
  fireEvent.input(screen.getByLabelText("Asunto"), { target: { value: asunto } });
  fireEvent.input(screen.getByLabelText("Descripción"), { target: { value: "Texto" } });
}

async function listoParaEnviar() {
  const widget = await pintar(Pagina, datos);
  llenar();
  await fireEvent.click(screen.getByLabelText(/Autorizo a la entidad/i));
  return widget;
}

const enviar = () => fireEvent.submit(document.querySelector("form"));

/** @param {number} status @param {string} error @param {string} detail */
const rechazo = (status, error, detail = "x") => ({
  ok: false,
  status,
  json: async () => ({ error, detail, status }),
});

beforeEach(() => {
  /** @type {any} */ (globalThis).altchaCreateWorker = undefined;
});
afterEach(() => vi.restoreAllMocks());

describe("/radicacion — ALTCHA: estructura y accesibilidad", () => {
  it("agrupa el widget en un fieldset con leyenda visible y texto de ayuda", async () => {
    const widget = await pintar(Pagina, datos);
    const grupo = widget.closest("fieldset");
    expect(grupo).not.toBeNull();
    expect(grupo.querySelector("legend")).toHaveTextContent("Verificación automática");
    expect(grupo).toHaveTextContent(/No tiene que resolver nada/i);
    expect(widget.getAttribute("challengeurl")).toBe("/radicacion/api/reto");
    expect(widget.getAttribute("auto")).toBe("onfocus");
    expect(grupo.getAttribute("aria-describedby")).toBe("altcha-ayuda");
    // El estado se anuncia en una región viva.
    expect(grupo.querySelector('[role="status"]')).toHaveTextContent(/Se hará al enviar/i);
  });

  it("el worker es el del propio origen y se fija DESPUÉS de importar el widget", async () => {
    await pintar(Pagina, datos);
    const fabrica = /** @type {any} */ (globalThis).altchaCreateWorker;
    // Control positivo: si el orden fuese el contrario, quedaría el de blob.
    expect(fabrica()).toBeInstanceOf(WorkerPropio);
    expect(fabrica()).not.toBe("worker-de-blob");
  });

  it("incluye el aviso de canales alternos para quien no tiene JavaScript", () => {
    // Svelte no pinta <noscript> al hidratar en el cliente (solo en el HTML del SSR), así
    // que jsdom no lo ve: se afirma sobre la fuente de la página, que es lo que se sirve.
    const fuente = readFileSync("src/routes/(public)/radicacion/+page.svelte", "utf8");
    const bloque = fuente.match(/<noscript>([\s\S]*?)<\/noscript>/);
    expect(bloque).not.toBeNull();
    expect(bloque[1]).toMatch(/necesita JavaScript/i);
    expect(bloque[1]).toMatch(/CANALES_ALTERNOS/);
    expect(fuente).toMatch(/de forma presencial en la ventanilla de la entidad/);
    expect(fuente).toMatch(/correo de atención de la entidad/);
  });

  it("si el widget informa `error`, ofrece los canales alternos", async () => {
    const widget = await pintar(Pagina, datos);
    expect(document.querySelector("#altcha-alternos")).toBeNull();
    emitir(widget, "error", "");
    const aviso = await screen.findByText(/no funciona en su navegador/i);
    expect(aviso).toHaveTextContent(/presencial/i);
    expect(aviso).toHaveTextContent(/correo/i);
    // Y se recupera si luego verifica.
    emitir(widget, "verified");
    await waitFor(() => expect(document.querySelector("#altcha-alternos")).toBeNull());
  });
});

describe("/radicacion — ALTCHA: envío", () => {
  it("con el reto ya resuelto, lo mete en `datos.altcha` sin pedir otra verificación", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ tracking_number: "x" }) });
    vi.stubGlobal("fetch", f);
    const widget = await listoParaEnviar();
    emitir(widget);
    await enviar();
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    expect(widget.verify).not.toHaveBeenCalled();
    expect(JSON.parse(f.mock.calls[0][1].body.get("datos")).altcha).toBe(PAYLOAD);
  });

  it("sin reto resuelto, lanza `verify()` y espera a `verified` antes de enviar", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ tracking_number: "x" }) });
    vi.stubGlobal("fetch", f);
    const widget = await listoParaEnviar();
    widget.verify = vi.fn(() => setTimeout(() => emitir(widget, "verified", PAYLOAD), 0));
    await enviar();
    expect(widget.verify).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    expect(JSON.parse(f.mock.calls[0][1].body.get("datos")).altcha).toBe(PAYLOAD);
  });

  it("si la verificación falla, no envía nada y nombra los canales alternos", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const widget = await listoParaEnviar();
    widget.verify = vi.fn(() => setTimeout(() => emitir(widget, "error", ""), 0));
    await enviar();
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/No se pudo completar la verificación automática/i);
    expect(alerta).toHaveTextContent(/presencial/i);
    expect(f).not.toHaveBeenCalled();
    expect(widget.reset).toHaveBeenCalled();
  });

  it("el reintento tras un fallo de red conserva clave y reto; el reto no entra en la huella", async () => {
    const f = vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => ({}) });
    vi.stubGlobal("fetch", f);
    const widget = await listoParaEnviar();
    emitir(widget, "verified", PAYLOAD);
    await enviar();
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    // Un 502 no quema el reto: no se reinicia el widget.
    expect(widget.reset).not.toHaveBeenCalled();
    // Control positivo: con OTRO reto y el mismo contenido, la clave es la misma.
    emitir(widget, "verified", PAYLOAD + "AAAA");
    await enviar();
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
    expect(f.mock.calls[1][1].headers["Idempotency-Key"]).toBe(
      f.mock.calls[0][1].headers["Idempotency-Key"],
    );
    expect(JSON.parse(f.mock.calls[1][1].body.get("datos")).altcha).not.toBe(
      JSON.parse(f.mock.calls[0][1].body.get("datos")).altcha,
    );
  });
});

describe("/radicacion — ALTCHA: errores del servidor", () => {
  it.each([
    [409, "antirrobots_usado", /ya se utilizó/i],
    [422, "antirrobots_vencido", /venció/i],
    [422, "antirrobots_invalido", /No se pudo completar la verificación automática/i],
  ])(
    "%i %s: mensaje en español, reinicia el widget, enfoca el grupo y conserva lo escrito",
    async (status, codigo, mensaje) => {
      const f = vi.fn().mockResolvedValue(rechazo(status, codigo));
      vi.stubGlobal("fetch", f);
      const widget = await listoParaEnviar();
      emitir(widget);
      await enviar();
      const alerta = await screen.findByRole("alert");
      expect(alerta).toHaveTextContent(mensaje);
      expect(widget.reset).toHaveBeenCalledTimes(1);
      expect(document.activeElement).toBe(document.querySelector("#grupo-altcha"));
      expect(screen.getByLabelText("Asunto")).toHaveValue("Solicitud");
      expect(screen.getByLabelText(/Autorizo a la entidad/i).checked).toBe(true);
      // El reto reiniciado ya no se reenvía: el siguiente envío vuelve a verificar.
      f.mockResolvedValue({ ok: true, json: async () => ({ tracking_number: "x" }) });
      widget.verify = vi.fn(() => setTimeout(() => emitir(widget, "verified", PAYLOAD + "ZZ"), 0));
      await enviar();
      await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
      expect(widget.verify).toHaveBeenCalledTimes(1);
    },
  );

  it("503 antirrobots_no_disponible: nombra presencial y correo y NO reinicia el widget", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(rechazo(503, "antirrobots_no_disponible")));
    const widget = await listoParaEnviar();
    emitir(widget);
    await enviar();
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/presencial/i);
    expect(alerta).toHaveTextContent(/correo/i);
    expect(widget.reset).not.toHaveBeenCalled();
  });

  it("un 409 de política o un 422 de anexo también queman el reto", async () => {
    const f = vi.fn().mockResolvedValue(rechazo(422, "anexo_invalido", "El anexo no es un PDF."));
    vi.stubGlobal("fetch", f);
    const widget = await listoParaEnviar();
    emitir(widget);
    await enviar();
    expect(await screen.findByRole("alert")).toHaveTextContent(/anexo no es un PDF/i);
    expect(widget.reset).toHaveBeenCalledTimes(1);
  });
});
