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

describe("el panel lee el contrato REAL de signature-service", () => {
  // Los nombres de campo se adivinaron en la primera versión —`turnos`,
  // `firmado_at`, y la respuesta tratada como lista— y el panel salía
  // «turno 1 de 0» con el circuito perfectamente creado en la base de datos.
  // El contrato de un servicio se lee, no se supone.
  const ESQUEMA = readFileSync("../services/signature-service/app/schemas/cadena.py", "utf-8");

  /** Campos declarados en una clase Pydantic del esquema real. */
  function camposDe(clase) {
    const cuerpo = ESQUEMA.split(`class ${clase}(BaseModel):`)[1]?.split("\nclass ")[0] ?? "";
    return new Set([...cuerpo.matchAll(/^ {4}(\w+):/gm)].map((m) => m[1]));
  }

  it("la lista de turnos se llama `items`, no `turnos`", () => {
    expect(camposDe("SolicitudResponse").has("items")).toBe(true);
    expect(PAGINA).toMatch(/circuitoActual\?\.items/);
    expect(PAGINA).not.toMatch(/circuitoActual\?\.turnos/);
  });

  it("un turno cumplido se reconoce por `estado`, no por un `firmado_at` inventado", () => {
    const campos = camposDe("TurnoItem");
    expect(campos.has("estado")).toBe(true);
    expect(campos.has("firmado_at")).toBe(false);
    expect(PAGINA).toMatch(/turno\.estado !== "pendiente"/);
    expect(PAGINA).not.toMatch(/turno\.firmado_at/);
  });

  it("la respuesta es UN objeto, no una colección", () => {
    // El router declara `response_model=SolicitudResponse` y responde 404 si no
    // hay circuito abierto. Tratarla como lista dejaba `circuitoActual` en null
    // para siempre.
    expect(PAGINA).toMatch(/circuitoActual = data\?\.estado === "pendiente"/);
  });
});

describe("la vuelta atrás del ciclo", () => {
  // El ciclo era de una sola dirección —borrador → aprobado → radicado— y el
  // texto quedaba congelado al aprobar: la única salida era radicar. Eso dejaba
  // inalcanzable la mitad del trámite que se pidió, porque un circuito de
  // firmas se revierte cuando el documento cambia y no había forma de
  // cambiarlo.
  it("la pantalla ofrece devolver a edición y llama a su proxy", () => {
    expect(PAGINA).toMatch(/Devolver a edición/);
    expect(PAGINA).toMatch(
      /fetch\(`\/borradores\/api\/\$\{editingBorrador\.id\}\/devolver`,\s*\{[\s\S]{0,120}?method:\s*"POST"/,
    );
  });

  it("avisa de los turnos ya cumplidos antes de reabrir", () => {
    // Reabrir no tira nada por sí solo; corregir el texto sí. Quien pidió las
    // aprobaciones debería saberlo antes de tener que volver a pedirlas.
    expect(PAGINA).toMatch(/turnosCumplidos > 0/);
    expect(PAGINA).toMatch(/habrá que recogerlos de nuevo/);
  });
});

describe("el circuito ata también en la pantalla", () => {
  // El backend responde 409 `circuito_pendiente` (E23, RF-PRO-09). Un botón
  // que solo puede fallar convierte una regla del trámite en un tropiezo: el
  // funcionario descubre el límite estrellándose contra él.
  it("con turnos sin resolver NO se ofrece «Aprobar borrador»", () => {
    expect(PAGINA).toMatch(/\{#if circuitoAbierto\}/);
    // El botón queda en la rama `:else`, no antes del `if`.
    const iAviso = PAGINA.indexOf("{#if circuitoAbierto}");
    const iBoton = PAGINA.indexOf("Aprobar borrador");
    expect(iAviso).toBeGreaterThan(-1);
    expect(iBoton).toBeGreaterThan(iAviso);
  });

  it("el estado del circuito se lee también en edición, no solo en «aprobado»", () => {
    // El panel del circuito solo se pintaba con el borrador ya aprobado, así
    // que el dato se cargaba y nadie lo miraba mientras se editaba: justo
    // cuando hace falta para no ofrecer el botón.
    expect(PAGINA).toMatch(
      /\$: circuitoAbierto = circuitoActual !== null && turnosCumplidos < turnosDelCircuito\.length/,
    );
  });

  it("el aviso dice cuántos turnos faltan, no solo que hay circuito", () => {
    expect(PAGINA).toMatch(/turnosDelCircuito\.length - turnosCumplidos/);
  });
});

describe("radicar es el acto que de verdad hay que atar", () => {
  // El circuito se abre sobre un borrador YA aprobado —ese es el orden del
  // trámite—, así que la vía real para saltárselo nunca fue aprobar dos veces:
  // era radicar con los turnos sin resolver. Y radicar es peor: aprobar de más
  // se deshace devolviendo a edición; un consecutivo gastado no se devuelve.
  it("con el circuito abierto no se ofrece «Radicar»", () => {
    const iRadicar = PAGINA.indexOf("pedirConfirmacionRadicar}");
    const iAviso = PAGINA.indexOf("No se puede radicar todavía");
    expect(iAviso).toBeGreaterThan(-1);
    // El aviso está en la rama que precede al botón dentro del mismo bloque.
    expect(iAviso).toBeLessThan(iRadicar);
  });

  it("el aviso dice por qué importa, no solo que no se puede", () => {
    expect(PAGINA).toMatch(/número,?\s+y el número\s+no se devuelve/);
  });
});
