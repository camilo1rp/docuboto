// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Contrato del rechazo de transferencia: proxy BFF ↔ `TransferenciaRechazo`.
 *
 * EL DEFECTO QUE CIERRA. El backend exige `motivo` (obligatorio, mín. 3) y el
 * proxy mandaba `observacion`, opcional. La rama entera estaba MUERTA: todo
 * rechazo devolvía 422 y ninguno llegó nunca a ocurrir, mientras la pantalla
 * rotulaba el campo como «opcional» — contradiciendo una decisión que el
 * backend había tomado a propósito (un rechazo es terminal, y su motivo es el
 * único insumo de subsanación para la oficina de origen).
 *
 * Nadie lo cazó porque los dos lados están bien cada uno por su cuenta: el
 * esquema es correcto, el proxy es correcto, y solo el PAR está roto. Por eso
 * el test no se conforma con fijar el literal `motivo` —eso volvería a
 * divergir el día que el backend lo renombre— sino que LEE los campos
 * requeridos del esquema Pydantic y comprueba que el proxy los manda.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { POST as rechazar } from "./+server.js";

const ESQUEMA = resolve(process.cwd(), "../services/archive-service/app/schemas/transferencia.py");

const ID = "b44330b6-6d7e-489d-a58c-f62a0e2d0f53";
const LOCALS = {
  accessToken: "tok",
  user: { tenant_slug: "demo", sub: "33333333-3333-3333-3333-333333333333" },
};
const MOTIVO = "Falta la unidad de conservación asignada";

function peticion(body) {
  return { json: async () => body };
}

/** Campos SIN default de `TransferenciaRechazo` — los que Pydantic exige. */
function camposRequeridosDelEsquema() {
  const fuente = readFileSync(ESQUEMA, "utf-8");
  const i = fuente.indexOf("class TransferenciaRechazo(BaseModel):");
  expect(i, "la clase TransferenciaRechazo ya no existe con ese nombre").toBeGreaterThan(-1);
  const siguiente = fuente.indexOf("\nclass ", i + 1);
  let cuerpo = fuente.slice(i, siguiente === -1 ? undefined : siguiente);
  // El docstring se quita ANTES de analizar: su prosa contiene líneas como
  // «OBLIGATORIO: antes se admitía…», que la expresión de campo confundía con
  // una declaración. Un extractor que lee comentarios inventa contrato.
  cuerpo = cuerpo.replace(/"""[\s\S]*?"""/g, "");

  const requeridos = [];
  for (const linea of cuerpo.split("\n")) {
    // `nombre: tipo = Field(...)` — requerido si no hay `default`/`= None`.
    const m = /^\s{4}(\w+)\s*:\s*[^=]+(=\s*(.*))?$/.exec(linea);
    if (!m) continue;
    const [, nombre, , asignado] = m;
    if (!asignado) requeridos.push(nombre);
    else if (!/default|None/.test(asignado)) requeridos.push(nombre);
  }
  return requeridos;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("POST /transferencias/api/[id]/rechazar — contrato con el backend", () => {
  it("el proxy manda EXACTAMENTE los campos que el esquema exige", async () => {
    const requeridos = camposRequeridosDelEsquema();
    // Anti-vacuidad: si la extracción se rompe, el test pasaría comparando el
    // conjunto vacío contra cualquier cosa.
    expect(requeridos.length, "no se extrajo ningún campo del esquema").toBeGreaterThan(0);

    let enviado = null;
    globalThis.fetch = vi.fn(async (_url, init) => {
      enviado = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });

    await rechazar({ locals: LOCALS, params: { id: ID }, request: peticion({ motivo: MOTIVO }) });

    for (const campo of requeridos) {
      expect(Object.keys(enviado), `el backend exige «${campo}» y el proxy no lo manda`).toContain(
        campo,
      );
    }
  });

  it("rechaza sin motivo SIN llamar al gateway, con un mensaje que nombra el campo", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamarse al gateway");
    });
    for (const body of [{}, { motivo: "  " }, { motivo: "ab" }, { motivo: 7 }]) {
      const res = await rechazar({ locals: LOCALS, params: { id: ID }, request: peticion(body) });
      expect(res.status).toBe(400);
      expect((await res.json()).detail).toMatch(/motivo/i);
    }
  });

  it("no reenvía campos ajenos que vengan en el cuerpo", async () => {
    let enviado = null;
    globalThis.fetch = vi.fn(async (_url, init) => {
      enviado = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });
    await rechazar({
      locals: LOCALS,
      params: { id: ID },
      request: peticion({ motivo: MOTIVO, estado: "recibida", decidida_por: "otro" }),
    });
    expect(Object.keys(enviado)).toEqual(["motivo"]);
  });

  it("propaga el status del gateway en vez de colapsarlo", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: { error: "estado_invalido", status: 409 } }), {
          status: 409,
        }),
    );
    const res = await rechazar({
      locals: LOCALS,
      params: { id: ID },
      request: peticion({ motivo: MOTIVO }),
    });
    expect(res.status).toBe(409);
  });
});
