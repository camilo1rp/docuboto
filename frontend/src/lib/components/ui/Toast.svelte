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
  Toast.svelte — Contenedor global de notificaciones tipo toast.

  Renderiza la cola de toasts del store notifications.js.
  Posición: esquina inferior derecha (fija).

  Accesibilidad:
    - role="status" + aria-live="polite" para éxito/info/warning
    - role="alert" + aria-live="assertive" para errores
    - Botón de cierre individual con aria-label
-->
<script>
  import { toasts, dismiss } from "$lib/stores/notifications.js";

  const ICONS = {
    success: "fas fa-check-circle",
    error: "fas fa-exclamation-circle",
    warning: "fas fa-exclamation-triangle",
    info: "fas fa-info-circle",
  };
</script>

<div class="op-toast-container" role="region" aria-label="Notificaciones">
  {#each $toasts as toast (toast.id)}
    <div
      class="op-toast op-toast--{toast.type}"
      role={toast.type === "error" ? "alert" : "status"}
      aria-live={toast.type === "error" ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <i class="{ICONS[toast.type]} op-toast__icon" aria-hidden="true"></i>
      <span class="op-toast__message">{toast.message}</span>
      <button
        type="button"
        class="op-toast__close"
        aria-label="Cerrar notificación"
        on:click={() => dismiss(toast.id)}
      >
        <i class="fas fa-times" aria-hidden="true"></i>
      </button>
    </div>
  {/each}
</div>

<style lang="scss">
  .op-toast-container {
    position: fixed;
    bottom: var(--op-space-5);
    right: var(--op-space-5);
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    max-width: 380px;
    width: calc(100vw - 2 * var(--op-space-5));
  }

  .op-toast {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    padding: var(--op-space-3) var(--op-space-4);
    border-radius: var(--op-radius);
    box-shadow: var(--op-shadow);
    font-size: var(--op-font-sm);
    animation: op-toast-in 0.2s ease;

    &--success {
      background: var(--op-success-bg);
      color: var(--op-success-fg);
      border-left: 4px solid var(--op-success);
    }
    &--error {
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-left: 4px solid var(--op-error);
    }
    &--warning {
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
      border-left: 4px solid var(--op-warning);
    }
    &--info {
      background: var(--op-info-bg);
      color: var(--op-info-fg);
      border-left: 4px solid var(--op-info);
    }

    &__icon {
      margin-top: 2px;
      flex-shrink: 0;
    }

    &__message {
      flex: 1;
      line-height: 1.4;
    }

    &__close {
      position: relative;
      background: none;
      border: none;
      cursor: pointer;
      color: inherit;
      opacity: 0.6;
      padding: 0;
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;

      // Objetivo táctil >=44px (v1.0 A11y) sin alterar el tamaño visual del icono.
      &::after {
        content: "";
        position: absolute;
        inset: -12px;
      }

      &:hover {
        opacity: 1;
      }
      &:focus-visible {
        outline: 2px solid currentColor;
        outline-offset: 2px;
        border-radius: var(--op-radius-sm);
      }
    }
  }

  @keyframes op-toast-in {
    from {
      opacity: 0;
      transform: translateX(16px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }
</style>
