// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * vocabulario.test.js — El vocabulario de marcadores llega a la pantalla.
 *
 * El backend publica `GET /plantillas/campos` desde el primer día y esta
 * pantalla —la única donde se ESCRIBEN plantillas— no lo pedía. El vocabulario
 * es cerrado: un marcador que no esté en él no se resuelve al combinar, se
 * imprime literal en el oficio que recibe el ciudadano. Sin la lista a la
 * vista, escribirlo bien era adivinar.
 *
 * Se ejerce el `load` REAL, no el texto del archivo: que la petición esté
 * escrita no prueba que su resultado llegue a `data`.
 */
import { describe, it, expect, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({ API_V1: "http://gw:8080/api/v1" }));
vi.mock("$lib/server/sanitizeHtml.js", () => ({ attachSafeCuerpo: () => {} }));
vi.mock("$lib/services/auth.server.js", () => ({ COOKIE_ACCESS_TOKEN: "access_token" }));

const { load } = await import("./+page.server.js");

const json = (cuerpo, status = 200) =>
  new Response(JSON.stringify(cuerpo), { status, headers: { "content-type": "application/json" } });

const CAMPOS = [
  {
    nombre: "NOM_R",
    marcador: "*NOM_R*",
    descripcion: "Nombre del destinatario",
    grupo: "Destinatario",
    diferido: false,
  },
  {
    nombre: "RAD_S",
    marcador: "*RAD_S*",
    descripcion: "Número de radicado",
    grupo: "Radicación",
    diferido: true,
  },
];

/** @param {(url: string) => Response} responder */
function evento(responder) {
  return {
    cookies: { get: () => "tok" },
    parent: async () => ({ user: { tenant_slug: "demo" } }),
    fetch: vi.fn(async (url) => responder(url)),
  };
}

describe("admin/plantillas — vocabulario de marcadores", () => {
  it("pide /plantillas/campos y lo devuelve en `data.campos`", async () => {
    const ev = evento((url) =>
      url.includes("/plantillas/campos") ? json({ campos: CAMPOS }) : json([]),
    );

    const data = await load(ev);

    expect(ev.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/plantillas/campos"),
      expect.anything(),
    );
    expect(data.campos).toEqual(CAMPOS);
  });

  it("si el vocabulario falla, la pantalla sigue usable sin él", async () => {
    // Degradar es correcto: sin la lista se puede seguir redactando. Lo que no
    // puede es tumbar la pantalla ni dejar `campos` en `undefined`, que
    // reventaría el `.reduce` de la vista.
    const ev = evento((url) =>
      url.includes("/plantillas/campos") ? json({ error: "boom" }, 503) : json([]),
    );

    const data = await load(ev);

    expect(data.campos).toEqual([]);
    expect(data.loadError).toBeNull();
  });

  it("el fallo del vocabulario NO se confunde con el de la lista de plantillas", async () => {
    // Anti-vacuidad: si `campos` se llenara siempre a [], los dos casos de
    // arriba pasarían igual. Aquí la lista sí trae datos y el vocabulario no.
    const ev = evento((url) =>
      url.includes("/plantillas/campos")
        ? json({ campos: [] })
        : url.includes("/plantillas")
          ? json([{ id: "p-1", nombre: "Oficio" }])
          : json([]),
    );

    const data = await load(ev);

    expect(data.campos).toEqual([]);
    expect(data.plantillas).toHaveLength(1);
  });
});
