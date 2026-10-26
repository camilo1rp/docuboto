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
  ButtonHarness.svelte — Fixture de test para Button.svelte (uso exclusivo de tests).

  @testing-library/svelte 4/5 no soporta pasar contenido de slot por defecto
  directamente vía `render(Component, { slots: {...} })` para componentes
  Svelte 4; este wrapper provee el slot por defecto (`label`) reenviando el
  resto de props tal cual y reenviando el evento `click`.
-->
<script>
  import Button from "../Button.svelte";
  export let label = "Botón";
</script>

<Button {...$$restProps} on:click>{label}</Button>
