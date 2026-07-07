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
  Modal.svelte — Diálogo modal accesible con trampa de foco.

  Props:
    open     boolean          — controla la visibilidad
    title    string           — título del diálogo
    size     'sm'|'md'|'lg'  — ancho máximo (default: 'md')
    closeOnBackdrop boolean   — cerrar al hacer clic en el fondo (default: true)

  Eventos:
    on:close   — emitido al cerrar (ESC, clic en X, clic en fondo)

  Slots:
    default  — cuerpo del modal
    footer   — botones de acción en el pie

  Accesibilidad:
    - role="dialog", aria-modal, aria-labelledby
    - Trampa de foco: Tab/Shift+Tab ciclan dentro del modal
    - ESC cierra el modal
    - Foco inicial en el botón de cierre al abrir
    - Al cerrar, devuelve el foco al elemento que lo abrió
-->
<script>
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { pushOverlay, popOverlay, isTopOverlay } from "$lib/utils/overlayStack.js";

  export let open = false;
  export let title = "";

  /** @type {'sm'|'md'|'lg'} */
  export let size = "md";
  export let closeOnBackdrop = true;

  const dispatch = createEventDispatcher();

  // ID único por instancia — evita IDs duplicados cuando hay varios modales en el DOM
  const instanceId = `op-modal-title-${
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2)
  }`;

  let modalEl;
  let closeBtnEl;
  /** @type {Element|null} */
  let previousFocus = null;

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function getFocusable() {
    return modalEl ? Array.from(modalEl.querySelectorAll(FOCUSABLE)) : [];
  }

  function trapFocus(e) {
    // No procesar eventos si el modal está cerrado, o si hay otra superposición
    // (Modal/Drawer) apilada encima — solo el tope de la pila reacciona a
    // Tab/Escape (ver overlayStack.js).
    if (!open || !modalEl || !isTopOverlay(instanceId)) return;
    const focusable = getFocusable();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === "Tab") {
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    if (e.key === "Escape") {
      close();
    }
  }

  function close() {
    dispatch("close");
  }

  function handleBackdropClick(e) {
    if (closeOnBackdrop && e.target === e.currentTarget) {
      close();
    }
  }

  // Guard `typeof document !== "undefined"` (D-16): este bloque reactivo corre
  // también durante el render SSR (a diferencia de onMount, que SvelteKit solo
  // ejecuta en cliente) — si `open` llega en true en el primer render del
  // servidor (p. ej. un modal que abre según estado inicial), acceder a
  // `document` sin guardar revienta con ReferenceError y devuelve 500 en toda
  // ruta que monte el componente.
  $: if (typeof document !== "undefined" && open) {
    previousFocus = document.activeElement;
    pushOverlay(instanceId);
    tick().then(() => {
      if (closeBtnEl) closeBtnEl.focus();
    });
  } else if (previousFocus) {
    popOverlay(instanceId);
    // Se toma una copia local y se limpia `previousFocus` de inmediato (no
    // dentro del `.then`): si el modal se reabre/cierra rápido, una segunda
    // pasada de este bloque no debe pisar la referencia que ya está en vuelo
    // en el primer `tick().then`, ni dejarla en null antes de tiempo.
    const elementToRefocus = /** @type {HTMLElement} */ (previousFocus);
    previousFocus = null;
    tick().then(() => {
      elementToRefocus?.focus?.();
    });
  }

  // Limpieza como retorno de onMount (solo cliente): onDestroy corre también en
  // SSR, donde `document` no existe y rompería el render.
  onMount(() => {
    document.addEventListener("keydown", trapFocus);
    return () => {
      popOverlay(instanceId);
      document.removeEventListener("keydown", trapFocus);
    };
  });
</script>

{#if open}
  <div
    class="op-modal__overlay"
    role="presentation"
    on:click={handleBackdropClick}
    on:keydown={() => {}}
  >
    <div
      bind:this={modalEl}
      class="op-modal op-modal--{size}"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? instanceId : undefined}
      aria-label={title ? undefined : "Diálogo"}
    >
      <!-- Cabecera -->
      <div class="op-modal__header">
        <h2 id={instanceId} class="op-modal__title">{title}</h2>
        <button
          bind:this={closeBtnEl}
          type="button"
          class="op-modal__close"
          aria-label="Cerrar"
          on:click={close}
        >
          <i class="fas fa-times" aria-hidden="true"></i>
        </button>
      </div>

      <!-- Cuerpo -->
      <div class="op-modal__body">
        <slot />
      </div>

      <!-- Pie (opcional) -->
      {#if $$slots.footer}
        <div class="op-modal__footer">
          <slot name="footer" />
        </div>
      {/if}
    </div>
  </div>
{/if}

<style lang="scss">
  .op-modal {
    &__overlay {
      position: fixed;
      inset: 0;
      background: rgba(33, 36, 33, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 500;
      padding: var(--op-space-4);
    }

    background: var(--op-surface);
    border-radius: var(--op-radius-lg);
    box-shadow: var(--op-shadow-lg);
    width: 100%;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    &--sm {
      max-width: 400px;
    }
    &--md {
      max-width: 560px;
    }
    &--lg {
      max-width: 760px;
    }

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--op-space-4) var(--op-space-5);
      border-bottom: 1px solid var(--op-border);
      flex-shrink: 0;
    }

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__close {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--op-radius-sm);

      &:hover {
        background: var(--op-bg);
        color: var(--op-text);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__body {
      padding: var(--op-space-5);
      overflow-y: auto;
      flex: 1;
    }

    &__footer {
      padding: var(--op-space-4) var(--op-space-5);
      border-top: 1px solid var(--op-border);
      display: flex;
      justify-content: flex-end;
      gap: var(--op-space-3);
      flex-shrink: 0;
    }
  }
</style>
