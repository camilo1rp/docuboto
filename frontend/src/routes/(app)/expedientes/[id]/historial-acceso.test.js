// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * historial-acceso.test.js — Quién leyó y quién descargó (RF-BUS-10).
 *
 * La pestaña «Acceso» tenía un diccionario de etiquetas —`read`, `download`,
 * `verify`— que **no casaba con ninguna acción real** de `audit_log`: todas
 * caían al `humanize`, y la entrada "Descarga" hacía creer que ya se veían las
 * descargas de anexos, que era justo lo que no se veía.
 *
 * Y aunque la entrada llegara, la fila no decía **sobre qué documento**: «X
 * descargó un anexo» sin el número de radicado no responde la pregunta que
 * esta pestaña existe para responder.
 */
import { describe, it, expect } from "vitest";

const { ACCESO_ACCION_LABELS } = await import("./acceso-labels.js");

describe("etiquetas del historial de acceso", () => {
  it("cubren las acciones REALES que escribe el backend, no nombres inventados", () => {
    // Estas son las que de verdad viajan en `audit_log.action`. Antes el mapa
    // usaba `read`/`download`/`verify`, que no existen en ninguna parte.
    for (const accion of [
      "archive.expediente_consultado",
      "archive.indice_consultado",
      "document.radicado_consultado",
      "document.anexo_descargado",
    ]) {
      expect(ACCESO_ACCION_LABELS[accion], accion).toBeTruthy();
    }
  });

  it("la descarga de un anexo tiene su propia etiqueta", () => {
    expect(ACCESO_ACCION_LABELS["document.anexo_descargado"]).toMatch(/descarga/i);
  });

  it("no quedan claves que no correspondan a ninguna acción del backend", () => {
    // Un mapa con claves muertas hace creer que la pantalla muestra cosas que
    // no muestra — que es exactamente lo que pasó con "Descarga".
    for (const clave of Object.keys(ACCESO_ACCION_LABELS)) {
      expect(clave, clave).toMatch(/^(archive|document)\./);
    }
  });
});
