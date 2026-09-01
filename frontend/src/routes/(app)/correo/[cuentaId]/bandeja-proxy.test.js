// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxies y `load` de la bandeja de correo (E19 Increment 2).
 *
 * Lo que se blinda: que la acción salga de una lista cerrada, que abrir la
 * pantalla no dispare una sincronización —traer del buzón es un acto que el
 * operador decide—, y que los 409 de la reserva lleguen al cliente tal cual en
 * vez de disfrazarse de avería.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { POST as accion } from "./api/[mensajeId]/+server.js";
import { GET as estado } from "./api/estado/+server.js";
import { load } from "./+page.server.js";

const locals = {
  accessToken: "tok",
  user: {
    tenant_slug: "demo",
    // Los DOS identificadores, distintos a propósito: el `sub` es de Keycloak y
    // el `user_id` es la identidad interna del tenant, que es la que el backend
    // escribe en las columnas de autoría. Con un solo valor, un test no podría
    // distinguir cuál usa la pantalla.
    sub: "11111111-1111-1111-1111-111111111111",
    user_id: "99999999-9999-9999-9999-999999999999",
  },
};
const CUENTA = "22222222-2222-2222-2222-222222222222";
const MENSAJE = "33333333-3333-3333-3333-333333333333";
const params = { cuentaId: CUENTA, mensajeId: MENSAJE };
const peticion = (c = {}) => ({ json: async () => c });
const conAccion = (a) => ({ searchParams: new URLSearchParams(`accion=${a}`) });

let original;
beforeEach(() => {
  original = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = original;
});

