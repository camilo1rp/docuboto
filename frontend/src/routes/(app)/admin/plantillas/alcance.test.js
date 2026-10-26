// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * Alcance de plantillas por dependencia (E23 Inc.5, T-14/T-15, RF-PRO-06).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { PUT } from "./api/[id]/alcance/+server.js";

const SESSION = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const LOAD = readFileSync("src/routes/(app)/admin/plantillas/+page.server.js", "utf-8");
const PAGINA = readFileSync("src/routes/(app)/admin/plantillas/+page.svelte", "utf-8");

function req(body) {
  return { json: async () => body };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("admin/plantillas/api/[id]/alcance", () => {
  it("sin sesión: 401 sin llamar al gateway", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse");
    });
    try {
      const res = await PUT({ locals: {}, params: { id: "p1" }, request: req({}) });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("global manda: la lista de dependencias se descarta", async () => {
    // Mantenerla guardada dejaría una asignación invisible que reaparecería al
    // volver a restringir, y nadie recordaría de dónde salió.
    const original = globalThis.fetch;
    let enviado = null;
    globalThis.fetch = vi.fn(async (_u, opts) => {
      enviado = JSON.parse(opts.body);
      return { ok: true, status: 200, json: async () => ({}) };
    });
    try {
      await PUT({
        locals: SESSION,
        params: { id: "p1" },
        request: req({ es_global: true, dependencias: [100, 200] }),
      });
      expect(enviado).toEqual({ es_global: true, dependencias: [] });
    } finally {
      globalThis.fetch = original;
    }
  });

  it("los códigos que no son enteros se descartan", async () => {
    const original = globalThis.fetch;
    let enviado = null;
    globalThis.fetch = vi.fn(async (_u, opts) => {
      enviado = JSON.parse(opts.body);
      return { ok: true, status: 200, json: async () => ({}) };
    });
    try {
      await PUT({
        locals: SESSION,
        params: { id: "p1" },
        request: req({ es_global: false, dependencias: [100, "200", null, 300.5] }),
      });
      expect(enviado.dependencias).toEqual([100]);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("restringir a ninguna dependencia se admite", async () => {
    // Estado válido a propósito: retirar una plantilla de circulación sin
    // borrar los documentos ya producidos con ella.
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    try {
      const res = await PUT({
        locals: SESSION,
        params: { id: "p1" },
        request: req({ es_global: false, dependencias: [] }),
      });
      expect(res.status).toBe(200);
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("la pantalla de administración", () => {
  it("pide TODAS las plantillas, no solo las de su dependencia", () => {
    // Es el defecto que este test existe para impedir: sin `?todas=true` el
    // listado se recorta por la dependencia del llamante, y la pantalla cuyo
    // trabajo es gestionar las plantillas de TODA la entidad dejaría fuera
    // precisamente las que hay que asignar.
    const linea = LOAD.slice(LOAD.indexOf("API_V1}/plantillas"), LOAD.indexOf("headers }"));
    expect(linea).not.toBe("");
    expect(linea).toContain("todas=true");
  });

  it("carga el organigrama para poder asignar", () => {
    expect(LOAD).toContain("/dependencias?activo=true");
  });

  it("degrada sin organigrama en vez de quedarse inservible", () => {
    // Un fallo del catálogo impide restringir, no crear ni editar.
    expect(LOAD).toContain("dependencias = []");
    expect(PAGINA).toContain("No se pudo cargar el organigrama");
  });

  it("nombra el estado «ninguna dependencia» en vez de enseñar un cero", () => {
    // «0 dependencias» se leería como un dato que falta, y es un estado
    // deliberado.
    expect(PAGINA).toContain("Ninguna (retirada de circulación)");
  });

  it("avisa antes de dejar una plantilla sin dependencias", () => {
    expect(PAGINA).toContain("deja de ofrecerse a");
  });
});
