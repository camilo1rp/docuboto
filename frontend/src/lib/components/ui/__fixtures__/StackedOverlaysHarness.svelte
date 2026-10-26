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
  StackedOverlaysHarness.svelte — Fixture de test para el escenario real de
  `(app)/bandeja/+page.svelte`: un Drawer de detalle que permanece abierto
  mientras se lanza un Modal de confirmación desde dentro de él (p. ej.
  "Solicitar anulación"). Ambos componentes están montados siempre (igual que
  en la página real) y su visibilidad se controla vía la prop `open`.
-->
<script>
  import Drawer from "../Drawer.svelte";
  import Modal from "../Modal.svelte";

  export let drawerOpen = false;
  export let modalOpen = false;

  function closeDrawer() {
    drawerOpen = false;
  }
  function closeModal() {
    modalOpen = false;
  }
</script>

<Drawer open={drawerOpen} title="Detalle" on:close={closeDrawer}>
  <button type="button" data-testid="drawer-action">Acción del drawer</button>
</Drawer>

<Modal open={modalOpen} title="Confirmar" on:close={closeModal}>
  <button type="button" data-testid="modal-action">Acción del modal</button>
</Modal>
