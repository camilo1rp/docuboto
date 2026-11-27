// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * securityHeaders.test.js — Cabeceras transversales del SSR.
 *
 * El caso que motiva `no-store`: puesto de ventanilla compartido, el
 * funcionario siguiente pulsa Atrás y el navegador sirve del disco la bandeja
 * del anterior — con sus radicados y, según la habilitación de aquél, con
 * documentos que este no puede consultar. El backend filtra bien; el dato se ve
 * porque no se vuelve a pedir.
 */
import { describe, it, expect } from "vitest";
import { aplicarCabecerasDeSeguridad } from "./securityHeaders.js";

/** @param {string} tipo @param {Record<string,string>} [extra] */
function respuestaCon(tipo, extra = {}) {
  return new Response("x", { headers: { "content-type": tipo, ...extra } });
}

describe("aplicarCabecerasDeSeguridad", () => {
  it("marca no-store el HTML del SSR", () => {
    const r = aplicarCabecerasDeSeguridad(respuestaCon("text/html; charset=utf-8"));
    expect(r.headers.get("cache-control")).toBe("no-store");
  });

  it("marca no-store el JSON de los proxies", () => {
    // Es el material de negocio: la bandeja, el expediente, el listado de
    // radicados. Sin esto, el `bfcache` y el disco los conservan.
    const r = aplicarCabecerasDeSeguridad(respuestaCon("application/json"));
    expect(r.headers.get("cache-control")).toBe("no-store");
  });

  it("respeta la política que la respuesta ya declaró", () => {
    // Hay proxies de descarga que fijan la suya; pisarla a ciegas sería
    // cambiarles el comportamiento desde un sitio que no los conoce.
    const r = aplicarCabecerasDeSeguridad(
      respuestaCon("application/json", { "cache-control": "private, max-age=60" }),
    );
    expect(r.headers.get("cache-control")).toBe("private, max-age=60");
  });

  it("no toca la caché de los estáticos versionados", () => {
    // `_app/immutable/*.js` debe seguir cacheándose: su nombre lleva el hash
    // del contenido. (En producción ni siquiera pasan por aquí.)
    const r = aplicarCabecerasDeSeguridad(respuestaCon("application/javascript"));
    expect(r.headers.get("cache-control")).toBeNull();
  });

  it("emite las cabeceras de seguridad en toda respuesta", () => {
    const r = aplicarCabecerasDeSeguridad(respuestaCon("text/html"));
    expect(r.headers.get("x-content-type-options")).toBe("nosniff");
    expect(r.headers.get("referrer-policy")).toBe("same-origin");
    expect(r.headers.get("x-frame-options")).toBe("DENY");
  });

  it("NO emite HSTS: quién termina TLS lo sabe el proxy, no este proceso", () => {
    // Emitirla en un despliegue de desarrollo por HTTP deja el navegador
    // clavado en HTTPS para ese host, sin nadie escuchando al otro lado.
    const r = aplicarCabecerasDeSeguridad(respuestaCon("text/html"));
    expect(r.headers.get("strict-transport-security")).toBeNull();
  });
});

/**
 * `form-action 'self'` basta: con la identidad emitida por la app (ADR-048) el
 * login y el logout envían solo a este origen. La política no se amplía.
 */
describe("form-action — no se amplía a ningún otro origen", () => {
  it("deja la CSP que declaró SvelteKit tal cual", () => {
    const CSP = "default-src 'self'; form-action 'self'; frame-ancestors 'none'";
    const r = new Response("x", {
      headers: { "content-type": "text/html", "content-security-policy": CSP },
    });
    expect(aplicarCabecerasDeSeguridad(r).headers.get("content-security-policy")).toBe(CSP);
  });
});
