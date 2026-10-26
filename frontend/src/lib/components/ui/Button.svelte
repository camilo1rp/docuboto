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
  Button.svelte — Botón reutilizable con variantes de estilo y estado.

  Props:
    variant  'primary' | 'secondary' | 'danger' | 'ghost'  (default: 'primary')
    size     'sm' | 'md' | 'lg'                             (default: 'md')
    type     'button' | 'submit' | 'reset'                  (default: 'button')
    disabled boolean                                          (default: false)
    loading  boolean — muestra spinner y desactiva           (default: false)
    icon     clase FontAwesome opcional (ej. 'fas fa-save')
    href     si se pasa, renderiza como <a> en vez de <button>
    fullWidth boolean — ancho completo                        (default: false)

  Accesibilidad:
    - aria-disabled cuando loading/disabled
    - aria-busy cuando loading
    - rol button en el <a> si href está presente
-->
<script>
  /** @type {'primary'|'secondary'|'danger'|'ghost'} */
  export let variant = "primary";

  /** @type {'sm'|'md'|'lg'} */
  export let size = "md";

  /** @type {'button'|'submit'|'reset'} */
  export let type = "button";

  export let disabled = false;
  export let loading = false;

  /** Clase de icono FontAwesome (ej. 'fas fa-save') */
  export let icon = "";

  /** Si se define, el botón es un enlace <a> */
  export let href = "";

  export let fullWidth = false;

  $: isDisabled = disabled || loading;
</script>

<!--
  Con `href` esto es un ENLACE y se anuncia como tal. Llevaba `role="button"`, y
  eso no es un matiz de etiqueta: un lector de pantalla anunciaba «botón» y la
  persona pulsaba la barra espaciadora, que en un enlace no activa nada — la
  tecla de un enlace es Intro. Prometer el rol obliga a implementar su teclado,
  y aquí el elemento ya hace lo correcto siendo lo que es.

  Se caen con él dos atributos que no decían nada y un trozo de código muerto:
  esta rama solo existe cuando `!isDisabled`, y como `isDisabled = disabled ||
  loading`, dentro de ella `aria-disabled` es siempre `false`, `aria-busy`
  siempre `false` y el spinner de `loading` inalcanzable. Un enlace deshabilitado
  cae a la rama `<button disabled>` de abajo, que es donde sí se puede impedir la
  acción — un `<a>` "deshabilitado" sigue navegando.
-->
{#if href && !isDisabled}
  <a
    {href}
    class="op-btn op-btn--{variant} op-btn--{size}"
    class:op-btn--full={fullWidth}
    on:click
    {...$$restProps}
  >
    {#if icon}
      <i class="{icon} op-btn__icon" aria-hidden="true"></i>
    {/if}
    {#if $$slots.default}
      <span class="op-btn__label"><slot /></span>
    {/if}
  </a>
{:else}
  <button
    {type}
    class="op-btn op-btn--{variant} op-btn--{size}"
    class:op-btn--full={fullWidth}
    class:op-btn--loading={loading}
    disabled={isDisabled}
    aria-disabled={isDisabled}
    aria-busy={loading}
    on:click
    {...$$restProps}
  >
    {#if loading}
      <span class="op-btn__spinner" aria-hidden="true"></span>
    {:else if icon}
      <i class="{icon} op-btn__icon" aria-hidden="true"></i>
    {/if}
    {#if $$slots.default}
      <span class="op-btn__label"><slot /></span>
    {/if}
  </button>
{/if}

<style lang="scss">
  .op-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-2);
    border: 1px solid transparent;
    border-radius: var(--op-radius);
    font-family: var(--op-font-family);
    font-weight: var(--op-weight-medium);
    line-height: 1;
    cursor: pointer;
    text-decoration: none;
    transition:
      background var(--op-transition),
      color var(--op-transition),
      border-color var(--op-transition),
      box-shadow var(--op-transition);
    white-space: nowrap;
    user-select: none;

    // Tamaños
    &--sm {
      font-size: var(--op-font-xs);
      padding: var(--op-space-1) var(--op-space-3);
      height: 28px;

      // Objetivo táctil >=44px (v1.0 A11y) en dispositivos táctiles, sin
      // alterar la densidad visual en escritorio (mouse/trackpad).
      @media (pointer: coarse) {
        min-height: 44px;
      }
    }
    &--md {
      font-size: var(--op-font-sm);
      padding: var(--op-space-2) var(--op-space-4);
      height: 36px;

      @media (pointer: coarse) {
        min-height: 44px;
      }
    }
    &--lg {
      font-size: var(--op-font-base);
      padding: var(--op-space-3) var(--op-space-5);
      height: 44px;
    }

    // Variantes
    &--primary {
      background: var(--op-primary-dark);
      color: var(--op-surface);
      border-color: var(--op-primary-dark);

      &:hover:not(:disabled) {
        background: var(--op-brand-700);
        border-color: var(--op-brand-700);
      }
    }

    &--secondary {
      background: var(--op-surface);
      color: var(--op-primary-dark);
      border-color: var(--op-primary);

      &:hover:not(:disabled) {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
      }
    }

    &--danger {
      background: var(--op-error);
      color: var(--op-surface);
      border-color: var(--op-error);

      &:hover:not(:disabled) {
        filter: brightness(0.9);
      }
    }

    &--ghost {
      background: transparent;
      color: var(--op-text-secondary);
      border-color: transparent;

      &:hover:not(:disabled) {
        background: var(--op-bg);
        color: var(--op-text);
      }
    }

    &--full {
      width: 100%;
    }

    // Estado desactivado
    &:disabled,
    &[aria-disabled="true"] {
      opacity: 0.5;
      cursor: not-allowed;
      pointer-events: none;
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }

    // Spinner de carga
    &__spinner {
      width: 14px;
      height: 14px;
      border: 2px solid currentColor;
      border-top-color: transparent;
      border-radius: 50%;
      animation: op-spin 0.7s linear infinite;
    }

    &__icon {
      font-size: 0.9em;
    }

    &__label {
      display: contents;
    }
  }

  @keyframes op-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
