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
  WriteActionHarness.svelte — Fixture de test (uso exclusivo de tests).

  Reproduce, de forma mínima y aislada, el patrón real que usan las páginas
  de escritura (`radicar/+page.svelte`, `transferencias/+page.svelte`,
  `bandeja/+page.svelte`: `showConfirmModal` local + Modal con el botón de
  confirmación en el footer, que es el único punto que dispara la acción):
  un botón de acción NO llama directamente al proxy — solo abre un Modal de
  confirmación; la llamada real (`onConfirmWrite`, aquí una prop inyectable
  para poder espiarla desde el test) solo ocurre al pulsar "Confirmar" DENTRO
  del diálogo.
-->
<script>
  import Modal from "../Modal.svelte";

  /** Inyectada desde el test — simula la llamada real al proxy BFF (D-05). */
  export let onConfirmWrite = () => {};

  let showConfirmModal = false;

  function openConfirmModal() {
    showConfirmModal = true;
  }
  function closeConfirmModal() {
    showConfirmModal = false;
  }
  function confirmWrite() {
    onConfirmWrite();
    showConfirmModal = false;
  }
</script>

<button type="button" data-testid="action-anular" on:click={openConfirmModal}>
  Anular radicado
</button>

<Modal open={showConfirmModal} title="Confirmar anulación" on:close={closeConfirmModal}>
  <p>¿Confirma la anulación de este radicado? Esta acción no se puede deshacer.</p>
  <svelte:fragment slot="footer">
    <button type="button" on:click={closeConfirmModal}>Cancelar</button>
    <button type="button" data-testid="confirm-write" on:click={confirmWrite}>
      Confirmar anulación
    </button>
  </svelte:fragment>
</Modal>
