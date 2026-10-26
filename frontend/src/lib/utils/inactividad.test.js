// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * inactividad.test.js — El detector de inactividad REAL.
 *
 * **El defecto que corrige.** Keycloak cierra la sesión a los 30 min sin
 * actividad (`ssoSessionIdleTimeout`, producción). Pero la bandeja de correo
 * sondea cada 12 s mientras la pestaña esté abierta, cada sondeo pasa por el
 * `handle` del servidor, dispara el refresco transparente del token y
 * **reinicia ese contador**. Una pestaña olvidada en una mesa mantenía la
 * sesión viva hasta el tope absoluto de 10 h, con la pantalla desbloqueada: el
 * servidor tenía la política correcta y el cliente la desactivaba.
 *
 * Esto NO cierra la sesión desde el navegador —sería un control falso, se salta
 * desactivando JavaScript—: deja de fingir actividad para que el control del
 * servidor funcione.
 *
 * Lo que se prueba, y por qué cada cosa:
 *
 *  - **Ocultar la pestaña NO es actividad.** Irse a otra aplicación no debe
 *    renovar nada; volver a mirarla sí.
 *  - **El aviso de inactivo se emite UNA vez**, no en cada tic: quien escucha
 *    para un temporizador, y pararlo dos veces no es idempotente en todas las
 *    implementaciones.
 *  - **La limpieza desengancha de verdad**: un detector que sobrevive a su
 *    página sigue disparando sobre componentes ya destruidos.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { vigilarInactividad } from "./inactividad.js";

const MS = 1000;

describe("detector de inactividad", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("avisa al cruzar el umbral sin interacción", () => {
    const alInactivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alInactivo });

    expect(alInactivo).not.toHaveBeenCalled();
    vi.advanceTimersByTime(MS + 1);
    expect(alInactivo).toHaveBeenCalledTimes(1);

    limpiar();
  });

  it("una interacción REAL reinicia la cuenta", () => {
    const alInactivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alInactivo });

    vi.advanceTimersByTime(MS - 100);
    document.dispatchEvent(new Event("keydown"));
    vi.advanceTimersByTime(MS - 100);

    expect(alInactivo).not.toHaveBeenCalled();
    limpiar();
  });

  it("al volver la actividad avisa, y solo si antes estaba inactivo", () => {
    const alActivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alActivo });

    // Interacción sin haber estado inactivo: no anuncia una reanudación que no
    // ocurrió.
    document.dispatchEvent(new Event("pointerdown"));
    expect(alActivo).not.toHaveBeenCalled();

    vi.advanceTimersByTime(MS + 1);
    document.dispatchEvent(new Event("pointerdown"));
    expect(alActivo).toHaveBeenCalledTimes(1);

    limpiar();
  });

  it("OCULTAR la pestaña no cuenta como actividad", () => {
    const alInactivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alInactivo });

    // El reloj se adelanta CASI hasta el umbral ANTES de ocultar, y solo un poco
    // después. Así, si ocultar reiniciara la cuenta, no daría tiempo a cruzar
    // el umbral y el aviso no llegaría. Adelantando el umbral entero después de
    // ocultar, el test pasaba igual con y sin reinicio — medía otra cosa.
    vi.advanceTimersByTime(MS - 100);
    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    vi.advanceTimersByTime(200);

    // Si ocultarla reiniciara la cuenta, irse a otra aplicación renovaría la
    // sesión — exactamente lo contrario de lo que hace falta.
    expect(alInactivo).toHaveBeenCalledTimes(1);
    limpiar();
  });

  it("VOLVER a la pestaña sí cuenta", () => {
    const alActivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alActivo });

    vi.advanceTimersByTime(MS + 1);
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));

    expect(alActivo).toHaveBeenCalledTimes(1);
    limpiar();
  });

  it("el aviso de inactivo se emite UNA sola vez", () => {
    const alInactivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alInactivo });

    vi.advanceTimersByTime(MS * 10);

    expect(alInactivo).toHaveBeenCalledTimes(1);
    limpiar();
  });

  it("la limpieza desengancha: ya no dispara nada", () => {
    const alInactivo = vi.fn();
    const limpiar = vigilarInactividad({ msSinActividad: MS, alInactivo });

    limpiar();
    vi.advanceTimersByTime(MS * 5);
    document.dispatchEvent(new Event("keydown"));

    expect(alInactivo).not.toHaveBeenCalled();
  });
});
