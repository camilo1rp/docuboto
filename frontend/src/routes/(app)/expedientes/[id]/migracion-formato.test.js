// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * migracion-formato.test.js — El proxy de la migración de formato (RF-PRE-04).
 *
 * Dos cosas que no se pueden fallar:
 *
 *  - **El `503` del convertidor ausente llega entero.** Dice que este
 *    despliegue no tiene Ghostscript instalado, que es un problema de
 *    instalación y no del documento; colapsarlo a «no se pudo migrar» mandaría
 *    a depurar el PDF equivocado.
 *  - **El perfil pasa por una lista CERRADA.** Las variantes `a`/`u` de PDF/A
 *    exigen texto etiquetado o Unicode garantizado, y una conversión
 *    automática no puede prometerlo: ofrecerlas sería declarar una conformidad
 *    que el convertidor no alcanza.
 */
import { describe, it, expect, vi } from "vitest";

import { POST } from "./api/migrar-formato/+server.js";

const FILE = "11111111-1111-4111-8111-111111111111";

function llamada(body, { token = "tok", status = 200, respuesta = {} } = {}) {
  const fetch = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => respuesta,
  }));
  const event = {
    params: { id: "22222222-2222-4222-8222-222222222222" },
    request: { json: async () => body },
    locals: { accessToken: token, user: { tenant_slug: "icetex", sub: "u-1" } },
    fetch,
  };
  return { event, fetch };
}

describe("proxy de migración de formato", () => {
  it("reenvía el archivo y el perfil", async () => {
    const { event, fetch } = llamada({ file_id: FILE, perfil: "3b" });

    await POST(event);

    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      file_id: FILE,
      perfil: "3b",
    });
  });

  it("un perfil fuera de la lista cerrada cae al de política, no viaja", async () => {
    const { event, fetch } = llamada({ file_id: FILE, perfil: "2a" });

    await POST(event);

    expect(JSON.parse(fetch.mock.calls[0][1].body).perfil).toBe("2b");
  });

  it("el 503 del convertidor ausente llega ENTERO, no colapsado", async () => {
    const detalle = {
      error: "migrador_no_disponible",
      detail: "Este despliegue no tiene convertidor de formato configurado…",
      status: 503,
    };
    const { event } = llamada({ file_id: FILE }, { status: 503, respuesta: { detail: detalle } });

    const res = await POST(event);

    expect(res.status).toBe(503);
    expect((await res.json()).detail).toEqual(detalle);
  });

  it("un identificador de archivo inválido no viaja", async () => {
    const { event, fetch } = llamada({ file_id: "../../otro" });

    const res = await POST(event);

    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sin sesión no se llama al backend", async () => {
    const { event, fetch } = llamada({ file_id: FILE }, { token: "" });

    expect((await POST(event)).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
});
