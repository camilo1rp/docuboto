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
  /login/callback — Pantalla transitoria durante el intercambio de código.
  En condiciones normales el servidor redirige antes de que esta página se pinte.
  Se muestra brevemente si el redirect tarda (ej. red lenta).
-->
<svelte:head>
  <title>Verificando sesión — OrpycaMCP</title>
</svelte:head>

<div class="callback-container" role="main">
  <div class="callback-card" role="status" aria-live="polite">
    <span class="callback-spinner" aria-hidden="true"></span>
    <p class="callback-text">Verificando sesión&hellip;</p>
  </div>
</div>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).

  .callback-container {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
  }

  .callback-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--op-space-4);
  }

  .callback-spinner {
    width: 40px;
    height: 40px;
    border: 3px solid var(--op-border);
    border-top-color: var(--op-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .callback-text {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    margin: 0;
  }
</style>
