// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-paridad.test.js — Paridad backend ↔ BFF del PINAR (AREA-12-03).
 *
 * El plan de pruebas decía «36 endpoints, la interfaz consume 6» y era falso en
 * las dos mitades: son 30, y el desfase se cerró en agosto. Una cifra a mano
 * envejece en silencio; esto la deriva. Extrae las rutas de `pinar.py`
 * (`@router.<verbo>`) y exige que cada una tenga puerta en el BFF: un proxy
 * `api/**\/+server.js` que exporte ese verbo sobre esa ruta, o el `load` de las
 * páginas SSR (solo lectura). Un endpoint sin puerta debe declararse en
 * `SIN_PUERTA` con su motivo — hoy no hay ninguno.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const ROUTER = resolve(AQUI, "../../../../../../services/tenant-service/app/routers/pinar.py");

/** `"VERBO /ruta/{}"` → motivo. */
const SIN_PUERTA = /** @type {Record<string, string>} */ ({});

const norm = (ruta) =>
  ruta
    .replace(/\$\{[^}]*\}/g, "{}")
    .replace(/\{[a-z_]+\}/g, "{}")
    .replace(/\?.*$/, "");

function backend() {
  const src = readFileSync(ROUTER, "utf8");
  return [...src.matchAll(/@router\.(get|post|patch|put|delete)\(\s*"([^"]+)"/g)].map(
    ([, v, r]) => `${v.toUpperCase()} ${norm(r)}`,
  );
}

function archivos(dir) {
  /** @type {string[]} */
  const out = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const r = join(dir, e.name);
    if (e.isDirectory()) out.push(...archivos(r));
    else if (e.name === "+server.js" || e.name === "+page.server.js") out.push(r);
  }
  return out;
}

/**
 * Lecturas que la pantalla refresca SIN recargar (botones «Actualizar»): el `load`
 * SSR no basta, necesitan su proxy BFF. Borrarlo rompe el botón en silencio.
 */
const PROXY_OBLIGATORIO = ["GET /planes/{}/tablero"];

function bff({ soloProxies = false } = {}) {
  /** @type {Set<string>} */
  const puertas = new Set();
  for (const f of archivos(AQUI)) {
    if (soloProxies && !f.endsWith("+server.js")) continue;
    const src = readFileSync(f, "utf8");
    const esProxy = f.endsWith("+server.js");
    const verbos = new Set();
    if (esProxy) {
      for (const m of src.matchAll(
        /export (?:const|async function) (GET|POST|PATCH|PUT|DELETE)\b/g,
      ))
        verbos.add(m[1]);
      for (const m of src.matchAll(/method: "(GET|POST|PATCH|PUT|DELETE)"/g)) verbos.add(m[1]);
    } else {
      verbos.add("GET"); // los `load` SSR solo leen
    }
    // Rutas aguas arriba: literales que cuelgan de /pinar o los `path:` de la fábrica.
    const rutas = [
      ...src.matchAll(/\/pinar((?:[^"'`\s$]|\$\{[^}]*\})+)/g),
      ...src.matchAll(/[`"'](\/(?:planes|proyectos|ejes|objetivos|aspectos)[^"'`\s]*)[`"']/g),
    ].map((m) => norm(m[1]));
    for (const v of verbos) for (const r of rutas) puertas.add(`${v} ${r}`);
  }
  return puertas;
}

describe("PINAR: cada endpoint del backend tiene puerta en el BFF", () => {
  const rutas = backend();
  const puertas = bff();

  it("el censo no está vacío (anti-vacuidad)", () => {
    expect(rutas.length).toBeGreaterThanOrEqual(30);
    expect(puertas.size).toBeGreaterThan(20);
  });

  it("ningún endpoint queda sin puerta salvo los declarados", () => {
    const faltan = rutas.filter((r) => !puertas.has(r) && !(r in SIN_PUERTA));
    expect(faltan).toEqual([]);
  });

  it("las lecturas con botón «Actualizar» conservan su proxy BFF", () => {
    const proxies = bff({ soloProxies: true });
    expect(PROXY_OBLIGATORIO.filter((r) => !proxies.has(r))).toEqual([]);
  });

  it("toda mutación tiene proxy (el load SSR solo lee)", () => {
    const proxies = bff({ soloProxies: true });
    const sinProxy = rutas.filter(
      (r) => !r.startsWith("GET ") && !proxies.has(r) && !(r in SIN_PUERTA),
    );
    expect(sinProxy).toEqual([]);
  });

  it("la lista de excepciones no esconde endpoints que ya tienen puerta", () => {
    const obsoletas = Object.keys(SIN_PUERTA).filter((r) => puertas.has(r));
    expect(obsoletas).toEqual([]);
  });
});
