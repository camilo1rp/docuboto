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
 * «¿Esto lo hice yo?» se pregunta con la identidad INTERNA, nunca con el `sub`.
 *
 * Una persona tiene dos UUID en este sistema: el `sub` de Keycloak y su
 * `auth_users.id` dentro del tenant. El backend escribe SIEMPRE el segundo en
 * las columnas de autoría (`creada_por`, `enviada_por`, `decidida_por`,
 * `solicitado_por`, `firmante_id`…). El frontend guardaba solo el primero y
 * comparaba con él, así que toda pregunta de pertenencia daba **siempre falso**
 * — y en silencio, que es lo que la hizo durar:
 *
 *  - El botón «Firmar como …» del acta de una transferencia no aparecía nunca,
 *    ni para quien había preparado, enviado y recibido el traslado. La firma
 *    personal por rol (E17/E06) estaba construida y era inalcanzable.
 *  - En `/firmas`, ningún turno de una cadena se reconocía como propio.
 *  - El aviso de que uno no puede aprobar su propia anulación no se mostraba,
 *    y la persona se encontraba el 409 después de escribir el motivo.
 *
 * `/auth/me` ya resolvía el id interno («Replace keycloak sub with the internal
 * id for coherence with X-User-Id»); el frontend lo tiraba al construir la
 * sesión. Este control censa las pantallas para que no vuelva a compararse el
 * `sub` con un campo de autoría del backend.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { get } from "svelte/store";
import { userId, initSession, clearSession } from "./auth.js";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../../");

/** Campos que el backend rellena con la identidad INTERNA del tenant. */
const CAMPOS_DE_AUTORIA = [
  "creada_por",
  "enviada_por",
  "decidida_por",
  "solicitado_por",
  "resuelto_por",
  "firmante_id",
  "elaborado_por",
  "entregado_por",
  "recibido_por",
  "registered_by",
  "opened_by",
];

function svelteYJs(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir)) {
    const ruta = join(dir, entrada);
    if (statSync(ruta).isDirectory()) salida.push(...svelteYJs(ruta));
    else if (/\.(svelte|js)$/.test(entrada) && !/\.test\.js$/.test(entrada)) salida.push(ruta);
  }
  return salida;
}

describe("identidad interna frente al sub de Keycloak", () => {
  it("la store expone el id interno cuando la sesión lo trae", () => {
    clearSession();
    initSession({
      sub: "keycloak-sub",
      user_id: "interno-1",
      name: "X",
      tenant_slug: "demo",
      permissions: [],
    });
    expect(get(userId)).toBe("interno-1");
    clearSession();
  });

  it("sin `user_id` la store da null, no el sub", () => {
    // Devolver el `sub` como sustituto sería peor que no saber: produciría
    // comparaciones que fallan pareciendo verdaderas comprobaciones.
    clearSession();
    initSession({ sub: "keycloak-sub", name: "X", tenant_slug: "demo", permissions: [] });
    expect(get(userId)).toBeNull();
    clearSession();
  });

  it("ninguna pantalla compara un campo de autoría contra `sub`", () => {
    const sospechosas = [];
    for (const archivo of svelteYJs(SRC)) {
      const fuente = readFileSync(archivo, "utf8");
      // Se ignora el texto de los comentarios: ahí se EXPLICA el defecto.
      const codigo = fuente
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "")
        .replace(/<!--[\s\S]*?-->/g, "");
      for (const campo of CAMPOS_DE_AUTORIA) {
        const patron = new RegExp(
          `${campo}\\s*===?\\s*\\$?sessionUser|\\$?sessionUser[^\\n]{0,40}===?\\s*[^\\n]{0,20}${campo}|${campo}\\s*===?\\s*(sub|userSub)\\b`,
        );
        if (patron.test(codigo)) {
          sospechosas.push(`${archivo.replace(SRC, "src/")} :: ${campo}`);
        }
      }
    }

    expect(
      sospechosas,
      "Estos campos los rellena el backend con `auth_users.id`, no con el `sub` " +
        "de Keycloak: compararlos con `sub` da siempre falso, y en silencio. " +
        "Use la store `userId`. Afectados:\n" +
        sospechosas.join("\n"),
    ).toEqual([]);
  });
});
