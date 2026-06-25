// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

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
});
