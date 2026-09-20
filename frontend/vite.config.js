// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/// <reference types="vitest/config" />
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";
import { resolve } from "path";

/**
 * Node 22 o nada — y dicho con una frase, no con nueve rojos.
 *
 * Las dos imágenes del frontend son `node:22-alpine` y el job de CI también,
 * pero nada impedía ejecutar la suite desde un host con otra versión. Y el
 * fallo NO se parece a su causa: en Node 18 cinco guardianes ni siquiera
 * cargan (`process.getBuiltinModule` llegó en 22.3) y los cuatro casos de
 * `handleError` revientan por el `crypto` global (estable desde 19). Salen
 * nueve pruebas en rojo repartidas por seis ficheros, ninguna relacionada con
 * el código que se está tocando.
 *
 * Ha costado un diagnóstico **dos veces**: la primera quedó escrita en
 * `Dockerfile.dev` —que por eso subió de node:18 a node:22— y aun así volvió a
 * pasar, porque aquella corrección arregló la imagen y no la puerta por la que
 * se cuela la versión equivocada. Un aviso que se lee una vez no es una
 * barrera; esto sí.
 *
 * Va en el config de Vite —no en `engines` de `package.json`— porque `engines`
 * sin `engine-strict` solo advierte durante la instalación, y aquí lo que hay
 * que parar es la EJECUCIÓN: este archivo lo carga tanto `vitest` como el
 * servidor de desarrollo y el build.
 */
const MAYOR_MINIMA = 22;
const mayorActual = Number(process.versions.node.split(".")[0]);
if (Number.isFinite(mayorActual) && mayorActual < MAYOR_MINIMA) {
  throw new Error(
    `Node ${process.versions.node} no sirve para este frontend: hace falta Node ${MAYOR_MINIMA} o superior ` +
      `(es la versión de \`Dockerfile\`, \`Dockerfile.dev\` y del job \`test-frontend\` del CI).\n` +
      `Con una versión anterior la suite no falla: se DESCOMPONE — cinco guardianes no llegan a cargar y ` +
      `\`handleError\` revienta por el \`crypto\` global, y ninguno de esos rojos tiene que ver con tu cambio.\n` +
      `Ejecútala en Docker, que es la regla del proyecto:\n` +
      `  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/repo -w /repo/frontend -e HOME=/tmp \\\n` +
      `    node:22-alpine npx vitest run`,
  );
}

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: true,
    port: 5173,
  },
  build: {
    target: "esnext",
  },
  resolve: {
    alias: {
      // Alias para importar estilos desde componentes sin rutas relativas largas.
      // Uso: @use '$styles/tokens' as *;
      $styles: resolve(process.cwd(), "src/styles"),
    },
    // Bajo Vitest, el resolvedor de módulos usa por defecto las condiciones
    // "ssr" (sin "browser"), así que `import ... from "svelte"` resuelve a
    // `svelte/ssr.js` — ahí `onMount`/`beforeUpdate`/`afterUpdate` son NO-OPs
    // (solo `onDestroy` es real, ver D-16: por eso onDestroy sí corre en SSR).
    // Con `environment: "jsdom"` los componentes SÍ necesitan el `onMount`
    // real (p. ej. Modal/Drawer registran su trampa de foco ahí) — forzamos
    // la condición "browser" solo bajo Vitest; el build/dev real de
    // adapter-node no pasa por aquí (VITEST no está seteada) y sigue
    // resolviendo SSR normalmente.
    conditions: process.env.VITEST ? ["browser"] : undefined,
  },
  css: {
    preprocessorOptions: {
      scss: {
        // Permite @use '$styles/tokens' en bloques <style lang="scss"> de .svelte
        loadPaths: [resolve(process.cwd(), "src/styles")],
      },
    },
  },
  // Edición colaborativa (TipTap + Yjs + Hocuspocus) es opcional/futura;
  // pre-bundle solo si se habilita (ver optionalDependencies en package.json).
  optimizeDeps: {
    include: [],
  },
  test: {
    // Por defecto los tests de componentes/stores corren con DOM (jsdom).
    // `src/lib/server/**` es código server-only (D-05: nunca se bundlea al
    // cliente) y corre en entorno 'node', igual que en producción.
    environment: "jsdom",
    environmentMatchGlobs: [["src/lib/server/**", "node"]],
    setupFiles: ["./vitest-setup.js"],
    include: ["src/**/*.{test,spec}.js"],
    // El suite Playwright (e2e/) vive fuera de src/ y NUNCA debe ser recogido
    // por Vitest (son procesos de test distintos con runners distintos).
    exclude: ["node_modules/**", ".svelte-kit/**", "build/**", "e2e/**"],
  },
});
