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
-->
<svelte:head>
  <title>Acceso denegado (403) — OrpycaMCP</title>
</svelte:head>

<main class="forbidden">
  <div class="forbidden__card">
    <div class="forbidden__icon" aria-hidden="true">
      <span class="forbidden__icon-inner">403</span>
    </div>

    <h1 class="forbidden__title">Acceso denegado</h1>

    <p class="forbidden__message">
      No tienes permiso para acceder a esta seccion. Si crees que esto es un error, contacta al
      administrador de tu entidad.
    </p>

    <div class="forbidden__actions">
      <a href="/dashboard" class="button-primary" role="button"> Volver al panel principal </a>
      <a href="/" class="button-secondary" role="button"> Ir al inicio </a>
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
    margin-bottom: var(--op-space-5);
  }

  .forbidden__icon-inner {
    display: inline-block;
    font-size: var(--op-font-2xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-warning);
    letter-spacing: 2px;
  }

  .forbidden__title {
    font-size: var(--op-font-xl);
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
    display: inline-block;
    background: var(--op-primary);
    color: var(--op-surface);
    padding: var(--op-space-2) var(--op-space-5);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    text-decoration: none;
    transition: background var(--op-transition);

    &:hover {
      background: var(--op-primary-dark);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .button-secondary {
    display: inline-block;
    color: var(--op-primary);
    font-size: var(--op-font-sm);
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
