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
  /403 — Acceso denegado.

  Se muestra cuando un usuario autenticado intenta acceder a una ruta
  o acción para la que no tiene permisos (RBAC en UI, T-19).
  La autorización real siempre la hace el backend.

  Dentro de la app solo va el icono de marca (nunca el logotipo completo,
  reservado para /login y la landing pública).
-->
<svelte:head>
  <title>Acceso denegado (403) — OrpycaMCP</title>
</svelte:head>

<main class="forbidden">
  <div class="forbidden__card">
    <img src="/brand/orpyca-mark.svg" alt="" class="forbidden__icon" aria-hidden="true" />

    <p class="forbidden__code" aria-hidden="true">403</p>

    <h1 class="forbidden__title">Acceso denegado</h1>

    <p class="forbidden__message">
      Tu cuenta no tiene los permisos necesarios para esta sección. Si crees que deberías tener
      acceso, contacta al administrador de tu entidad para que revise tu rol.
    </p>

    <div class="forbidden__actions">
      <a href="/dashboard" class="button-primary" role="button">Volver al panel principal</a>
      <a href="/" class="button-secondary" role="button">Ir al inicio</a>
    </div>
  </div>
</main>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).

  .forbidden {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
    padding: var(--op-space-4);
  }

  .forbidden__card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 460px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .forbidden__icon {
    height: 40px;
    width: auto;
    margin-bottom: var(--op-space-4);
  }

  .forbidden__code {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-warning);
    letter-spacing: 2px;
    margin: 0 0 var(--op-space-2);
  }

  .forbidden__title {
    font-family: var(--op-font-display);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-3);
  }

  .forbidden__message {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin: 0 0 var(--op-space-6);
  }

  .forbidden__actions {
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
    text-decoration: none;
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

    &:hover {
      color: var(--op-primary);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: var(--op-radius-sm);
    }
  }
</style>
