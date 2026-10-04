// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * firma-gate-orden.spec.js — El gate de ORDEN de la firma, contra la pila real
 * (AREA-05-08). Requiere `seed_demo.py` ejecutado: siembra un borrador con un
 * circuito de dos turnos (jefe → secretaria) y cambia su texto en la base tras abrirlo,
 * sin emitir el evento de invalidación (la ventana en la que el gate síncrono es la
 * única defensa; editarlo por la API invalidaría la cadena en segundos y los dos
 * recibirían `409 solicitud_no_pendiente`).
 *
 * El hallazgo: `cadena_service.firmar` comprobaba `documento_modificado` ANTES de
 * la pertenencia y el turno, así que cualquiera con nivel de acceso suficiente
 * aprendía que existe un circuito, que el texto cambió y la versión vigente. Hoy
 * la autorización va primero y el estado del documento después. El código está
 * cubierto con mocks; esto lo ve el sistema levantado, por el BFF del front.
 *
 *  - un usuario FUERA de la cadena (e2e1) -> 403 `no_es_firmante`, y el cuerpo NO
 *    contiene `documento_modificado` ni `version`;
 *  - control positivo: el firmante con turno (jefe) -> 409 `documento_modificado`.
 *    Sin él, un 403 para todos pasaría la primera comprobación sin probar nada.
 *
 * Mutación: mover `await self._gate_documento_vigente(sol)` antes de las guardas de
 * pertenencia en `firmar()` -> el caso del ajeno se pone rojo.
 */
import { test, expect } from "@playwright/test";

import { IDENTIDADES, estadoDe, iniciarSesion } from "./_sesion.js";

const TITULO = /Circuito de dos turnos sobre un borrador/;

/** El id del circuito del borrador, visto desde la bandeja del primer firmante. */
async function circuitoDelBorrador(browser, baseURL) {
  const ctx = await browser.newContext({ baseURL });
  try {
    const page = await ctx.newPage();
    await iniciarSesion(page, { usuario: "jefe" });
    const res = await ctx.request.get("/firmas/api?page=1&size=50");
    expect(res.ok(), "la bandeja del firmante responde").toBeTruthy();
    const { pendientes } = await res.json();
    const item = pendientes.find((p) => TITULO.test(p.titulo ?? ""));
    expect(item, "seed_demo.py no sembró el circuito sobre el borrador").toBeTruthy();
    // Control positivo, desde la misma sesión del firmante con turno.
    const firma = await ctx.request.post(`/firmas/api/${item.solicitud_id}/sign`, { data: {} });
    return { id: item.solicitud_id, estadoJefe: firma.status(), cuerpoJefe: await firma.text() };
  } finally {
    await ctx.close();
  }
}

test.describe("el gate de orden de la firma", () => {
  test.use({ storageState: estadoDe(IDENTIDADES[0]) });

  test("un ajeno recibe 403 no_es_firmante y no se entera del circuito; el firmante con turno, 409", async ({
    browser,
    request,
  }, testInfo) => {
    const baseURL = testInfo.project.use.baseURL;
    const { id, estadoJefe, cuerpoJefe } = await circuitoDelBorrador(browser, baseURL);

    // Control positivo: quien tiene el turno SÍ ve que el texto cambió.
    expect(estadoJefe).toBe(409);
    expect(cuerpoJefe).toContain("documento_modificado");

    // El ajeno: autorización primero, estado del documento después.
    const ajeno = await request.post(`/firmas/api/${id}/sign`, { data: {} });
    const texto = await ajeno.text();
    expect(ajeno.status()).toBe(403);
    expect(texto).toContain("no_es_firmante");
    expect(texto).not.toContain("documento_modificado");
    expect(texto).not.toMatch(/\bversion\b/i);
  });
});
