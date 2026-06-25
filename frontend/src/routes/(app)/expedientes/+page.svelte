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
  Listado de expedientes — placeholder (T-12 implementará la vista completa).
  Sirve como punto de navegación para el breadcrumb del workspace de expediente.
-->
<script>
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
</script>

<svelte:head>
  <title>Expedientes — OrpycaMCP</title>
</svelte:head>

<div style="padding: var(--op-space-5);">
  <h1
    style="font-size: var(--op-font-lg); font-weight: var(--op-weight-bold); color: var(--op-text); margin-bottom: var(--op-space-4);"
  >
    Expedientes
  </h1>
  <EmptyState
    icon="fa-folder-open"
    title="Vista en construcción"
    description="La gestión de expedientes (T-12) se implementará en la siguiente tarea. Accede a un expediente directamente desde la bandeja de radicados."
  />
</div>
