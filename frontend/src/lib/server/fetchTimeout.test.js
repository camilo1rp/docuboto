// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * fetchTimeout.test.js — Tiempo límite de RESPUESTA del SSR hacia el backend.
 *
 * Los dos comportamientos que hay que separar, y que un test descuidado
 * confunde:
 *  1. Un upstream que **no contesta** debe cortarse.
 *  2. Un upstream que contesta y luego envía un cuerpo enorme (ZIP de
 *     exportación, AIP, FUID) **no** debe cortarse — por eso el temporizador se
 *     cancela al llegar las cabeceras, no al terminar la descarga.
 *
 * El segundo es el que justifica no haber usado `AbortSignal.timeout` a secas,
 * así que se prueba explícitamente: se comprueba que el `signal` sigue sin
 * abortar mucho después del plazo, con el cuerpo aún sin leer.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { instalarTiempoLimite, _reiniciarParaTests } from "./fetchTimeout.js";

const API = "http://api-gateway:8080/api/v1";

/** @type {typeof globalThis.fetch} */
let fetchOriginal;

beforeEach(() => {
  fetchOriginal = globalThis.fetch;
  _reiniciarParaTests();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  globalThis.fetch = fetchOriginal;
});

describe("instalarTiempoLimite", () => {
  it("aborta cuando el upstream acepta la conexión y no responde", async () => {
    // El fallo real que esto cubre: el gateway arrancando, saturado o con un
    // upstream colgado. Sin plazo, esta promesa no se resuelve NUNCA y el SSR
    // se queda con la petición del usuario ocupada.
    globalThis.fetch = vi.fn(
      (_entrada, init) =>
        new Promise((_resolver, rechazar) => {
          init.signal.addEventListener("abort", () => rechazar(init.signal.reason));
        }),
    );
    instalarTiempoLimite([API], 30_000);

    const promesa = globalThis.fetch(`${API}/auth/me`);
    const capturado = promesa.catch((e) => e);

    await vi.advanceTimersByTimeAsync(30_000);

    expect((await capturado).name).toBe("TimeoutError");
  });

  it("NO aborta la descarga del cuerpo una vez llegaron las cabeceras", async () => {
    // Este es el test que separa esta implementación de un
    // `AbortSignal.timeout` corriente: con aquél, el ZIP de exportación de un
    // expediente moriría a los 30 s de empezar por el mero hecho de ser grande.
    /** @type {AbortSignal} */
    let señalVista;
    globalThis.fetch = vi.fn(async (_entrada, init) => {
      señalVista = init.signal;
      return new Response("cabeceras ya enviadas");
    });
    instalarTiempoLimite([API], 30_000);

    const respuesta = await globalThis.fetch(`${API}/expedientes/x/export`);
    expect(respuesta.ok).toBe(true);

    // Muy pasado el plazo, con el cuerpo aún sin consumir.
    await vi.advanceTimersByTimeAsync(120_000);

    expect(señalVista.aborted).toBe(false);
  });

  it("respeta el `signal` de quien llama y no le impone el suyo", async () => {
    // La vía para quien necesita más que el tope por defecto: el envoltorio no
    // debe recortar lo que la propia llamada ya acota.
    const propio = new AbortController();
    globalThis.fetch = vi.fn(async (_entrada, init) => {
      expect(init.signal).toBe(propio.signal);
      return new Response("ok");
    });
    instalarTiempoLimite([API], 30_000);

    await globalThis.fetch(`${API}/knowledge/search`, { signal: propio.signal });

    expect(globalThis.fetch).toBeDefined();
  });

  it("no toca las URL ajenas al backend", async () => {
    let señalVista = "sin tocar";
    globalThis.fetch = vi.fn(async (_entrada, init) => {
      señalVista = init?.signal ?? "sin tocar";
      return new Response("ok");
    });
    instalarTiempoLimite([API], 30_000);

    await globalThis.fetch("http://example.org/algo");

    expect(señalVista).toBe("sin tocar");
  });

  it("es idempotente: dos instalaciones no encadenan dos envoltorios", async () => {
    const espia = vi.fn(async () => new Response("ok"));
    globalThis.fetch = espia;

    instalarTiempoLimite([API], 30_000);
    const trasPrimera = globalThis.fetch;
    instalarTiempoLimite([API], 30_000);

    expect(globalThis.fetch).toBe(trasPrimera);
  });
});
