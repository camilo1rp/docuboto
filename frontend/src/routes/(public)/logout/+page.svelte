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
  /logout — Confirmación de cierre de sesión.

  Esta página existe porque el cierre de sesión dejó de vivir en el `load` de la
  ruta (ver `+page.server.js`): un GET no puede tener efectos, y con
  `data-sveltekit-preload-data="hover"` el `load` se ejecutaba al pasar el ratón
  por el enlace del topbar.

  Quien llegue aquí escribiendo la URL o desde un marcador ve un botón, no una
  sesión ya cerrada. El botón del topbar envía su propio POST y no pasa por esta
  pantalla.

  Funciona sin JavaScript: es un `<form method="POST">` corriente.
-->
<svelte:head>
  <title>Cerrar sesión — OrpycaMCP</title>
</svelte:head>

<main class="logout">
  <div class="logout__card">
    <img src="/brand/orpyca-mark.png" alt="" class="logout__icon" aria-hidden="true" />

    <h1 class="logout__title">¿Cerrar la sesión?</h1>

    <p class="logout__message">
      Se cerrará tu sesión en OrpycaMCP y también en el servidor de autenticación de la entidad.
      Cualquier formulario sin guardar se perderá.
    </p>

    <div class="logout__actions">
      <form method="POST" action="/logout">
        <button type="submit" class="button-primary">Cerrar sesión</button>
      </form>
      <a href="/dashboard" class="button-secondary">Volver al panel principal</a>
    </div>
  </div>
</main>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).

  .logout {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
    padding: var(--op-space-4);
  }

  .logout__card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 460px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .logout__icon {
    height: 40px;
    width: auto;
    margin-bottom: var(--op-space-4);
  }

  .logout__title {
    font-family: var(--op-font-display);
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-3);
  }

  .logout__message {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin: 0 0 var(--op-space-6);
  }

  .logout__actions {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    align-items: center;
  }

  .button-primary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    background: var(--op-primary-dark);
    color: var(--op-on-primary);
    padding: var(--op-space-2) var(--op-space-5);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    border: none;
    cursor: pointer;
    transition: background var(--op-transition);

    &:hover {
      background: var(--op-brand-700);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .button-secondary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: var(--op-space-2) var(--op-space-3);
    color: var(--op-primary-dark);
    font-size: var(--op-font-sm);
    text-decoration: underline;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }
</style>
