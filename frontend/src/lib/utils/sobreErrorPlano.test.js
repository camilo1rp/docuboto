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
 * Guardián: nadie vuelve a leer `error`/`detail` del PRIMER nivel de un cuerpo
 * de respuesta del gateway.
 *
 * El backend levanta sus errores con `HTTPException(detail={...})` —unos 500
 * sitios repartidos por los once servicios—, así que FastAPI serializa el sobre
 * del proyecto DENTRO de su propio `detail`:
 *
 *     {"detail": {"error": "not_a_reviewer", "detail": "El actor no…", "status": 403}}
 *
 * Leer `err.detail` de ahí no devuelve un texto: devuelve un OBJETO. En un aviso
 * de error eso se pinta literalmente como «[object Object]», que es lo que veía
 * la persona al pedir un visto bueno sin estar en la cadena de revisores —donde
 * el backend explicaba exactamente qué pasaba—. Y leer `err.error` devuelve
 * `undefined`, así que toda rama que distinga un código de error concreto
 * (`no_active_step`, `validation_error`, …) se queda muerta sin avisar.
 *
 * Por eso el acceso pasa siempre por `mensajeError`/`codigoError`, que aceptan
 * las dos formas: la plana la fabrican de verdad los proxies BFF, la anidada la
 * produce el backend.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PROPIO = path.join(SRC, "lib", "utils", "apiError.js");

// Nombres que en este proyecto SIEMPRE contienen un cuerpo de respuesta del
// gateway (o de un proxy BFF que lo reenvía tal cual).
const CUERPOS = "err|error|body|data|result|resData|responseData|payload|cuerpo";
const PROHIBIDO = new RegExp(
  String.raw`\b(?:${CUERPOS})\??\.(?:detail|error)\s*(?:\?\?|\|\||===|!==)`,
  "g",
);

function fuentes(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir, { withFileTypes: true })) {
    const completa = path.join(dir, entrada.name);
    if (entrada.isDirectory()) {
      salida.push(...fuentes(completa));
    } else if (/\.(svelte|js)$/.test(entrada.name) && !entrada.name.endsWith(".test.js")) {
      if (completa !== PROPIO) salida.push(completa);
    }
  }
  return salida;
}

describe("el sobre de error nunca se lee plano", () => {
  it("recorre de verdad el árbol de fuentes", () => {
    expect(fuentes(SRC).length).toBeGreaterThan(150);
  });

  it("no queda ningún acceso directo a `error`/`detail` de primer nivel", () => {
    const infractores = [];
    for (const ruta of fuentes(SRC)) {
      const texto = readFileSync(ruta, "utf8");
      const lineas = texto.split("\n");
      for (const m of texto.matchAll(PROHIBIDO)) {
        const linea = texto.slice(0, m.index).split("\n").length;
        // Excepción EXPLÍCITA y local: un sobre de error que no es el nuestro
        // (p. ej. el de OAuth2 que devuelve Keycloak). Se marca en las líneas
        // inmediatamente anteriores para que quede escrito por qué, en vez de
        // en una lista de archivos que nadie vuelve a leer.
        if (lineas.slice(Math.max(0, linea - 5), linea).some((l) => l.includes("sobre-ajeno"))) {
          continue;
        }
        infractores.push(`${path.relative(SRC, ruta)}:${linea} → ${m[0]}`);
      }
    }
    expect(infractores).toEqual([]);
  });
});
