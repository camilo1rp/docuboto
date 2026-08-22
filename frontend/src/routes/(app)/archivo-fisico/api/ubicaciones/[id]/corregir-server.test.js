// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * PATCH de una ubicación: allowlist estricta.
 *
 * La corrección existe por un caso real: `tipo_archivo` es opcional al crear y
 * decide qué transferencias admite la ubicación (Ley 594/2000 art. 23). Una
 * topografía cargada sin ese dato dejaba el archivo físico sin poder
 * transferir y sin ninguna ruta de corrección.
 *
 * Lo que este proxy NO deja pasar importa tanto como lo que deja: `codigo` es
 * la referencia impresa en las signaturas de las unidades y `parent_id`
 * movería de golpe toda la rama.
 */
import { describe, it, expect, afterEach, vi } from "vitest";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "demo", sub: "u-1" } };
const ID = "e4a72e5b-cb3e-49de-b958-19112b341e4e";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function backend() {
  const enviados = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, init) => {
      enviados.push({ url: String(url), method: init?.method, body: JSON.parse(init.body) });
      return { ok: true, status: 200, json: async () => ({ id: ID, codigo: "DEP-001" }) };
    }),
  );
  return enviados;
}

async function corregir(cuerpo) {
  const { PATCH } = await import("./+server.js");
  return PATCH({
    locals: LOCALS,
    params: { id: ID },
    request: new Request("http://x", { method: "PATCH", body: JSON.stringify(cuerpo) }),
  });
}

describe("PATCH /archivo-fisico/api/ubicaciones/[id]", () => {
  it("el tipo de archivo se corrige y viaja al backend", async () => {
    const enviados = backend();
    const res = await corregir({ tipo_archivo: "gestion" });

    expect(res.status).toBe(200);
    expect(enviados[0].method).toBe("PATCH");
    expect(enviados[0].body).toEqual({ tipo_archivo: "gestion" });
  });

  it("el código NO viaja aunque se mande", async () => {
    // Cambiarlo dejaría las etiquetas del depósito apuntando a una ubicación
    // que ya no se llama así.
    const enviados = backend();
    await corregir({ codigo: "OTRO", nombre: "Depósito uno" });

    expect(enviados[0].body).toEqual({ nombre: "Depósito uno" });
  });

  it("el padre tampoco: mover la rama no es una corrección", async () => {
    const enviados = backend();
    await corregir({ parent_id: "11111111-2222-4333-8444-555555555555", nombre: "X" });

    expect(enviados[0].body.parent_id).toBeUndefined();
  });

  it("un tipo de archivo inventado se descarta", async () => {
    // Los tres valores son los del ciclo vital del documento; cualquier otro
    // no significa nada y el backend lo rechazaría con un error de esquema.
    const enviados = backend();
    await corregir({ tipo_archivo: "intermedio", nombre: "X" });

    expect(enviados[0].body.tipo_archivo).toBeUndefined();
  });

  it("sin ningún campo válido no se llama al backend", async () => {
    // Gemelo: una petición vacía escribiría un asiento de auditoría que no
    // describe ningún cambio.
    const enviados = backend();
    const res = await corregir({ codigo: "SOLO-ESTO" });

    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("sin_cambios");
    expect(enviados).toEqual([]);
  });

  it("sin sesión, 401 y sin tocar el backend", async () => {
    const enviados = backend();
    const { PATCH } = await import("./+server.js");
    const res = await PATCH({
      locals: {},
      params: { id: ID },
      request: new Request("http://x", { method: "PATCH", body: "{}" }),
    });

    expect(res.status).toBe(401);
    expect(enviados).toEqual([]);
  });
});
