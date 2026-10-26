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
  Drawer.svelte — Panel lateral deslizante (para detalle de radicado en bandeja, etc.).

  Props:
    open     boolean              — controla la visibilidad
    title    string               — título del drawer
    side     'left'|'right'      — de qué lado sale (default: 'right')
    width    string               — ancho CSS (default: '400px')

  Eventos:
    on:close  — emitido al cerrar

  Accesibilidad:
    - role="dialog", aria-modal, aria-labelledby
    - Trampa de foco (Tab/Shift+Tab dentro del drawer)
    - ESC cierra el drawer
    - Devuelve foco al elemento que lo abrió al cerrarse
-->
<script>
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { pushOverlay, popOverlay, isTopOverlay } from "$lib/utils/overlayStack.js";

  export let open = false;
  export let title = "";

  /** @type {'left'|'right'} */
  export let side = "right";
  export let width = "400px";

  // ID único por instancia — evita IDs duplicados cuando hay varios drawers en el DOM
  const instanceId = `op-drawer-title-${
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2)
  }`;

  const dispatch = createEventDispatcher();

  let drawerEl;
  let closeBtnEl;
  /** @type {Element|null} */
  let previousFocus = null;

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function trapFocus(e) {
    // No procesar eventos si el drawer está cerrado, o si hay otra superposición
    // (Modal/Drawer) apilada encima — solo el tope de la pila reacciona a
    // Tab/Escape (ver overlayStack.js). Ej.: un Modal de confirmación abierto
    // desde dentro de este Drawer no debe dejar que Escape cierre también el
    // Drawer de fondo.
    if (!open || !drawerEl || !isTopOverlay(instanceId)) return;
    const focusable = Array.from(drawerEl.querySelectorAll(FOCUSABLE));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === "Tab") {
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    if (e.key === "Escape") close();
  }

  function close() {
    dispatch("close");
  }

  // Guard `typeof document !== "undefined"` (D-16): este bloque reactivo corre
  // también durante el render SSR (a diferencia de onMount, que SvelteKit solo
  // ejecuta en cliente) — si `open` llega en true en el primer render del
  // servidor, acceder a `document` sin guardar revienta con ReferenceError y
  // devuelve 500 en toda ruta que monte el componente.
  $: if (typeof document !== "undefined" && open) {
    previousFocus = document.activeElement;
    pushOverlay(instanceId);
    tick().then(() => closeBtnEl?.focus());
  } else if (previousFocus) {
    popOverlay(instanceId);
    // Copia local + limpieza inmediata (no dentro del `.then`): evita que un
    // cierre/apertura rápidos pisen la referencia en vuelo de un `tick()`
    // anterior o la dejen en null antes de tiempo.
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
  <div class="op-drawer__backdrop" role="presentation" on:click={close} on:keydown={() => {}}></div>

  <div
    bind:this={drawerEl}
    class="op-drawer op-drawer--{side}"
    style="width: {width}"
    role="dialog"
    aria-modal="true"
    aria-labelledby={title ? instanceId : undefined}
    aria-label={title ? undefined : "Panel lateral"}
  >
    <div class="op-drawer__header">
      <h2 id={instanceId} class="op-drawer__title">{title}</h2>
      <button
        bind:this={closeBtnEl}
        type="button"
        class="op-drawer__close"
        aria-label="Cerrar panel"
        on:click={close}
      >
        <i class="fas fa-times" aria-hidden="true"></i>
      </button>
    </div>

    <div class="op-drawer__body">
      <slot />
    </div>

    {#if $$slots.footer}
      <div class="op-drawer__footer">
        <slot name="footer" />
      </div>
    {/if}
  </div>
{/if}

<style lang="scss">
  .op-drawer {
    position: fixed;
    top: 0;
    height: 100vh;
    max-width: 100vw;
    background: var(--op-surface);
    box-shadow: var(--op-shadow-lg);
    display: flex;
    flex-direction: column;
    z-index: 400;

    &--right {
      right: 0;
      border-left: 1px solid var(--op-border);
    }
    &--left {
      left: 0;
      border-right: 1px solid var(--op-border);
    }

    &__backdrop {
      position: fixed;
      inset: 0;
      background: var(--op-scrim);
      z-index: 399;
    }

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 var(--op-space-4);
      height: var(--op-topbar-height);
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
      position: relative;
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

      /* Objetivo táctil >=44px (v1.0 A11y) sin alterar el tamaño visual. */
      &::after {
        content: "";
        position: absolute;
        inset: -6px;
      }

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
      flex: 1;
      overflow-y: auto;
      padding: var(--op-space-5);
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

  @media (max-width: 480px) {
    .op-drawer {
      width: 100vw !important;
    }
  }
</style>
