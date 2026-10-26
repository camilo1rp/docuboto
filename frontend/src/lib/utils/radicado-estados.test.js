// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El vocabulario de estados de un radicado lo fija el backend, no esta copia.
 *
 * `anulado` —el estado más consecuente de todos: el documento queda sin efecto y
 * su número se conserva— **no estaba en el mapa de etiquetas**, así que se
 * pintaba crudo; y dos guardas de la bandeja comparaban el estado con
 * `"cancelled"`, que NO es un estado de radicado sino de paso de flujo. Las dos
 * guardas eran inertes: la de anulación ofrecía «Anular» sobre un radicado ya
 * anulado —botón que el backend rechaza con 409— y la de la cadena de firmas no
 * excluía nada. Y el test de esta última pasaba en verde porque usaba el mismo
 * valor equivocado: un test que codifica el valor malo es peor que su ausencia.
 *
 * Este censo lee del FUENTE del servicio los estados que se escriben de verdad
 * en `radicados.status`, en vez de confiar en que alguien se acuerde.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { RADICADO_ESTADO_LABEL, RADICADO_ESTADO_CHIP } from "./radicado.js";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SERVICIO = resolve(AQUI, "../../../../services/document-service");

function archivos(dir) {
  /** @type {string[]} */
  const salida = [];
  // `withFileTypes` decide por el dirent y no por `stat`: el servicio tiene un
  // enlace simbólico (CLAUDE.md) que no resuelve dentro del contenedor, y
  // seguirlo reventaba el censo entero antes de leer un solo fichero.
  for (const entrada of readdirSync(dir, { withFileTypes: true })) {
    const ruta = join(dir, entrada.name);
    if (entrada.isDirectory()) {
      if (entrada.name === "__pycache__") continue;
      salida.push(...archivos(ruta));
    } else if (entrada.isFile() && (ruta.endsWith(".py") || ruta.endsWith(".sql"))) {
      salida.push(ruta);
    }
  }
  return salida;
}

/**
 * Estados que el servicio ESCRIBE en `radicados.status`.
 *
 * Se buscan las dos formas en que un estado llega a esa columna: el `DEFAULT`
 * de la migración que crea la tabla y los `SET status='…'` de los repositorios.
 * Leer las comparaciones (`status = '…'`) traería estados de otras tablas.
 */
function estadosDelBackend() {
  const encontrados = new Set();
  for (const ruta of archivos(SERVICIO)) {
    const fuente = readFileSync(ruta, "utf8");
    // Acotado a sentencias que tocan `radicados`. Bastaba con «el fichero
    // menciona radicados» para colar `failed`/`processing`/`success`, que son
    // estados del JOB de carga masiva y de otra tabla: un censo demasiado laxo
    // exige etiqueta para estados que esta pantalla nunca verá.
    for (const m of fuente.matchAll(/UPDATE\s+radicados\b([\s\S]{0,400}?)(?:;|"|'\s*\))/gi)) {
      for (const s of m[1].matchAll(/status\s*=\s*'([a-z_]+)'/gi)) encontrados.add(s[1]);
    }
    if (ruta.endsWith("001_create_documents.sql")) {
      const m = fuente.match(/status\s+VARCHAR\(\d+\)[^\n]*DEFAULT\s+'([a-z_]+)'/i);
      if (m) encontrados.add(m[1]);
    }
  }
  return encontrados;
}

describe("estados de radicado — la UI sigue al servicio", () => {
  const backend = estadosDelBackend();

  it("el censo lee algo: un guardián vacío aprobaría por vacuidad", () => {
    // Con la ruta mal, `backend` sería vacío y todo lo de abajo pasaría sin
    // haber comparado nada. Es el control que faltó la primera vez.
    expect(backend.size).toBeGreaterThanOrEqual(2);
    expect([...backend]).toContain("registered");
  });

  it("todo estado que el servicio escribe tiene etiqueta y chip", () => {
    const sinEtiqueta = [...backend].filter((e) => !(e in RADICADO_ESTADO_LABEL));
    const sinChip = [...backend].filter((e) => !(e in RADICADO_ESTADO_CHIP));
    expect(sinEtiqueta).toEqual([]);
    expect(sinChip).toEqual([]);
  });

  it("«anulado» es un estado real y está traducido", () => {
    // Control explícito del defecto: es el que faltaba, y el que las guardas
    // tenían que mirar.
    expect([...backend]).toContain("anulado");
    expect(RADICADO_ESTADO_LABEL.anulado).toBe("Anulado");
  });

  it("«cancelled» NO es un estado de radicado", () => {
    // Pertenece a los pasos de flujo. Dos guardas de la bandeja lo comparaban
    // con el estado del radicado, y por eso no excluían nada.
    expect([...backend]).not.toContain("cancelled");
    expect(Object.keys(RADICADO_ESTADO_LABEL)).not.toContain("cancelled");
  });
});
