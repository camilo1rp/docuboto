// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

// app.d.ts -- Tipos globales de SvelteKit para OrpycaMCP.
// Ver: https://kit.svelte.dev/docs/types#app

declare global {
  namespace App {
    /**
     * Datos disponibles en `event.locals` para todos los request handlers.
     * Poblados por hooks.server.js tras verificar/renovar el access_token.
     */
    interface Locals {
      /** JWT de acceso (crudo), disponible si el usuario tiene sesión activa. */
      accessToken?: string;
      /** Claims extraídos del JWT (roles, tenant_slug, nombre, etc.). */
      user?: import("$lib/services/auth.server.js").SessionUser | null;
    }

    /**
     * Error shape que devuelve handleError en hooks.server.js.
     */
    interface Error {
      message: string;
      code?: string;
    }

    // PageData, PageState, Platform: sin personalización por ahora.
    interface PageData {}
    interface PageState {}
    interface Platform {}
  }
}

export {};
