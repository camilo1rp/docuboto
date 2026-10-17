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
  (app)/+error.svelte — Errores de las páginas privadas.

  Sin este archivo, un `error(403)` lanzado por una página hija se pintaba con
  el +error raíz FUERA del layout de (app), y la pantalla de entidad no
  operativa (ADR-035) nunca llegaba a mostrarse. Aquí el error se dibuja dentro
  del layout: si la entidad no está operativa, el layout sustituye este error
  por `EntidadNoOperativa`; si no, se reutiliza la pantalla de error raíz.
-->
<script>
  import RootError from "../+error.svelte";
</script>

<RootError />
