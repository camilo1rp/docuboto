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
  DrawerHarness.svelte — Fixture de test para Drawer.svelte (uso exclusivo de tests).

  Mismo patrón que ModalHarness.svelte: botón disparador fuera del drawer +
  slot con elementos enfocables, cerrándose a sí mismo al recibir `close`.
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "../Drawer.svelte";

  export let open = false;
  export let title = "Detalle";
  export let side = "right";

  const dispatch = createEventDispatcher();

  function handleClose() {
    open = false;
    dispatch("close");
  }
</script>

<button type="button" data-testid="trigger">Abrir drawer</button>

<Drawer {open} {title} {side} on:close={handleClose}>
  <button type="button">Primero</button>
  <p>Contenido del panel.</p>
  <svelte:fragment slot="footer">
    <button type="button" data-testid="last-action">Última acción</button>
  </svelte:fragment>
</Drawer>
