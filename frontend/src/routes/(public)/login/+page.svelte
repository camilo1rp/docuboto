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
  /login — Página de inicio de sesión.

  La lógica del flujo PKCE está en +page.server.js (redirect a Keycloak).
  Esta página solo se muestra transitoriamente si JavaScript está deshabilitado
  o si el redirect es lento — en condiciones normales el usuario no la ve.
-->
<script>
  // La lógica del flujo PKCE vive en +page.server.js (redirect a Keycloak).
  // Esta página no recibe datos del servidor; se muestra solo transitoriamente.
</script>

<svelte:head>
  <title>Iniciando sesión — OrpycaMCP</title>
</svelte:head>

<div class="login-container" role="main">
  <div class="login-card" aria-labelledby="login-heading">
    <div class="login-logo" aria-hidden="true">
      <span class="login-logo__mark">O</span>
    </div>

    <h1 id="login-heading" class="login-title">OrpycaMCP</h1>
    <p class="login-subtitle">Sistema de Gestión Documental</p>

    <div class="login-redirect-notice" role="status" aria-live="polite">
      <span class="login-spinner" aria-hidden="true"></span>
      <span>Redirigiendo al proveedor de identidad&hellip;</span>
    </div>

    <p class="login-help">
      Si no eres redirigido automáticamente,
      <a href="/login" class="login-link">haz clic aquí</a>.
    </p>
  </div>
</div>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).
  // No se importa _tokens.scss aquí; los componentes consumen var(--op-*) directamente.

  .login-container {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
    padding: var(--op-space-4);
  }

  .login-card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 380px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .login-logo {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: var(--op-primary);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-bottom: var(--op-space-4);
  }

  .login-logo__mark {
    color: var(--op-surface);
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    line-height: 1;
  }

  .login-title {
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-1);
  }

  .login-subtitle {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0 0 var(--op-space-6);
  }

  .login-redirect-notice {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-2);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    margin-bottom: var(--op-space-4);
  }

  .login-spinner {
    width: 16px;
    height: 16px;
    border: 2px solid var(--op-border);
    border-top-color: var(--op-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .login-help {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .login-link {
    color: var(--op-primary);
    text-decoration: underline;

    &:hover {
      color: var(--op-primary-dark);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: var(--op-radius-sm);
    }
  }
</style>
