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
