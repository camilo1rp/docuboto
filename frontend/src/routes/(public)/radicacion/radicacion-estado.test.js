// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicacion-estado.test.js — la verificación antirrobots NO operativa se avisa ANTES
 * de escribir, con el contacto concreto que la entidad declaró (C-3, auditoría).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/svelte";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));
vi.mock("altcha/altcha.css", () => ({}));
vi.mock("altcha/external", () => ({}));
vi.mock("altcha/i18n/es-419", () => ({}));
vi.mock("altcha/worker?worker", () => ({ default: class {} }));

const { load } = await import("./+page.server.js");
const { POST } = await import("./api/+server.js");
const { GET: GET_RETO } = await import("./api/reto/+server.js");
const { default: Pagina } = await import("./+page.svelte");
const { emitir, pintar } = await import("./altchaTestUtil.js");

const CONTACTO = {
  nombre: "Oficina de Correspondencia",
  direccion: "Calle 1 # 2-3",
  telefono: "6015550000",
  email: "radicacion@entidad.example",
  web: "https://entidad.example",
};
const ok = (cuerpo) => ({ ok: true, status: 200, json: async () => cuerpo });
const base = {
  tipos: [{ code: "DP-GEN", nombre: "Derecho de petición" }],
  habilitada: true,
  politica: { publicada: true, verificable: true, version: "2026-03", url: "" },
};

afterEach(() => vi.restoreAllMocks());

describe("load — /ventanilla/estado", () => {
  const fetchCon = (estado) =>
    vi.fn(async (url) => {
      const u = String(url);
      if (u.endsWith("/estado")) return estado;
      if (u.endsWith("/politica")) return ok({ publicada: true, version: "v1" });
      return ok([{ code: "DP" }]);
    });

  it("toma antirrobots_disponible y el contacto declarado", async () => {
    const f = fetchCon(ok({ antirrobots_disponible: false, contacto: CONTACTO }));
    const r = await load({ fetch: f });
    expect(f.mock.calls.map((c) => String(c[0]))).toContain(
      "http://gw/api/v1/public/demo/ventanilla/estado",
    );
    expect(r.estado.antirrobotsDisponible).toBe(false);
    expect(r.estado.contacto).toEqual(CONTACTO);
  });

  it("campos no declarados quedan en null y un contacto vacío es null", async () => {
    const vacio = { nombre: null, direccion: null, telefono: null, email: null, web: null };
    const r1 = await load({
      fetch: fetchCon(ok({ antirrobots_disponible: true, contacto: vacio })),
    });
    expect(r1.estado).toEqual({ antirrobotsDisponible: true, contacto: null });
    const r2 = await load({
      fetch: fetchCon(ok({ antirrobots_disponible: true, contacto: { telefono: "123" } })),
    });
    expect(r2.estado.contacto).toEqual({
      nombre: null,
      direccion: null,
      telefono: "123",
      email: null,
      web: null,
    });
  });

  it("si /estado falla, es solo un aviso: se asume disponible y sin contacto", async () => {
    const r = await load({ fetch: fetchCon({ ok: false, status: 500, json: async () => ({}) }) });
    expect(r.estado).toEqual({ antirrobotsDisponible: true, contacto: null });
    expect(r.habilitada).toBe(true);
  });
});