describe("proxy de acciones sobre un correo", () => {
  it("solo admite las acciones de la lista cerrada", async () => {
    globalThis.fetch = vi.fn();
    for (const mala of ["borrar", "constructor", "toString", ""]) {
      const res = await accion({ locals, params, url: conAccion(mala), request: peticion() });
      expect(res.status, `aceptó «${mala}»`).toBe(400);
    }
    // `constructor` y `toString` son la razón de usar `Object.hasOwn`: un acceso
    // directo devolvería un miembro heredado de Object.prototype y pasaría el
    // guard.
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("compone la ruta de la acción y propaga la sesión", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(String(url)).toContain(`/email/cuentas/${CUENTA}/bandeja/${MENSAJE}/reservar`);
      expect(opts.headers["X-User-Id"]).toBe(locals.user.sub);
      return new Response(JSON.stringify({ id: MENSAJE }), { status: 200 });
    });
    const res = await accion({ locals, params, url: conAccion("reservar"), request: peticion() });
    expect(res.status).toBe(200);
  });

  it("liberar está en la lista, y su 204 no lleva cuerpo", async () => {
    // Un 204 con cuerpo no es una respuesta válida, y `json({}, {status: 204})`
    // produce exactamente eso. Se comprueba que el cuerpo va vacío de verdad.
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(String(url)).toContain(`/bandeja/${MENSAJE}/liberar`);
      expect(opts.method).toBe("POST");
      return new Response(null, { status: 204 });
    });
    const res = await accion({ locals, params, url: conAccion("liberar"), request: peticion() });
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });

  it("un 409 de la reserva llega al cliente tal cual", async () => {
    // No es una avería: significa que la reserva está haciendo su trabajo, y la
    // pantalla necesita distinguirlo para avisar en vez de alarmar.
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: { error: "correo_ocupado", detail: "Otro lo trabaja" } }),
          { status: 409 },
        ),
    );
    const res = await accion({ locals, params, url: conAccion("reservar"), request: peticion() });
    expect(res.status).toBe(409);
    expect((await res.json()).detail.error).toBe("correo_ocupado");
  });

  it("sin sesión no se llama al gateway", async () => {
    globalThis.fetch = vi.fn();
    const res = await accion({
      locals: {},
      params,
      url: conAccion("radicar"),
      request: peticion(),
    });
    expect(res.status).toBe(401);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("un identificador que no es UUID se rechaza antes de construir la URL", async () => {
    globalThis.fetch = vi.fn();
    const res = await accion({
      locals,
      params: { cuentaId: CUENTA, mensajeId: "../../admin" },
      url: conAccion("radicar"),
      request: peticion(),
    });
    expect(res.status).toBe(400);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe("sondeo de estado", () => {
  it("consulta el resumen, no la lista entera", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url) => {
      llamadas.push(String(url));
      return new Response(JSON.stringify({ pendientes: 2, ocupados: 1 }), { status: 200 });
    });
    const res = await estado({ locals, params: { cuentaId: CUENTA } });
    expect(res.status).toBe(200);
    expect(llamadas[0]).toContain("/bandeja/estado");
  });
});

describe("load de la bandeja", () => {
  it("carga la lista y la cuenta, y NUNCA sincroniza", async () => {
    const llamadas = [];
    const fake = vi.fn(async (url, opts) => {
      llamadas.push({ url: String(url), metodo: opts?.method ?? "GET" });
      if (String(url).includes("/bandeja")) {
        return new Response(JSON.stringify([{ id: "m1", asunto: "Petición" }]), { status: 200 });
      }
      return new Response(JSON.stringify([{ id: CUENTA, nombre: "Ventanilla" }]), { status: 200 });
    });

    const result = await load({
      cookies: { get: () => "tok" },
      parent: async () => ({ user: locals.user }),
      fetch: fake,
      params: { cuentaId: CUENTA },
    });

    // Traer del buzón es un acto que el operador dispara, no un efecto de abrir
    // una pantalla: descargar un buzón entero cada vez que alguien mira la
    // bandeja sería un coste que nadie pidió.
    expect(llamadas.every((l) => !l.url.includes("sincronizar"))).toBe(true);
    expect(llamadas.every((l) => l.metodo === "GET")).toBe(true);

    expect(result.correos).toHaveLength(1);
    expect(result.cuenta.nombre).toBe("Ventanilla");
    // `user_id`, NO `sub`: comparar la autoría del backend con el identificador
    // de Keycloak haría que el funcionario no reconociera sus propios correos y,
    // con el botón deshabilitado sobre lo ajeno, no pudiera continuar con el que
    // él mismo abrió. Ya rompió tres pantallas antes que esta.
    expect(result.yo).toBe(locals.user.user_id);
    expect(result.yo).not.toBe(locals.user.sub);
    expect(result.correosError).toBeNull();
  });

  it("un 403 conserva el status para que la pantalla no lo llame 'vacío'", async () => {
    const fake = vi.fn(async (url) =>
      String(url).includes("/bandeja")
        ? new Response(JSON.stringify({ detail: { detail: "Sin permiso" } }), { status: 403 })
        : new Response(JSON.stringify([]), { status: 200 }),
    );
    const result = await load({
      cookies: { get: () => "t" },
      parent: async () => ({ user: locals.user }),
      fetch: fake,
      params: { cuentaId: CUENTA },
    });
    expect(result.correos).toEqual([]);
    expect(result.correosError.status).toBe(403);
  });
});

describe("una bandeja que no se alcanza no finge ser una bandeja", () => {
  /**
   * Encontrado en el navegador, no en un test: `jefe` (administrador) abrió por
   * URL directa la bandeja de la cuenta PERSONAL de otra funcionaria. El backend
   * la negó con el `404` neutro, correcto — pero la pantalla, además del aviso
   * bueno, mostraba en ámbar «no se está pudiendo comprobar si hay novedades,
   * así que esta lista puede estar desactualizada, actualícela a mano antes de
   * radicar». Sobre una bandeja que no existe y en la que no se puede radicar
   * nada. El sondeo seguía preguntando cada 12 s y su 404 se leía como un fallo
   * transitorio: un error de AUTORIZACIÓN presentado como otra cosa, que es el
   * mismo defecto que hacía que `/envios` presentara un 403 como «no hay
   * registros».
   *
   * Se comprueba sobre el fuente porque las tres consecuencias —no sondear, no
   * avisar de sondeo caído, no ofrecer el botón— cuelgan de una sola condición,
   * y lo que no puede volver es que esa condición desaparezca.
   */
  it("con `correosError` no se sondea, no se avisa de sondeo caído y no se ofrece consultar", async () => {
    const { readFileSync } = await import("node:fs");
    const fuente = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.svelte", "utf-8");

    // Anti-vacuidad: el fichero es el que creo que es.
    expect(fuente).toContain("mirarSiHayNovedades");
    expect(fuente).toContain("Consultar el buzón");

    // La condición existe y sale del error de carga.
    expect(fuente).toMatch(/\$:\s*bandejaInaccesible\s*=\s*Boolean\(correosError\)/);

    // 1. El sondeo no arranca.
    expect(fuente).toMatch(/if\s*\(!bandejaInaccesible\)\s*sondeo\s*=\s*setInterval/);
    // 2. El aviso de sondeo caído queda callado.
    expect(fuente).toContain("{#if sondeoCaido && !bandejaInaccesible}");
    // 3. El botón no ofrece un acto imposible.
    expect(fuente).toContain("disabled={bandejaInaccesible}");
  });
});

describe("el sondeo no puede fingir actividad", () => {
  it("la pantalla usa el detector de inactividad para pausarlo", async () => {
    // Guardián por FORMA, no por render: montar la bandeja entera aquí exigiría
    // doblar media docena de cargas, y lo que hay que impedir es concreto —
    // que alguien quite la pausa y el sondeo vuelva a correr indefinidamente.
    //
    // El sondeo de esta pantalla pasa por el `handle` del servidor, dispara el
    // refresco transparente del token y REINICIA el contador de inactividad de
    // Keycloak (30 min en producción). Sin pausa, una pestaña olvidada mantiene
    // la sesión viva hasta el tope absoluto de 10 h con la pantalla
    // desbloqueada: el servidor tiene la política correcta y el cliente la
    // desactiva.
    const { readFileSync } = await import("node:fs");
    // Ruta desde la raíz del proyecto: el `URL` de `import.meta.url` no lo
    // acepta `readFileSync` en este entorno de pruebas.
    const fuente = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.svelte", "utf-8");

    expect(fuente).toContain("vigilarInactividad");
    // Y el umbral tiene que quedar POR DEBAJO del de Keycloak con margen: si
    // fuera mayor, el sondeo seguiría renovando la sesión hasta después de que
    // el servidor debería haberla cerrado.
    const umbral = fuente.match(/INACTIVIDAD_MS\s*=\s*([^;]+);/);
    expect(umbral, "INACTIVIDAD_MS debe estar declarado").toBeTruthy();
    const ms = Function(`"use strict"; return (${umbral[1]});`)();
    expect(ms).toBeGreaterThan(0);
    expect(ms).toBeLessThan(30 * 60 * 1000);
  });
});

describe("radicar un correo elige del catálogo, no escribe a mano", () => {
  it("la dependencia va por CÓDIGO y el tipo documental por su código", async () => {
    // Dos campos de texto libre donde el sistema tiene catálogos cerrados:
    //
    // - **Dependencia competente.** `dest_dept` es un nombre suelto;
    //   `dest_dept_code` es el código del organigrama, que document-service
    //   valida contra tenant-service y con el que denormaliza el nombre. Con el
    //   nombre a mano, el radicado nacido de un correo quedaba SIN vínculo con
    //   la dependencia —no aparecía al repartir por código— y «Jurídica» y
    //   «Juridica» eran dos áreas distintas. Es la misma lección que costó la
    //   corrección entera de la geografía DIVIPOLA.
    // - **Tipo documental.** Escrito a mano no casa con ninguna serie de la
    //   TRD, que es la que gobierna cuánto se conserva el documento.
    //
    // El asistente de `/radicar` siempre ofreció selectores para los dos; esta
    // pantalla, que radica exactamente el mismo tipo de documento, no.
    const { readFileSync } = await import("node:fs");
    const fuente = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.svelte", "utf-8");
    const carga = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.server.js", "utf-8");

    // Los vocabularios se cargan del backend, no se inventan en el cliente.
    expect(carga).toContain("/dependencias?activo=true");
    expect(carga).toContain("/tipos-documentales?activo=true");

    // Y se ELIGEN.
    expect(fuente).toContain("bind:value={formulario.dest_dept_code}");
    expect(fuente).toContain("bind:value={formulario.doc_class}");
    expect(fuente).not.toContain("bind:value={formulario.dest_dept}");

    // Control positivo del `not`: si los campos desaparecieran del formulario,
    // la aserción de arriba pasaría igual y no quedaría nada que comprobar.
    expect(fuente).toContain("Dependencia competente");
    expect(fuente).toContain("Tipo documental");
  });

  it("una dependencia que no casa NO se preselecciona", async () => {
    // `email_cuenta.dependencia_destino` es texto libre heredado, así que la
    // preselección se hace casando por nombre. Cuando no casa, el desplegable
    // tiene que quedarse vacío: preseleccionar la primera de la lista pondría
    // un destino que nadie eligió en un acto que no se deshace.
    const { readFileSync } = await import("node:fs");
    const fuente = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.svelte", "utf-8");
    expect(fuente).toContain("codigoPorNombre");
    expect(fuente).toMatch(/return hallado \? String\(hallado\.codigo \?\? hallado\.id\) : "";/);
  });
});

describe("el buzón tiene puerta", () => {
  it("hay una entrada de menú a /correo con el permiso de la bandeja", async () => {
    // La bandeja se gatea con `PERM_RADI` a propósito —quien trabaja el correo
    // recibido es la unidad de correspondencia—, pero la ÚNICA entrada a
    // `/correo/{id}` era un botón dentro de `/admin/correo`, que exige
    // `USUA_PERM_ADMIN`. El funcionario al que la pantalla está dirigida no
    // podía llegar a ella ni conocer el identificador de su buzón: capacidad
    // construida, gateada para su público y sin puerta.
    const { NAV_ITEMS } = await import("$lib/utils/navItems.js");
    const correo = NAV_ITEMS.find((i) => i.href === "/correo");
    expect(correo, "falta la entrada de menú a /correo").toBeTruthy();
    expect(correo.permission).toBe("PERM_RADI");

    const { existsSync } = await import("node:fs");
    expect(existsSync("src/routes/(app)/correo/+page.svelte")).toBe(true);
    expect(existsSync("src/routes/(app)/correo/+page.server.js")).toBe(true);
  });
});
