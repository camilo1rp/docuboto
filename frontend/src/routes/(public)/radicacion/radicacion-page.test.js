// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


/**
 * radicacion-page.test.js — la ventanilla en línea (AREA-02-07 / AREA-09-08).
 *
 * - Sin autorizar el tratamiento de datos (Ley 1581/2012) NO se envía nada.
 * - El honeypot existe, está fuera del orden de tabulación y del árbol de accesibilidad,
 *   y viaja vacío en una radicación legítima.
 * - Cada envío lleva `Idempotency-Key`; un reintento IDÉNTICO reutiliza la clave, y un
 *   envío con otro contenido genera otra (la misma clave con otro contenido sería 409).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import Pagina from "./+page.svelte";

const datos = {
  tipos: [{ code: "DP-GEN", nombre: "Derecho de petición" }],
  habilitada: true,
  politica: { version: "2026-03", url: "https://entidad.example/politica" },
};

function llenar({ asunto = "Solicitud" } = {}) {
  fireEvent.input(screen.getByLabelText(/Nombre/i, { selector: "#nombre" }), {
    target: { value: "Ana Ruiz" },
  });
  fireEvent.input(screen.getByLabelText("Asunto"), { target: { value: asunto } });
  fireEvent.input(screen.getByLabelText("Descripción"), { target: { value: "Texto" } });
}

afterEach(() => vi.restoreAllMocks());

describe("/radicacion — ventanilla en línea", () => {
  it("sin autorizar el tratamiento de datos no envía nada", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    render(Pagina, { data: datos });
    llenar();
    await fireEvent.submit(document.querySelector("form"));
    expect(f).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(/autorizar el tratamiento/i);
  });

  it("el honeypot está oculto a personas y a lectores de pantalla", () => {
    render(Pagina, { data: datos });
    const trampa = document.querySelector("#sitio_web");
    expect(trampa).not.toBeNull();
    expect(trampa.getAttribute("tabindex")).toBe("-1");
    expect(trampa.getAttribute("autocomplete")).toBe("off");
    expect(trampa.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it("enlaza la política de la entidad cuando está configurada", () => {
    render(Pagina, { data: datos });
    const enlace = screen.getByRole("link", { name: /política de tratamiento/i });
    expect(enlace.getAttribute("href")).toBe("https://entidad.example/politica");
  });

  it("envía la autorización con su versión, el honeypot vacío y la Idempotency-Key", async () => {
    const f = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ tracking_number: "2026-DEMO-E-000001", verification_code: "x" }),
    });
    vi.stubGlobal("fetch", f);
    render(Pagina, { data: datos });
    llenar();
    await fireEvent.click(screen.getByLabelText(/Autorizo a la entidad/i));
    await fireEvent.submit(document.querySelector("form"));
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));

    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/radicacion/api");
    expect(init.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    const enviado = JSON.parse(init.body.get("datos"));
    expect(enviado.acepta_tratamiento).toBe(true);
    expect(enviado.politica_version).toBe("2026-03");
    expect(enviado.sitio_web).toBe("");
  });

  it("un reintento idéntico reutiliza la clave; si cambia el contenido, genera otra", async () => {
    const f = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: "caído" }) });
    vi.stubGlobal("fetch", f);
    render(Pagina, { data: datos });
    llenar();
    await fireEvent.click(screen.getByLabelText(/Autorizo a la entidad/i));
    const form = document.querySelector("form");

    await fireEvent.submit(form);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    await fireEvent.submit(form); // reintento idéntico
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
    const k1 = f.mock.calls[0][1].headers["Idempotency-Key"];
    expect(f.mock.calls[1][1].headers["Idempotency-Key"]).toBe(k1);

    llenar({ asunto: "Otro asunto distinto" }); // edita el contenido
    await fireEvent.submit(form);
    await waitFor(() => expect(f).toHaveBeenCalledTimes(3));
    expect(f.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe(k1);
  });
});
