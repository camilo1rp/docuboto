// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * La bandeja no puede ofrecer FIRMAR sobre un turno de visto bueno.
 *
 * El gate del backend estaba puesto solo en una dirección, y en la inocua:
 * `dar_visto_bueno` rechazaba los turnos de firma, pero firmar un turno de
 * visto bueno no lo impedía nada. La pantalla lo facilitaba activamente —
 * casilla de firma en lote en toda fila y botón «Ver / Firmar» sobre un visto
 * bueno—, así que bastaba un «seleccionar todas» para comprometer a la entidad
 * en bloque con actos que sus autores entendían como una revisión.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const PAGINA = readFileSync("src/routes/(app)/firmas/+page.svelte", "utf-8");
const TIPOS = readFileSync("src/lib/types/api.js", "utf-8");

describe("la firma en lote solo alcanza turnos de firma", () => {
  it("existe un subconjunto «firmables» y es el que usa seleccionar-todas", () => {
    expect(PAGINA).toMatch(/\$: firmables = pendientes\.filter\(\(p\) => p\.rol === "firma"\)/);
    // toggleAll recorre firmables, no pendientes: si recorriera pendientes,
    // «seleccionar todas» volvería a meter los vistos buenos en el lote.
    expect(PAGINA).not.toMatch(/pendientes\.forEach\(\(p\) => selectedIds\.(add|delete)/);
    expect(PAGINA).toMatch(/firmables\.forEach\(\(p\) => selectedIds\.add/);
  });

  it("la casilla por fila solo se pinta en turnos de firma", () => {
    expect(PAGINA).toMatch(/\{#if hasFirma && row\.rol === "firma"\}/);
  });

  it("la barra de seleccionar-todas no aparece si no hay nada firmable", () => {
    expect(PAGINA).toMatch(/\{#if !loading && firmables\.length > 0 && hasFirma\}/);
  });
});

describe("la acción nombra el acto real del turno", () => {
  it("un visto bueno no invita a firmar", () => {
    expect(PAGINA).toMatch(/row\.rol === "firma" \? "Ver \/ Firmar" : "Ver \/ Dar visto bueno"/);
  });

  it("y la etiqueta accesible dice lo mismo que el botón", () => {
    // Un aria-label que dice «firmar» sobre un botón que dice «visto bueno»
    // deja a quien usa lector de pantalla con la descripción peligrosa.
    expect(PAGINA).toMatch(/Ver y dar visto bueno a \$\{row\.titulo/);
  });
});

describe("el contrato del cliente declara el campo", () => {
  it("SignaturePendingItem tiene rol", () => {
    // El backend lo mandaba desde que `PendienteItem` lo declara; el typedef se
    // quedó atrás, así que la pantalla no podía leerlo sin que svelte-check
    // protestara — y sin leerlo no hay forma de distinguir los dos actos.
    const bloque = TIPOS.slice(
      TIPOS.indexOf("@typedef {object} SignaturePendingItem"),
      TIPOS.indexOf("@typedef {object} SignatureTurn"),
    );
    expect(bloque).toMatch(/@property \{'visto_bueno'\|'firma'\} rol/);
  });
});

describe("el revisor puede ver lo que se le pide aprobar", () => {
  // El detalle mostraba identificador, tipo y fecha, y ni el texto ni una forma
  // de llegar a él: se aprobaba a ciegas. El circuito ancla la aprobación al
  // hash del cuerpo —«aprobó ESTE texto»—, y esa garantía es hueca si quien
  // aprueba nunca lo vio.
  it("hay una acción para ver el documento en el detalle", () => {
    expect(PAGINA).toMatch(/Ver documento/);
    expect(PAGINA).toMatch(/on:click=\{verDocumento\}/);
  });

  it("solo para borradores, que es lo único previsualizable", () => {
    expect(PAGINA).toMatch(/\{#if selectedDetail\.objeto_tipo === "borrador"\}/);
  });

  it("reutiliza el proxy existente en vez de abrir un camino propio", () => {
    expect(PAGINA).toMatch(
      /fetch\(`\/borradores\/api\/\$\{selectedDetail\.objeto_id\}\/previsualizar`/,
    );
  });

  it("un 403 se explica, no se queda mudo", () => {
    // El endpoint exige PERM_RADI. Un revisor que solo tenga PERM_FIRMA tiene
    // que saber POR QUÉ no puede leer lo que se le pide aprobar — descubrir el
    // límite estrellándose contra un botón que no hace nada es lo peor de los
    // dos mundos.
    expect(PAGINA).toMatch(/res\.status === 403/);
    expect(PAGINA).toMatch(/tu turno no basta hoy para leerlo/);
  });
});
