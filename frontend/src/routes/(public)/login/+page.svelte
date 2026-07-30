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

  Es una de las dos únicas pantallas de la aplicación donde va el logotipo
  completo de marca (la otra es la landing pública).
-->
<script>
  // La lógica del flujo PKCE vive en +page.server.js (redirect a Keycloak).
  // Si /login/callback reenvió un error, `data.error` trae el mensaje en
  // español ya mapeado (nunca el string crudo de Keycloak) y esta página
  // se queda quieta en vez de reiniciar el flujo, para no entrar en bucle.
  /** @type {{ error?: string }} */
  export let data;
</script>

<svelte:head>
  <title>Iniciando sesión — OrpycaMCP</title>
</svelte:head>

<div class="login-container" role="main">
  <div class="login-brand-panel" aria-hidden="true">
    <div class="login-brand-panel__content">
      <img src="/orpyca-logo.png" alt="" class="login-brand-panel__logo" />
      <p class="login-brand-panel__tagline">
        La interfaz desaparece para que trabajes con tus documentos.
      </p>
    </div>
  </div>

  <div class="login-form-panel">
    <div class="login-card" aria-labelledby="login-heading">
      <img src="/orpyca-logo.png" alt="OrpycaMCP" class="login-logo" />

      <h1 id="login-heading" class="login-title">OrpycaMCP</h1>
      <p class="login-subtitle">Sistema de Gestión Documental</p>

      {#if data?.error}
        <div class="login-error" role="alert">
          <i class="fa-solid fa-circle-exclamation" aria-hidden="true"></i>
          <span>{data.error}</span>
        </div>

        <p class="login-help">
          <a href="/login" class="login-link">Reintentar</a>
        </p>
      {:else}
        <div class="login-redirect-notice" role="status" aria-live="polite">
          <span class="login-spinner" aria-hidden="true"></span>
          <span>Redirigiendo al proveedor de identidad&hellip;</span>
        </div>

        <p class="login-security-note">
          <i class="fa-solid fa-shield-halved" aria-hidden="true"></i>
          Acceso seguro mediante tu proveedor de identidad institucional.
        </p>

        <p class="login-help">
          Si no eres redirigido automáticamente,
          <a href="/login" class="login-link">haz clic aquí</a>.
        </p>
      {/if}
    </div>
  </div>
</div>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).
  // No se importa _tokens.scss aquí; los componentes consumen var(--op-*) directamente.

  // Mobile first: en pantallas pequeñas solo se ve la tarjeta de ingreso.
  .login-container {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--op-bg);
  }

  .login-brand-panel {
    display: none;
  }

  .login-form-panel {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
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
    height: 56px;
    width: auto;
    max-width: 100%;
    margin-bottom: var(--op-space-4);
  }

  .login-title {
    font-family: var(--op-font-display);
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

  .login-error {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    text-align: left;
    background: var(--op-error-bg);
    color: var(--op-error-fg);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin-bottom: var(--op-space-5);

    i {
      flex-shrink: 0;
      margin-top: 2px;
    }
  }

  .login-redirect-notice {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-2);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    margin-bottom: var(--op-space-5);
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

  .login-security-note {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--op-space-2);
    color: var(--op-text-secondary);
    font-size: var(--op-font-xs);
    line-height: var(--op-line-height);
    margin: 0 0 var(--op-space-5);

    i {
      color: var(--op-primary);
      flex-shrink: 0;
    }
  }

  .login-help {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .login-link {
    color: var(--op-primary-dark);
    text-decoration: underline;
    // Objetivo táctil >=44px sin distorsionar el texto en línea.
    display: inline-block;
    padding: var(--op-space-1) 0;

    &:hover {
      color: var(--op-primary);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: var(--op-radius-sm);
    }
  }

  // Composición split a partir de pantallas anchas (mobile first: se añade, no se resta).
  @media (min-width: 960px) {
    .login-container {
      flex-direction: row;
    }

    .login-brand-panel {
      display: flex;
      flex: 1 1 45%;
      align-items: center;
      justify-content: center;
      padding: var(--op-space-8);
      background: linear-gradient(160deg, var(--op-primary-dark), var(--op-primary));
    }

    .login-brand-panel__content {
      max-width: 380px;
      text-align: center;
    }

    .login-brand-panel__logo {
      height: 64px;
      width: auto;
      margin-bottom: var(--op-space-6);
      // El activo ya trae fondo transparente; se apoya tal cual sobre el degradado.
      filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.15));
    }

    .login-brand-panel__tagline {
      font-family: var(--op-font-display);
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-medium);
      color: var(--op-surface);
      line-height: var(--op-line-height);
      margin: 0;
    }

    .login-form-panel {
      flex: 1 1 55%;
    }
  }
</style>
