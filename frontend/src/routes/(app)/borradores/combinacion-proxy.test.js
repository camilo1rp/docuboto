// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * combinacion-proxy.test.js — combinación de campos de plantilla (E23, Inc.1).
 *
 * Cubre el proxy SSR y, sobre todo, el punto que hacía inútil todo el
 * incremento: que la pantalla PIDA el cuerpo combinado en vez de precargar la
 * plantilla cruda desde el navegador.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { POST } from "./api/plantillas/[id]/combinar/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const PAGINA = readFileSync("src/routes/(app)/borradores/+page.svelte", "utf-8");

function jsonRequest(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("borradores/api/plantillas/[id]/combinar POST", () => {
  it("sin token: 401, sin llamar al gateway", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await POST({
        locals: {},
        params: { id: "p1" },
        request: jsonRequest({}),
      });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("devuelve el cuerpo saneado y los faltantes", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        cuerpo: "<p>Señor Ana Ruiz</p><script>alert(1)</script>",
        faltantes: ["TEL_R"],
      }),
    }));
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: "p1" },
        request: jsonRequest({ radicado_padre_id: "r1" }),
      });
      const data = await res.json();
      expect(res.status).toBe(200);
      expect(data.faltantes).toEqual(["TEL_R"]);
      // El HTML que va al editor pasa por el saneo, como el del listado.
      expect(data.cuerpo_html).toContain("Ana Ruiz");
      expect(data.cuerpo_html).not.toContain("<script");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("propaga el estado de error del gateway", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ error: "not_found" }),
    }));
    try {
      const res = await POST({
        locals: SESSION_LOCALS,
        params: { id: "p1" },
        request: jsonRequest({}),
      });
      expect(res.status).toBe(404);
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("la pantalla de borradores no combina en el navegador", () => {
  it("pide el cuerpo combinado al servidor al elegir plantilla", () => {
    // Este es EL test del incremento. Mientras la página sembrara el editor con
    // `found.cuerpo_html` y enviara ese texto al crear, el `cuerpo` llegaba
    // lleno al backend y la rama que combina no se ejecutaba nunca: el
    // `*NOM_R*` acababa impreso en el oficio del ciudadano.
    expect(PAGINA).toContain("/borradores/api/plantillas/");
    expect(PAGINA).toContain("combinar");
  });

  it("enseña los campos que quedaron sin resolver", () => {
    // Un campo vacío en silencio es peor que un marcador crudo: nadie lo ve
    // hasta que el documento ya salió.
    expect(PAGINA).toContain("combinacionFaltantes");
    expect(PAGINA).toMatch(/combinacionFaltantes\.length > 0/);
  });

  it("avisa cuando la combinación falla en vez de fingir que resolvió", () => {
    expect(PAGINA).toContain("combinacionError");
    expect(PAGINA).toContain("SIN resolver");
  });
});
