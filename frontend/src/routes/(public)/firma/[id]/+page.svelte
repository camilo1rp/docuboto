<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!-- Comprobar una firma (ADR-054 §2, §4). Sin sesión. -->
<script>
  import VerificacionPublica from "$lib/components/features/firmas/VerificacionPublica.svelte";

  /** @type {any} */
  export let data;
</script>

<svelte:head>
  <title>Comprobar una firma — Docuboto</title>
  <meta
    name="description"
    content="Compruebe si una firma electrónica es válida con el código que acompaña al documento."
  />
</svelte:head>

<VerificacionPublica
  codigo={data.codigo ?? ""}
  verificacion={data.verificacion ?? null}
  mensaje={data.mensaje ?? null}
/>
