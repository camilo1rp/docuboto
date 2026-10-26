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
 * Un total falso es peor que ningún total: quien lo lee deja de buscar.
 *
 * El historial de envíos pinta las últimas 50 notificaciones y el backend no
 * expone un total. La tabla recibía `totalCount={history.length}` y el resumen
 * decía «1–50 de 50» habiendo 420 en la base — mientras el propio comentario
 * del módulo afirmaba que la pantalla «nunca finge una paginación que el
 * backend no ofrece».
 *
 * `disableNav` no servía como señal: `/anulaciones` también lo usa y su total
 * SÍ es real. Hace falta decirlo aparte.
 */
import { describe, it, expect } from "vitest";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const { readFileSync } = process.getBuiltinModule("fs");
const AQUI = dirname(fileURLToPath(import.meta.url));
const leer = (rel) => readFileSync(resolve(AQUI, rel), "utf-8");

describe("DataTable — el resumen no afirma un total que no conoce", () => {
  const tabla = leer("./DataTable.svelte");

  it("tiene la señal explícita, y no la deduce de `disableNav`", () => {
    expect(tabla).toContain("export let totalDesconocido = false;");
  });

  it("con el total desconocido el resumen no dice «de N»", () => {
    // La rama tiene que existir ANTES del «start–end de totalCount», que es la
    // que afirma completitud.
    const iRama = tabla.indexOf("{#if totalDesconocido}");
    const iRango = tabla.indexOf("{start}–{end} de {totalCount}");
    expect(iRama).toBeGreaterThan(-1);
    expect(iRango).toBeGreaterThan(iRama);
    expect(tabla).toContain("en esta consulta");
  });

  it("el historial de envíos la declara", () => {
    const pantalla = leer("../../../routes/(app)/admin/notificaciones/+page.svelte");
    expect(pantalla).toContain("totalDesconocido");
  });

  it("`/anulaciones` NO la declara: su total sí es real", () => {
    const pantalla = leer("../../../routes/(app)/anulaciones/+page.svelte");
    expect(pantalla).not.toContain("totalDesconocido");
  });
});