describe("página — aviso anticipado", () => {
  it("antirrobots no disponible: avisa arriba con el contacto, y no deja enviar", async () => {
    render(Pagina, {
      data: { ...base, estado: { antirrobotsDisponible: false, contacto: CONTACTO } },
    });
    const aviso = await screen.findByText(/no está disponible en este momento/i);
    const caja = aviso.closest("#antirrobots-no-disponible");
    expect(caja).toHaveTextContent(/presencial/i);
    expect(caja).toHaveTextContent("Oficina de Correspondencia");
    expect(caja).toHaveTextContent("Calle 1 # 2-3");
    expect(caja).toHaveTextContent("6015550000");
    expect(caja.querySelector('a[href="mailto:radicacion@entidad.example"]')).not.toBeNull();
    expect(caja.querySelector('a[href="https://entidad.example"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: /Radicar documento/i })).toBeDisabled();
    // El aviso viene ANTES del primer campo.
    const primerCampo = document.querySelector("#tipo");
    expect(
      caja.compareDocumentPosition(primerCampo) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("sin contacto declarado, texto genérico y sin inventar datos ni horario", async () => {
    render(Pagina, { data: { ...base, estado: { antirrobotsDisponible: false, contacto: null } } });
    const caja = (await screen.findByText(/no está disponible en este momento/i)).closest("p");
    expect(caja).toHaveTextContent(/presencial/i);
    expect(caja).toHaveTextContent(/correo de atención de la entidad/i);
    expect(caja.querySelector('[data-testid="contacto-entidad"]')).toBeNull();
    expect(caja.textContent).not.toMatch(/horario/i);
  });

  it("control positivo: con antirrobots disponible no hay aviso y el botón está activo", async () => {
    render(Pagina, {
      data: { ...base, estado: { antirrobotsDisponible: true, contacto: CONTACTO } },
    });
    expect(document.querySelector("#antirrobots-no-disponible")).toBeNull();
    expect(screen.getByRole("button", { name: /Radicar documento/i })).not.toBeDisabled();
  });

  it("solo pinta los campos declarados del contacto", async () => {
    render(Pagina, {
      data: {
        ...base,
        estado: { antirrobotsDisponible: false, contacto: { telefono: "6015550000" } },
      },
    });
    const c = await screen.findByTestId("contacto-entidad");
    expect(c).toHaveTextContent("Teléfono: 6015550000");
    expect(c.textContent).not.toMatch(/Dirección|Correo|Sitio web/);
  });

  it("el contacto con esquema peligroso no genera enlace", async () => {
    render(Pagina, {
      data: {
        ...base,
        estado: {
          antirrobotsDisponible: false,
          contacto: { web: "javascript:alert(1)", nombre: "X" },
        },
      },
    });
    await screen.findByTestId("contacto-entidad");
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("el estado de error del widget muestra el contacto concreto", async () => {
    const w = await pintar(Pagina, {
      ...base,
      estado: { antirrobotsDisponible: true, contacto: CONTACTO },
    });
    emitir(w, "error", "");
    const aviso = await screen.findByText(/no funciona en su navegador/i);
    expect(aviso).toHaveTextContent("Calle 1 # 2-3");
  });

  it("un 503 antirrobots_no_disponible muestra el contacto que trae el error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          error: "antirrobots_no_disponible",
          detail: "x",
          contacto: { ...CONTACTO, nombre: "Contacto del 503" },
        }),
      }),
    );
    const w = await pintar(Pagina, {
      ...base,
      estado: { antirrobotsDisponible: true, contacto: null },
    });
    fireEvent.input(screen.getByLabelText(/Nombre/i, { selector: "#nombre" }), {
      target: { value: "Ana" },
    });
    fireEvent.input(screen.getByLabelText("Asunto"), { target: { value: "a" } });
    fireEvent.input(screen.getByLabelText("Descripción"), { target: { value: "b" } });
    await fireEvent.click(screen.getByLabelText(/Autorizo a la entidad/i));
    emitir(w);
    await fireEvent.submit(document.querySelector("form"));
    const aviso = await screen.findByText(/La verificación automática no está disponible:/);
    expect(aviso).toHaveTextContent("Contacto del 503");
  });
});

describe("proxies — el contacto del 503 pasa por allowlist", () => {
  it("POST /radicacion/api reenvía `contacto` (solo claves declaradas) en antirrobots_no_disponible", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({
        detail: {
          error: "antirrobots_no_disponible",
          detail: "x",
          status: 503,
          contacto: { ...CONTACTO, secreto: "no" },
        },
      }),
    });
    const res = await POST({ request: { formData: async () => new FormData() }, fetch });
    const cuerpo = await res.json();
    expect(res.status).toBe(503);
    expect(cuerpo.contacto).toEqual(CONTACTO);
    expect(cuerpo.contacto.secreto).toBeUndefined();
  });

  it("otros errores NO llevan contacto", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: { error: "antirrobots_usado", contacto: CONTACTO } }),
    });
    const res = await POST({ request: { formData: async () => new FormData() }, fetch });
    expect((await res.json()).contacto).toBeUndefined();
  });

  it("GET /radicacion/api/reto reenvía el contacto del 503", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: { error: "antirrobots_no_disponible", contacto: CONTACTO } }),
    });
    const cuerpo = await (await GET_RETO({ fetch })).json();
    expect(cuerpo.contacto).toEqual(CONTACTO);
  });
});
