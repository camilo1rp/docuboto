<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  ModalHarness.svelte — Fixture de test para Modal.svelte (uso exclusivo de tests).

  Reproduce el patrón real de uso (botón disparador fuera del modal + slot con
  varios elementos enfocables + footer con acción de confirmación), necesario
  porque @testing-library/svelte 4 no soporta pasar slots a un componente
  Svelte 4 vía `render(Component, { slots })`. Cierra el modal por sí mismo al
  recibir `close` (igual que hacen las páginas reales) y reenvía el evento.
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "../Modal.svelte";

  export let open = false;
  export let title = "Confirmar acción";
  export let closeOnBackdrop = true;

  const dispatch = createEventDispatcher();

  function handleClose() {
    open = false;
    dispatch("close");
  }
</script>

<button type="button" data-testid="trigger">Abrir modal</button>

<Modal {open} {title} {closeOnBackdrop} on:close={handleClose}>
  <button type="button">Primero</button>
  <input type="text" aria-label="Campo de ejemplo" />
  <svelte:fragment slot="footer">
    <button type="button" on:click={handleClose}>Cancelar</button>
    <button type="button" data-testid="confirm">Confirmar</button>
  </svelte:fragment>
</Modal>
