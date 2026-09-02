// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * circuito-ui.test.js — El circuito de firmas se puede abrir DESDE la pantalla.
 *
 * El proxy `/borradores/api/[id]/circuito` existía, estaba probado, y su propio
 * comentario decía que cerraba el hueco de que ninguna pantalla creara cadenas
 * de firma. Ninguna pantalla lo llamaba. El backend sabía componer circuitos de
 * visto bueno y firma y desde el navegador no había forma de crear uno:
 * `/firmas` era una bandeja que firmaba lo que otro creaba, y no había otro.
 *
 * Es el requisito que el usuario planteó —varios dan el visto bueno, uno firma,
 * y si el documento cambia hay que volver a recogerlas— así que el hueco no
 * estaba en un detalle: estaba en la función entera.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { GET as GET_FIRMANTES } from "./api/firmantes/+server.js";

const PAGINA = readFileSync("src/routes/(app)/borradores/+page.svelte", "utf-8");

const SESSION = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => vi.restoreAllMocks());

describe("la pantalla usa el circuito", () => {
  it("ABRE el circuito con un POST que lleva los turnos", () => {
    // Se asegura la LLAMADA, no la existencia del proxy: tener el endpoint
    // escrito y probado es exactamente lo que había mientras el defecto existía.
    //
    // Y se exige el POST con su cuerpo en el MISMO fragmento. Una primera
    // versión de esta prueba buscaba la URL por separado y pasaba con la
    // creación rota: casaba con la lectura del circuito, que llama a la misma
    // ruta en GET. Dos llamadas a la misma URL no son la misma llamada.
    const postDelCircuito =
      /fetch\(`\/borradores\/api\/\$\{editingBorrador\.id\}\/circuito`,\s*\{[\s\S]{0,400}?method:\s*"POST"[\s\S]{0,400}?turnos/;
    expect(PAGINA).toMatch(postDelCircuito);
  });

  it("lee el circuito ya abierto para poder enseñar en qué turno va", () => {
    expect(PAGINA).toMatch(/fetch\(`\/borradores\/api\/\$\{borradorId\}\/circuito`\)/);
  });

  it("carga a las personas elegibles desde su propio proxy", () => {
    expect(PAGINA).toMatch(/fetch\("\/borradores\/api\/firmantes"\)/);
  });

  it("no deja abrir un circuito sin turno de firma ni con repetidos", () => {
    // Las dos condiciones son del trámite, no del formulario: sin firma no hay
    // documento definitivo, y una persona repetida acabaría dando el visto
    // bueno a su propia firma.
    expect(PAGINA).toMatch(/disabled=\{!turnosCompletos \|\| !hayFirma \|\| hayRepetidos\}/);
  });

  it("avisa de que editar el texto tira las aprobaciones", () => {
    // Es la mitad del requisito: el circuito se revierte cuando el documento
    // cambia, y quien compone el circuito tiene que saberlo de antemano.
    expect(PAGINA).toMatch(/aprobaciones recogidas dejan de valer/);
  });
});

describe("proxy de firmantes elegibles", () => {
  it("consume /auth/users/pickable, no el listado de administración", async () => {
    // Componer un circuito es trabajo de quien redacta un oficio. Pedir
    // USUA_PERM_ADMIN para elegir a un compañero dejaría la función en manos de
    // casi nadie de los que producen documentos.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("[]", { headers: { "content-type": "application/json" } }));

    await GET_FIRMANTES({ locals: SESSION });

    const url = fetchSpy.mock.calls[0][0];
    expect(url).toContain("/auth/users/pickable");
    expect(url).not.toMatch(/\/auth\/users\?/);
  });

  it("sin sesión responde 401 y no llama a nadie", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const res = await GET_FIRMANTES({ locals: {} });
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
