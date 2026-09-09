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
 * Los estados de transferencia de la UI se DERIVAN del servicio, no se copian.
 *
 * El defecto que cierra dejó el módulo entero inoperable: la pantalla comparaba
 * el estado contra `"pendiente"`, que no existe en ninguna parte del sistema.
 * Una transferencia recién creada nace `preparada`, así que **ningún botón de
 * transición aparecía jamás** — ni Enviar, ni Rechazar, ni Recibir. Una
 * transferencia se podía crear y ya no se podía mover, y el fallo es mudo: la
 * fila se pinta, el estado se ve, y lo único que falta son unas acciones cuya
 * ausencia se lee como «aquí no toca».
 *
 * El mismo estado inventado estaba en el filtro del listado y en la allowlist
 * del proxy, que además rechazaba `preparada` con 422 — el filtro útil.
 *
 * Este control lee `_VALID` del propio servicio: si mañana el ciclo cambia en
 * el backend, la copia del frontend deja de coincidir y el test lo dice, en vez
 * de que se descubra pulsando un botón que no está.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ESTADOS_TRANSFERENCIA,
  ESTADO_TRANSFERENCIA_MAP,
  TRANSICIONES_TRANSFERENCIA,
  admiteTransicion,
} from "./transferencia.js";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SERVICIO = resolve(
  AQUI,
  "../../../../services/archive-service/app/services/transferencia_service.py",
);

/** `_VALID` del servicio, leído del fuente. */
function transicionesDelBackend() {
  const fuente = readFileSync(SERVICIO, "utf8");
  const bloque = fuente.slice(fuente.indexOf("_VALID = {"));
  const cuerpo = bloque.slice(0, bloque.indexOf("\n}"));
  /** @type {Record<string, string[]>} */
  const salida = {};
  const linea = /"([a-z_]+)":\s*(set\(\)|\{([^}]*)\})/g;
  let m;
  while ((m = linea.exec(cuerpo)) !== null) {
    const destinos = m[3] ? [...m[3].matchAll(/"([a-z_]+)"/g)].map((d) => d[1]) : [];
    salida[m[1]] = destinos.sort();
  }
  return salida;
}

describe("estados de transferencia — la UI sigue al servicio", () => {
  const backend = transicionesDelBackend();

  it("el censo lee algo: un guardián vacío aprobaría por vacuidad", () => {
    // Con la ruta mal, `backend` sería `{}` y todo lo de abajo pasaría sin
    // haber comparado nada.
    expect(Object.keys(backend).length).toBeGreaterThanOrEqual(4);
  });

  it("los estados son exactamente los del ciclo del backend", () => {
    expect([...ESTADOS_TRANSFERENCIA].sort()).toEqual(Object.keys(backend).sort());
  });

  it("«pendiente» no es un estado de transferencia", () => {
    // Control explícito del defecto: era el que rompía las tres acciones.
    expect(ESTADOS_TRANSFERENCIA).not.toContain("pendiente");
    expect(Object.keys(ESTADO_TRANSFERENCIA_MAP)).not.toContain("pendiente");
  });

  it("las transiciones coinciden una a una con `_VALID`", () => {
    for (const [estado, destinos] of Object.entries(backend)) {
      expect([...(TRANSICIONES_TRANSFERENCIA[estado] ?? [])].sort(), `estado ${estado}`).toEqual(
        destinos,
      );
    }
  });

  it("cada estado tiene etiqueta: ninguno se muestra en crudo", () => {
    // Un estado sin entrada en el mapa se pinta con su nombre técnico, que es
    // lo que pasaba con `preparada` mientras la UI creía en `pendiente`.
    for (const estado of ESTADOS_TRANSFERENCIA) {
      expect(ESTADO_TRANSFERENCIA_MAP[estado]?.label, `falta etiqueta de ${estado}`).toBeTruthy();
    }
  });

  it("una transferencia recién creada SÍ se puede enviar", () => {
    // El caso exacto que no funcionaba en el navegador.
    expect(admiteTransicion("preparada", "enviada")).toBe(true);
  });

  it("y una recibida ya no se mueve", () => {
    // Gemelo: la corrección no puede consistir en ofrecerlo todo siempre.
    expect(admiteTransicion("recibida", "enviada")).toBe(false);
    expect(admiteTransicion("rechazada", "recibida")).toBe(false);
  });
});
