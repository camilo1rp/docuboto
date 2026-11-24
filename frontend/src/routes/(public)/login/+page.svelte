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
  /login — Página de inicio de sesión (ADR-048).

  Un formulario propio: la acción de +page.server.js envía usuario y
  contraseña a la app y guarda la sesión en cookies httpOnly. Funciona sin
  JavaScript (envío nativo del formulario). El rediseño visual queda para
  docs/rediseno-ui.

  Es una de las dos únicas pantallas de la aplicación donde va el logotipo
  completo de marca (la otra es la landing pública).
-->
<script>
  // Si la acción falló, `form.error` trae el mensaje en español ya mapeado
  // (nunca el texto crudo del backend) y `form.username` lo tecleado.
  /** @type {{ pedirEntidad?: boolean }} */
  export let data;
  /** @type {{ error?: string, username?: string } | null} */
  export let form;
</script>

<svelte:head>
  <title>Iniciar sesión — Docuboto</title>
</svelte:head>

<div class="login-container" role="main">
  <div class="login-brand-panel" aria-hidden="true">
    <div class="login-brand-panel__content">
      <!--
        Sobre el fondo profundo el nombre va como TEXTO, no como imagen: el
        logotipo institucional lleva la palabra "Orpyca" en un degradado negro
        que sobre oscuro desaparece. El isotipo (anillo verde) sí funciona en
        ambos fondos, así que la marca se recompone aquí con el isotipo y
        tipografía viva.
      -->
      <img src="/brand/orpyca-mark.svg" alt="" class="login-brand-panel__logo" />
      <p class="login-brand-panel__wordmark">Orpyca</p>
      <p class="login-brand-panel__claim">Gestión Documental Inteligente</p>
      <p class="login-brand-panel__tagline">
        La interfaz desaparece para que trabajes con tus documentos.
      </p>
    </div>
  </div>

  <div class="login-form-panel">
    <div class="login-card" aria-labelledby="login-heading">
      <!-- Sobre fondo claro sí entra el logotipo completo, con su marca denominativa. -->
      <img src="/brand/orpyca-lockup.svg" alt="Orpyca" class="login-logo" />

      <h1 id="login-heading" class="login-title">OrpycaMCP</h1>
      <p class="login-subtitle">Sistema de Gestión Documental</p>

      {#if form?.error}
        <div class="login-error" role="alert">
          <i class="fa-solid fa-circle-exclamation" aria-hidden="true"></i>
          <span>{form.error}</span>
        </div>
      {/if}

      <form method="POST" class="login-form">
        <label class="login-label" for="login-username">Usuario</label>
        <input
          id="login-username"
          class="login-input"
          name="username"
          autocomplete="username"
          autocapitalize="none"
          required
          value={form?.username ?? ""}
        />

        <label class="login-label" for="login-password">Contraseña</label>
        <input
          id="login-password"
          class="login-input"
          name="password"
          type="password"
          autocomplete="current-password"
          required
        />

        {#if data?.pedirEntidad}
          <label class="login-label" for="login-tenant">Entidad</label>
          <input
            id="login-tenant"
            class="login-input"
            name="tenant"
            autocapitalize="none"
            required
          />
        {/if}

        <button type="submit" class="login-submit">Ingresar</button>
      </form>

      <p class="login-security-note">
        <i class="fa-solid fa-shield-halved" aria-hidden="true"></i>
        Si olvidaste tu contraseña, pide al administrador de tu entidad que la restablezca.
      </p>
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

  // El logotipo completo es apaisado (≈2,3:1) y ya NO lleva leyenda dibujada: el
  // descriptor vive como texto (.login-brand-panel__claim), así que el activo solo
  // aporta isotipo y marca denominativa y no hay una línea fina que se pierda al
  // reducir. Se dimensiona por ANCHO: a 240px el nombre queda holgado en la
  // tarjeta (380px).
  .login-logo {
    width: 240px;
    max-width: 100%;
    height: auto;
    margin: 0 auto var(--op-space-4);
    display: block;
  }

  .login-title {
    font-family: var(--op-font-display);
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

  .login-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    text-align: left;
    margin-bottom: var(--op-space-5);
  }

  .login-label {
    font-size: var(--op-font-sm);
    color: var(--op-text);
  }

  .login-input {
    font: inherit;
    padding: var(--op-space-2) var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);
    color: var(--op-text);
    margin-bottom: var(--op-space-2);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 1px;
    }
  }

  .login-submit {
    font: inherit;
    font-weight: var(--op-weight-bold);
    padding: var(--op-space-3);
    border: none;
    border-radius: var(--op-radius);
    background: var(--op-primary);
    color: var(--op-surface);
    cursor: pointer;
    margin-top: var(--op-space-2);

    &:hover {
      background: var(--op-primary-dark);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary-dark);
      outline-offset: 2px;
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
      // Fondo profundo, no el verde medio de la paleta: el isotipo es un anillo
      // verde oscuro y sobre `--op-primary` se empastaba con el fondo. Sobre el
      // verde casi negro de marca recupera el contraste con que fue diseñado.
      background: linear-gradient(160deg, var(--op-brand-900), var(--op-brand-700));
    }

    .login-brand-panel__content {
      max-width: 380px;
      text-align: center;
    }

    .login-brand-panel__logo {
      height: 132px;
      width: auto;
      margin-bottom: var(--op-space-4);
      // El activo ya trae fondo transparente; se apoya tal cual sobre el degradado.
      filter: drop-shadow(0 4px 16px rgba(0, 0, 0, 0.45));
    }

    .login-brand-panel__wordmark {
      font-family: var(--op-font-display);
      font-size: var(--op-font-2xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-surface);
      letter-spacing: 0.01em;
      line-height: 1.1;
      margin: 0;
    }

    .login-brand-panel__claim {
      font-family: var(--op-font-display);
      font-size: var(--op-font-md);
      // `--op-brand-400` sobre el fondo profundo se queda en 4,31:1 — por debajo
      // del 4,5:1 que pide AA a este tamaño. El tinte pálido llega a 10,6:1 y
      // sigue leyéndose como verde de marca.
      color: var(--op-primary-light);
      letter-spacing: 0.06em;
      margin: 0 0 var(--op-space-6);
    }

    .login-brand-panel__tagline {
      font-family: var(--op-font-display);
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-medium);
      color: var(--op-surface);
      line-height: var(--op-line-height);
      margin: 0;
      opacity: 0.86;
    }

    .login-form-panel {
      flex: 1 1 55%;
    }
  }
</style>
