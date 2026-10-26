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
  CanalesAlternos.svelte — cómo presentar el documento SIN la radicación en línea.

  Siempre dice los dos canales (presencial y correo de la entidad). Si la entidad
  declaró su contacto, lo muestra concreto; los campos no declarados no se pintan y
  no se inventa ninguno (tampoco horario: no existe como dato).
-->
<script>
  import { urlHttpSegura } from "$lib/utils/urlSegura.js";
  import { normalizarContacto } from "$lib/utils/contactoEntidad.js";

  /** @type {any} */
  export let contacto = null;

  $: c = normalizarContacto(contacto);
  $: web = c?.web ? urlHttpSegura(c.web) : "";
</script>

<span class="canales">
  Puede presentar su documento de forma presencial en la ventanilla de la entidad o por el correo de
  atención de la entidad.
  {#if c}
    <span class="canales__contacto" data-testid="contacto-entidad">
      {#if c.nombre}<strong>{c.nombre}</strong>{/if}
      {#if c.direccion}<span> · Dirección: {c.direccion}</span>{/if}
      {#if c.telefono}<span> · Teléfono: {c.telefono}</span>{/if}
      {#if c.email}<span> · Correo: <a href="mailto:{c.email}">{c.email}</a></span>{/if}
      {#if web}<span>
          · Sitio web: <a href={web} target="_blank" rel="noopener noreferrer">{c.web}</a></span
        >{/if}
    </span>
  {/if}
</span>

<style>
  .canales__contacto {
    display: block;
    margin-top: var(--op-space-1);
    overflow-wrap: anywhere;
  }
</style>
