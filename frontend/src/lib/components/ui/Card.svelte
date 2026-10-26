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
  Card.svelte — Contenedor de tarjeta con título opcional.

  Slots:
    default    — cuerpo de la tarjeta
    header     — cabecera personalizada (sobreescribe `title`)
    footer     — pie opcional

  Props:
    title    string         — título en la cabecera
    padding  boolean        — si false, sin padding interno  (default true)
    shadow   boolean        — sombra sutil                   (default true)
-->
<script>
  export let title = "";
  export let padding = true;
  export let shadow = true;
</script>

<div class="op-card" class:op-card--shadow={shadow}>
  {#if $$slots.header}
    <div class="op-card__header">
      <slot name="header" />
    </div>
  {:else if title}
    <div class="op-card__header">
      <h2 class="op-card__title">{title}</h2>
    </div>
  {/if}

  <div class="op-card__body" class:op-card__body--padded={padding}>
    <slot />
  </div>

  {#if $$slots.footer}
    <div class="op-card__footer">
      <slot name="footer" />
    </div>
  {/if}
</div>

<style lang="scss">
  .op-card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    overflow: hidden;

    &--shadow {
      box-shadow: var(--op-shadow-sm);
    }

    &__header {
      display: flex;
      align-items: center;
      padding: var(--op-space-4) var(--op-space-5);
      border-bottom: 1px solid var(--op-border);
    }

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__body {
      &--padded {
        padding: var(--op-space-5);
      }
    }

    &__footer {
      padding: var(--op-space-3) var(--op-space-5);
      border-top: 1px solid var(--op-border);
      background: var(--op-bg);
    }
  }
</style>
