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
  Stepper.svelte — Indicador de pasos para flujos guiados (radicación, etc.).

  Props:
    steps    Array<string>  — etiquetas de los pasos
    current  number         — índice del paso activo (0-indexed, bindable)
    canGoBack boolean       — si se puede retroceder manualmente (default: true)

  Eventos:
    on:stepChange  { detail: { step: number } }

  Accesibilidad:
    - ol con role="list" y aria-label
    - Paso actual marcado con aria-current="step"
    - Pasos completados marcados visualmente y con sr-only "(completado)"
-->
<script>
  import { createEventDispatcher } from "svelte";

  export let steps = [];
  export let current = 0;
  export let canGoBack = true;

  const dispatch = createEventDispatcher();

  function goTo(index) {
    if (!canGoBack && index < current) return;
    if (index > current) return; // No saltar hacia adelante
    current = index;
    dispatch("stepChange", { step: index });
  }

  $: totalSteps = steps.length;
</script>

<nav class="op-stepper" aria-label="Pasos del proceso">
  <ol class="op-stepper__list" role="list">
    {#each steps as label, i (i)}
      {@const isDone = i < current}
      {@const isActive = i === current}
      {@const isFuture = i > current}
      <li
        class="op-stepper__step"
        class:op-stepper__step--done={isDone}
        class:op-stepper__step--active={isActive}
        class:op-stepper__step--future={isFuture}
      >
        <button
          type="button"
          class="op-stepper__marker"
          aria-current={isActive ? "step" : undefined}
          aria-label="{label}{isDone ? ' (completado)' : ''}"
          disabled={isFuture || (!canGoBack && i < current)}
          on:click={() => goTo(i)}
        >
          {#if isDone}
            <i class="fas fa-check" aria-hidden="true"></i>
          {:else}
            <span aria-hidden="true">{i + 1}</span>
          {/if}
        </button>
        <span class="op-stepper__label" aria-hidden="true">
          {label}
          {#if isDone}
            <span class="sr-only">(completado)</span>
          {/if}
        </span>
        {#if i < totalSteps - 1}
          <div class="op-stepper__line" aria-hidden="true"></div>
        {/if}
      </li>
    {/each}
  </ol>
</nav>

<style lang="scss">
  .op-stepper {
    &__list {
      display: flex;
      align-items: flex-start;
      list-style: none;
      margin: 0;
      padding: 0;
      overflow-x: auto;
      // `position: relative` NO es cosmético: sin bloque contenedor propio, un
      // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
      // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
      // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
      position: relative;
    }

    &__step {
      display: flex;
      flex-direction: column;
      align-items: center;
      position: relative;
      flex: 1;
      min-width: 80px;
    }

    &__marker {
      position: relative;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      border: 2px solid var(--op-border);
      background: var(--op-surface);
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;

      // Objetivo táctil >=44px (v1.0 A11y) sin alterar el tamaño visual.
      &::after {
        content: "";
        position: absolute;
        inset: -6px;
      }
      transition:
        background var(--op-transition),
        border-color var(--op-transition),
        color var(--op-transition);
      position: relative;
      z-index: 1;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 3px;
      }

      &:disabled {
        cursor: default;
      }
    }

    &__label {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      text-align: center;
      margin-top: var(--op-space-1);
      max-width: 90px;
      line-height: 1.3;
    }

    &__line {
      position: absolute;
      top: 16px;
      left: calc(50% + 16px);
      right: calc(-50% + 16px);
      height: 2px;
      background: var(--op-border);
      z-index: 0;
    }

    // Estados
    &__step--done {
      .op-stepper__marker {
        // Permanente (no solo :hover): blanco sobre op-primary da 4.28:1 y
        // falla AA (WCAG 1.4.3). op-primary-dark (6.56:1) es el token
        // AA-seguro para texto/iconos claros — ver nota en _tokens.scss.
        background: var(--op-primary-dark);
        border-color: var(--op-primary-dark);
        color: var(--op-surface);
      }
      .op-stepper__label {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-medium);
      }
      .op-stepper__line {
        background: var(--op-primary-dark);
      }
    }

    &__step--active {
      .op-stepper__marker {
        background: var(--op-surface);
        border-color: var(--op-primary);
        // op-primary como texto sobre blanco no es AA-seguro (ver nota de
        // _tokens.scss); op-primary-dark sí lo es.
        color: var(--op-primary-dark);
        box-shadow: 0 0 0 4px var(--op-primary-light);
      }
      .op-stepper__label {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }
    }

    &__step--future {
      .op-stepper__marker {
        cursor: default;
        opacity: 0.5;
      }
    }
  }
</style>
