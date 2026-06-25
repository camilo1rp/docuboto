<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  (app)/+layout.svelte — Layout raíz de las rutas privadas.

  1. Inicializa el store `auth` con los datos del usuario que el layout server
     ya verificó y enriqueció (initSession preserva la guarda de T-03).
  2. Envuelve el slot con AppLayout (T-05): sidebar, topbar, breadcrumb, dock.

  La guarda de sesión vive en +layout.server.js (T-03) — este archivo
  solo consume `data.user` que el servidor ya validó.
-->
<script>
  import { initSession } from "$lib/stores/auth.js";
  import AppLayout from "$lib/components/layout/AppLayout.svelte";
  import ToastContainer from "$lib/components/ui/Toast.svelte";

  /** @type {import('./$types').LayoutData} */
  export let data;

  // Reactivo: inicializa y sincroniza el store de sesión con los datos del servidor.
  // Se ejecuta en el primer render (SSR+hydration) y cada vez que data.user cambie.
  // El onMount redundante fue eliminado para evitar la doble llamada en la hydration.
  $: initSession(data.user);
</script>

<AppLayout>
  <slot />
</AppLayout>

<!-- Contenedor global de toasts (aria-live gestionado por el componente) -->
<ToastContainer />
