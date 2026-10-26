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
 * Circuito de visto bueno y firma en la interfaz (E23 Inc.4, T-23).
 *
 * Cubre las dos mitades: que `/firmas` distinga aprobar de firmar, y que
 * `/borradores` pueda CREAR el circuito — hasta ahora ninguna pantalla creaba
 * cadenas de firma, así que el endpoint existía y solo lo invocaban los tests.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { POST as voboPOST } from "./api/[id]/vobo/+server.js";
import {
  POST as circuitoPOST,
  GET as circuitoGET,
} from "../borradores/api/[id]/circuito/+server.js";

const SESSION = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

const FIRMAS = readFileSync("src/routes/(app)/firmas/+page.svelte", "utf-8");

function req(body) {
  return { json: async () => body };
}

function forbidFetch() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse");
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("firmas/api/[id]/vobo", () => {
  it("sin sesión: 401 sin llamar al gateway", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await voboPOST({ locals: {}, params: { id: "s1" }, request: req({}) });
      expect(res.status).toBe(401);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("un cuerpo ausente no es un error: el comentario es opcional", async () => {
    // Aprobar no exige explicación; detener el trámite de otro sí. Si esto
    // devolviera 400, dar el visto bueno obligaría a escribir algo.
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ estado: "pendiente" }),
    }));
    try {
      const res = await voboPOST({
        locals: SESSION,
        params: { id: "s1" },
        request: {
          json: async () => {
            throw new Error("sin cuerpo");
          },
        },
      });
      expect(res.status).toBe(200);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("NO manda ningún segundo factor al gateway", async () => {
    // Es la decisión D-6, y que este proxy no tenga dónde ponerlo es la forma
    // de que no se cuele por descuido.
    const original = globalThis.fetch;
    let enviado = null;
    globalThis.fetch = vi.fn(async (_url, opts) => {
      enviado = JSON.parse(opts.body);
      return { ok: true, status: 200, json: async () => ({}) };
    });
    try {
      await voboPOST({
        locals: SESSION,
        params: { id: "s1" },
        request: req({ comentario: "de acuerdo", totp_code: "123456", otp: "999999" }),
      });
      expect(Object.keys(enviado)).toEqual(["comentario"]);
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("borradores/api/[id]/circuito", () => {
  it("un circuito sin turno de firma se rechaza", async () => {
    // Sin turno de firma no hay documento definitivo y el borrador se queda
    // sin poder salir.
    const original = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await circuitoPOST({
        locals: SESSION,
        params: { id: "b1" },
        request: req({ turnos: [{ firmante_id: "u1", rol: "visto_bueno" }] }),
      });
      expect(res.status).toBe(400);
      expect((await res.json()).detail).toContain("turno de firma");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("un rol inventado se rechaza", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = forbidFetch();
    try {
      const res = await circuitoPOST({
        locals: SESSION,
        params: { id: "b1" },
        request: req({ turnos: [{ firmante_id: "u1", rol: "revisor" }] }),
      });
      expect(res.status).toBe(400);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("manda firmantes y roles PARALELOS, y ninguna huella", async () => {
    // La huella la resuelve el servidor: una que elige el cliente no vincula a
    // nadie. Que este proxy no tenga dónde ponerla es la garantía.
    const original = globalThis.fetch;
    let enviado = null;
    globalThis.fetch = vi.fn(async (_url, opts) => {
      enviado = JSON.parse(opts.body);
      return { ok: true, status: 201, json: async () => ({}) };
    });
    try {
      await circuitoPOST({
        locals: SESSION,
        params: { id: "b1" },
        request: req({
          titulo: "Oficio",
          turnos: [
            { firmante_id: "u1", rol: "visto_bueno" },
            { firmante_id: "u2", rol: "visto_bueno" },
            { firmante_id: "u3", rol: "firma" },
          ],
        }),
      });
      expect(enviado.objeto_tipo).toBe("borrador");
      expect(enviado.objeto_id).toBe("b1");
      expect(enviado.firmantes).toEqual(["u1", "u2", "u3"]);
      expect(enviado.roles).toEqual(["visto_bueno", "visto_bueno", "firma"]);
      expect(enviado.payload_sha256).toBeUndefined();
    } finally {
      globalThis.fetch = original;
    }
  });

  it("GET consulta la cadena abierta del borrador", async () => {
    const original = globalThis.fetch;
    let url = "";
    globalThis.fetch = vi.fn(async (u) => {
      url = String(u);
      return { ok: true, status: 200, json: async () => ({ items: [] }) };
    });
    try {
      const res = await circuitoGET({ locals: SESSION, params: { id: "b1" } });
      expect(res.status).toBe(200);
      expect(url).toContain("objeto_tipo=borrador");
      expect(url).toContain("objeto_id=b1");
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("la pantalla de firmas separa aprobar de firmar", () => {
  it("ofrece el acto que corresponde al ROL del turno propio", () => {
    // Ofrecer los dos, o el equivocado, atribuiría a un revisor un compromiso
    // que no adquirió — que es justo lo que el rol existe para separar.
    //
    // Se acota a la fila de acciones y NO se busca en todo el archivo: con un
    // `[\s\S]*` sobre el fichero entero, la condición del bloque de la nota
    // y el botón de otro bloque casaban juntos, y sustituir la condición del
    // botón por `false` dejaba el test en verde. Cuarta vez en esta épica que
    // una aserción mide algo contiguo en vez de lo que afirma.
    const inicio = FIRMAS.indexOf('class="firma-detail__actions-row"');
    // El corte se busca DESDE `inicio`: «Rechazar» aparece antes en el fichero
    // (en el comentario de cabecera), y un `indexOf` desde 0 devolvía un rango
    // vacío que hacía pasar cualquier cosa.
    const fila = FIRMAS.slice(inicio, FIRMAS.indexOf("Rechazar", inicio));
    expect(fila).not.toBe("");
    expect(fila).toMatch(/\{#if esTurnoDeVistoBueno\}[\s\S]{0,400}Dar visto bueno/);
    expect(fila).toMatch(/\{:else\}[\s\S]{0,300}Firmar/);
  });

  it("enseña el rol de CADA turno, también los ya resueltos", () => {
    // Quien lea la cadena dentro de diez años tiene que poder distinguir quién
    // aprobó de quién firmó.
    expect(FIRMAS).toContain("firma-chain__rol");
    expect(FIRMAS).toContain("ROL_LABEL");
  });

  it("el rol se distingue por FORMA, no solo por color", () => {
    // Quien no distingue los dos tonos tiene que poder separar igualmente una
    // opinión de un compromiso de la entidad.
    expect(FIRMAS).toMatch(/&--firma \{[\s\S]*?font-weight/);
    expect(FIRMAS).toMatch(/&--firma \{[\s\S]*?border-color/);
  });

  it("dice al revisor que aprueba y no firma", () => {
    expect(FIRMAS).toContain("apruebas el documento");
  });
});
