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
  /login/callback — Pantalla transitoria durante el intercambio de código.
  En condiciones normales el servidor redirige antes de que esta página se pinte.
  Se muestra brevemente si el redirect tarda (ej. red lenta).

  Dentro de la app solo va el icono de marca (nunca el logotipo completo,
  reservado para /login y la landing pública).
-->
<svelte:head>
  <title>Verificando sesión — Docuboto</title>
</svelte:head>

<div class="callback-container" role="main">
  <div class="callback-card" role="status" aria-live="polite">
    <img src="/brand/orpyca-mark.svg" alt="" class="callback-icon" aria-hidden="true" />
    <span class="callback-spinner" aria-hidden="true"></span>
    <h1 class="callback-title">Verificando sesión</h1>
    <p class="callback-text">Confirmando tu identidad con el proveedor institucional&hellip;</p>
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
    padding: var(--op-space-4);
  }

  .callback-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--op-space-3);
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 380px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .callback-icon {
    height: 40px;
    width: auto;
    margin-bottom: var(--op-space-2);
  }

  .callback-spinner {
    width: 32px;
    height: 32px;
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

  .callback-title {
    font-family: var(--op-font-display);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: var(--op-space-2) 0 0;
  }

  .callback-text {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin: 0;
  }
</style>
