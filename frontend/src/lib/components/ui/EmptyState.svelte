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
  EmptyState.svelte — Estado vacío explícito para tablas y listas.

  Props:
    icon        string  — clase FontAwesome (ej. 'fas fa-inbox')
    title       string  — mensaje principal
    description string  — descripción opcional
    action      string  — texto del botón de acción (opcional)
    actionHref  string  — href para el botón de acción

  Slot:
    default  — reemplaza el botón de acción por contenido personalizado
-->
<script>
  import Button from "./Button.svelte";
  export let icon = "fas fa-inbox";
  export let title = "Sin datos";
  export let description = "";
  export let action = "";
  export let actionHref = "";
</script>

<div class="op-empty" role="status" aria-label={title}>
  {#if icon}
    <span class="op-empty__icon" aria-hidden="true">
      <i class={icon}></i>
    </span>
  {/if}

  <p class="op-empty__title">{title}</p>

  {#if description}
    <p class="op-empty__description">{description}</p>
  {/if}

  {#if $$slots.default}
    <div class="op-empty__action">
      <slot />
    </div>
  {:else if action && actionHref}
    <div class="op-empty__action">
      <Button href={actionHref} variant="primary" size="sm">{action}</Button>
    </div>
  {/if}
</div>

<style lang="scss">
  .op-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: var(--op-space-8) var(--op-space-5);
    text-align: center;
    color: var(--op-text-secondary);

    &__icon {
      font-size: 2.5rem;
      margin-bottom: var(--op-space-4);
      opacity: 0.35;
    }

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      margin: 0 0 var(--op-space-2);
    }

    &__description {
      font-size: var(--op-font-sm);
      max-width: 340px;
      margin: 0 0 var(--op-space-4);
      line-height: 1.5;
    }

    &__action {
      margin-top: var(--op-space-2);
    }
  }

  // Aquí vivía una TERCERA definición de `.op-btn`, copiada de `Button.svelte`
  // "por si Button no está importado". El comentario describía el problema y lo
  // daba por resuelto: tres definiciones del mismo botón divergen —cambiar el
  // primario en una deja las otras dos con el color viejo— y el escape que
  // justificaba la copia no existe, porque importar el componente es
  // exactamente lo que hace falta. Ahora se usa `Button`, que es quien sabe
  // cómo se ve un botón de este sistema.
</style>
