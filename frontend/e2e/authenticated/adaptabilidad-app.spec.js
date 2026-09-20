// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * adaptabilidad-app.spec.js — las pantallas de TRABAJO se ajustan al dispositivo.
 *
 * El carril de humo mide lo mismo sobre las cuatro páginas públicas. Estas son
 * las que motivaron el arreglo y las que un funcionario usa todo el día, y solo
 * se pueden mirar con sesión: la pila arriba y credenciales por entorno.
 *
 * El defecto que cierra este fichero lo reportó una persona usando el producto
 * —«las pantallas no usan todo el espacio en escritorio, y algunas muestran
 * tablas recortadas»— con la suite entera en verde. No fue mala suerte: Vitest
 * corre sobre jsdom, que no calcula layout, así que dos mil pruebas no sabían
 * cuántos píxeles ocupa nada. Aquí hay un navegador de verdad.
 *
 * Se recorren los tres anchos que existen: el móvil de quien consulta algo de
 * pie, el portátil donde se reportó el problema y el escritorio donde más
 * espacio se desperdiciaba.
 *
 * HALLAZGO ABIERTO (2026-09-19): cinco pruebas quedan EN ROJO a propósito
 * —`/admin/trd`, `/admin/tvd`, `/admin/usuarios`, `/archivo-fisico` y
 * `/borradores` a 390 px—. El criterio NO se relaja para ponerlas en verde: un
 * verde falso aquí es peor que el defecto, porque este fichero existe justo
 * porque la suite entera estaba verde mientras el producto fallaba. El impacto
 * está medido y es menor de lo que el fallo sugiere: la franja desplazada está
 * VACÍA (se capturó la página arrastrada y solo hay fondo), así que no hay
 * contenido inaccesible sino un área muerta arrastrable. Diagnóstico y lo ya
 * descartado, en `docs/es/roadmap.md`.
 */
import { test, expect } from "@playwright/test";

import { ANCHOS, medir } from "../_adaptabilidad.js";
import { ESTADO_SESION } from "./_sesion.js";

/**
 * Las pantallas de trabajo, con la razón por la que cada una está aquí. La
 * lista se declara —no se descubre del árbol— porque lo que se quiere vigilar
 * es el recorrido real de un funcionario, no todas las rutas que existen.
 */
const PANTALLAS = [
  { ruta: "/dashboard", nombre: "tablero" },
  { ruta: "/bandeja", nombre: "bandeja" }, // el flujo núcleo
  { ruta: "/busqueda", nombre: "búsqueda" },
  { ruta: "/expedientes", nombre: "expedientes" },
  { ruta: "/borradores", nombre: "borradores" }, // estaba acotada a 1100
  { ruta: "/firmas", nombre: "firmas" }, // estaba acotada a 1100
  { ruta: "/anulaciones", nombre: "anulaciones" }, // estaba acotada a 1000
  { ruta: "/envios", nombre: "envíos" },
  { ruta: "/archivo-fisico", nombre: "archivo físico" }, // la tabla más ancha
  { ruta: "/admin/trd", nombre: "TRD" }, // árbol + 2 tablas sin scroll
  { ruta: "/admin/tvd", nombre: "TVD" }, // árbol
  { ruta: "/admin/parametros", nombre: "parámetros" }, // estaba acotada a 900
  { ruta: "/admin/catalogos", nombre: "catálogos" }, // maestro-detalle 280px
  { ruta: "/admin/metadatos", nombre: "metadatos" },
  { ruta: "/admin/eliminacion", nombre: "eliminación" }, // 3 tablas de 17 columnas
  { ruta: "/admin/usuarios", nombre: "usuarios" },
  { ruta: "/perfil", nombre: "perfil" }, // formulario: ancho de lectura
];

for (const ancho of ANCHOS) {
  test.describe(`adaptabilidad de la aplicación — ${ancho.nombre} (${ancho.width}px)`, () => {
    // La sesión se reutiliza del proyecto de preparación: estas 51 pruebas usan
    // la misma identidad, y autenticarse en cada una eran 51 vueltas completas
    // por Keycloak para comprobar anchos de pantalla.
    test.use({
      viewport: { width: ancho.width, height: ancho.height },
      storageState: ESTADO_SESION,
    });

    for (const { ruta, nombre } of PANTALLAS) {
      test(`${nombre} se ajusta a ${ancho.width}px`, async ({ page }) => {
        await page.goto(ruta);
        await page.waitForLoadState("networkidle");

        const m = await medir(page);

        expect(
          m.scrollHorizontal,
          `${ruta} se desplaza en horizontal a ${ancho.width}px ` +
            `(${m.scrollWidth}px de contenido). Culpables: ${m.desbordan.join(", ") || "—"}`,
        ).toBe(false);

        expect(
          m.tablasRecortadas,
          `${ruta}: una tabla necesita más ancho del que tiene y su contenedor no ` +
            "se desplaza. No se verá desbordada: se verá ESTRUJADA, con el " +
            "contenido de las celdas partido — que es como se lee una tabla rota.",
        ).toEqual([]);
      });
    }
  });
}
