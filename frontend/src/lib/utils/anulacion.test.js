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
 * anulacion.test.js — Fuente única de mapeos de estado de anulación
 * (M12, revisión UX Fase 0).
 *
 * Cubre:
 *  - Forma esperada de ANULACION_ESTADO_CHIP / ANULACION_ESTADO_LABEL.
 *  - Que `/anulaciones` y `/bandeja` IMPORTAN desde este módulo único en vez
 *    de volver a declarar el objeto localmente — la regresión concreta que
 *    motivó M12 (una copia queda desactualizada cuando el backend suma un
 *    estado nuevo).
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { ANULACION_ESTADO_CHIP, ANULACION_ESTADO_LABEL } from "./anulacion.js";

// jsdom (entorno de test) sustituye el `URL` global por el suyo propio, que
// no es reconocido por `node:url` (`new URL(...)` construido con ese global
// hace fallar `fileURLToPath`). Resolvemos rutas con `node:path` a partir de
// este mismo archivo en vez de construir una URL relativa.
const HERE = dirname(fileURLToPath(import.meta.url));

describe("ANULACION_ESTADO_CHIP / ANULACION_ESTADO_LABEL", () => {
  it("cubre los tres estados conocidos del backend", () => {
    for (const estado of ["solicitada", "aprobada", "rechazada"]) {
      expect(ANULACION_ESTADO_CHIP[estado]).toBeTruthy();
      expect(ANULACION_ESTADO_LABEL[estado]).toBeTruthy();
    }
  });
});

describe("M12 — fuente única (sin copias locales)", () => {
  const PAGES = [
    "../../routes/(app)/anulaciones/+page.svelte",
    "../../routes/(app)/bandeja/+page.svelte",
  ];

  it.each(PAGES)("%s importa las constantes desde $lib/utils/anulacion.js", (relPath) => {
    const path = resolve(HERE, relPath);
    const source = readFileSync(path, "utf-8");
    expect(source).toMatch(
      /import\s*\{\s*ANULACION_ESTADO_CHIP,\s*ANULACION_ESTADO_LABEL\s*\}\s*from\s*"\$lib\/utils\/anulacion\.js";/,
    );
  });

  it.each(PAGES)("%s no vuelve a declarar el objeto localmente", (relPath) => {
    const path = resolve(HERE, relPath);
    const source = readFileSync(path, "utf-8");
    expect(source).not.toMatch(/const ANULACION_ESTADO_CHIP\s*=/);
    expect(source).not.toMatch(/const ANULACION_ESTADO_LABEL\s*=/);
  });
});
