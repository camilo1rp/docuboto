// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Comprobar una firma (ADR-054 §2, §4): carga SSR sin sesión y la pantalla.
 * La constancia simple no se pinta válida; «válida» nunca se lee como acreditada;
 * un 503 no es «firma inválida».
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { load } = await import("./[id]/+page.server.js");
const { load: loadForm } = await import("./+page.server.js");
const { default: Pagina } = await import("./[id]/+page.svelte");

const ID = "3f2c1a9e-4b7d-4e2a-9c1f-0a1b2c3d4e5f";
const resp = (status, cuerpo) => new Response(JSON.stringify(cuerpo), { status });

const ELECTRONICA = {
  firma_id: ID,
  kind: "electronic_signature",
  valida: true,
  firma_criptograficamente_valida: true,
  revocacion_status: "vigente",
  motivo: null,
  nivel: "XAdES-T",
  firmante: "Ana Pérez",
  firmado_en: "2026-11-07T17:00:00Z",
  sello_tiempo: "2026-11-07T17:00:03Z",
  huella_documento: "sha256:" + "a".repeat(64),
  acreditado: false,
  constancia: `/api/v1/signature/firmas/${ID}`,
};

describe("load de /firma/[id]", () => {
  it("pide la verificación pública del tenant, sin cabeceras de sesión", async () => {
    const f = vi.fn(async () => resp(200, ELECTRONICA));
    const r = await load({ params: { id: ID }, fetch: f });
    expect(f.mock.calls[0][0]).toBe(`http://gw/api/v1/public/signature/demo/${ID}`);
    expect(f.mock.calls[0][1]).toBeUndefined();
    expect(r.verificacion.valida).toBe(true);
    expect(r.mensaje).toBeNull();
  });

  it("un código mal formado no sale a la red", async () => {
    const f = vi.fn();
    const r = await load({ params: { id: "abc" }, fetch: f });
    expect(f).not.toHaveBeenCalled();
    expect(r.mensaje).toMatch(/36 caracteres/);
  });

  it("404 es neutro: no dice si existe ni si es reservada", async () => {
    const r = await load({ params: { id: ID }, fetch: async () => resp(404, {}) });
    expect(r.verificacion).toBeNull();
    expect(r.mensaje).toMatch(/No encontramos ninguna firma/);
  });

  it("503 no se presenta como firma inválida", async () => {
    const r = await load({ params: { id: ID }, fetch: async () => resp(503, {}) });
    expect(r.verificacion).toBeNull();
    expect(r.mensaje).toMatch(/No se pudo comprobar/);
    expect(r.mensaje).not.toMatch(/inválida|no es válida/i);
  });
});

describe("load de /firma (formulario)", () => {
  it("un código válido redirige a su dirección estable", () => {
    try {
      loadForm({ url: new URL(`http://x/firma?codigo=${ID.toUpperCase()}`) });
      throw new Error("debió redirigir");
    } catch (e) {
      expect(/** @type {any} */ (e).status).toBe(303);
      expect(/** @type {any} */ (e).location).toBe(`/firma/${ID}`);
    }
  });

  it("un código mal formado se explica sin redirigir", () => {
    const r = loadForm({ url: new URL("http://x/firma?codigo=xyz") });
    expect(r.mensaje).toMatch(/36 caracteres/);
  });
});

describe("pantalla", () => {
  it("firma electrónica válida: lo dice, y dice que NO está acreditada", () => {
    render(Pagina, { props: { data: { codigo: ID, verificacion: ELECTRONICA, mensaje: null } } });
    expect(screen.getByText("Firma electrónica válida")).toBeTruthy();
    expect(screen.getByText(/No acreditada \(ONAC\)/)).toBeTruthy();
    expect(screen.getByText("Ana Pérez")).toBeTruthy();
  });

  it("constancia simple: ni válida ni inválida", () => {
    const simple = {
      ...ELECTRONICA,
      kind: "simple_signature_record",
      valida: null,
      motivo: "constancia_firma_simple_no_verificable_por_terceros",
    };
    render(Pagina, { props: { data: { codigo: ID, verificacion: simple, mensaje: null } } });
    expect(screen.getByText(/no es una firma electrónica verificable por terceros/)).toBeTruthy();
    expect(screen.queryByText("Firma electrónica válida")).toBeNull();
    expect(screen.queryByText(/NO es válida/)).toBeNull();
  });
});
