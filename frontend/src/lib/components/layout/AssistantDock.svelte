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
  AssistantDock.svelte — Panel lateral del asistente conversacional.

  Estado: VACÍO (placeholder para Fase 3 / T-14).

  El dock se activa/oculta con el botón en la topbar.
  En Fase 3 se conectará a:
    - $lib/stores/assistant.js (estado de la conversación)
    - $lib/services/assistant.service.js (POST /api/v1/assistant/message)
    - Panel de chat, entrada de texto, VoiceInput y subida de anexos.

  Por ahora renderiza un estado vacío accesible que no produce errores
  y sirve de ancla para los tests de T-05.
-->
<script>
  /** Controla si el dock está visible */
  export let open = false;

  /** Manejador de cierre (padre lo gestiona) */
  export let onClose = () => {};
</script>

{#if open}
  <div
    class="assistant-dock__backdrop"
    role="presentation"
    on:click={onClose}
    on:keydown={() => {}}
  ></div>

  <aside class="assistant-dock" aria-label="Asistente conversacional" aria-live="polite">
    <div class="assistant-dock__header">
      <span class="assistant-dock__title">
        <i class="fas fa-robot" aria-hidden="true"></i>
        Asistente OrpycaMCP
      </span>
      <button
        class="assistant-dock__close"
        type="button"
        aria-label="Cerrar asistente"
        on:click={onClose}
      >
        <i class="fas fa-times" aria-hidden="true"></i>
      </button>
    </div>

    <div class="assistant-dock__body">
      <!-- Placeholder Fase 3 — T-14 reemplaza este bloque -->
      <div class="assistant-dock__placeholder">
        <i class="fas fa-comments" aria-hidden="true"></i>
        <p>El asistente estará disponible en la Fase 3.</p>
        <p class="assistant-dock__placeholder-sub">
          Podrá radicar, consultar expedientes y ejecutar tareas mediante lenguaje natural, acotado
          por sus permisos.
        </p>
      </div>
    </div>
  </aside>
{/if}

<style lang="scss">
  .assistant-dock {
    position: fixed;
    top: 0;
    right: 0;
    width: 360px;
    max-width: 100vw;
    height: 100vh;
    background: var(--op-surface);
    border-left: 1px solid var(--op-border);
    box-shadow: var(--op-shadow-lg);
    display: flex;
    flex-direction: column;
    z-index: 300;

    &__backdrop {
      position: fixed;
      inset: 0;
      background: rgba(33, 36, 33, 0.25);
      z-index: 299;
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
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }

    &__close {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      padding: var(--op-space-2);
      border-radius: var(--op-radius-sm);
      display: flex;
      align-items: center;

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
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--op-space-5);
    }

    &__placeholder {
      text-align: center;
      color: var(--op-text-secondary);

      i {
        font-size: 2.5rem;
        margin-bottom: var(--op-space-4);
        opacity: 0.3;
        display: block;
      }

      p {
        font-size: var(--op-font-base);
        margin-bottom: var(--op-space-2);
      }
    }

    &__placeholder-sub {
      font-size: var(--op-font-sm) !important;
      opacity: 0.7;
    }
  }

  @media (max-width: 600px) {
    .assistant-dock {
      width: 100vw;
    }
  }
</style>
