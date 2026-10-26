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
  EntidadNoOperativa — Pantalla que sustituye al layout privado cuando el gateway
  rechaza la sesión por el estado de la entidad (ADR-035).

  No redirige al login (el login con sesión vuelve al panel: bucle) ni cierra la
  sesión por su cuenta; ofrece reintentar (si el estado puede cambiar solo) y
  salir. El cierre de sesión es un POST a /logout, como en el resto de la app.
-->
<script>
  import { invalidateAll } from "$app/navigation";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { TEXTOS_ENTIDAD } from "$lib/utils/estadoEntidad.js";

  /** @type {import('$lib/utils/estadoEntidad.js').EstadoEntidad} */
  export let estado;

  let reintentando = false;
  /** @type {string} */
  let sinCambio = "";
  /** @type {HTMLElement|undefined} */
  let titulo;

  $: texto = TEXTOS_ENTIDAD[estado] ?? TEXTOS_ENTIDAD.sin_comprobar;

  async function reintentar() {
    reintentando = true;
    sinCambio = "";
    try {
      await invalidateAll();
      // Si el componente sigue montado, el estado no cambió: se dice y el foco
      // vuelve al título para que el lector de pantalla lo anuncie.
      sinCambio = `Sigue sin estar disponible (${formatearFechaHora(new Date())}).`;
      titulo?.focus();
    } finally {
      reintentando = false;
    }
  }
</script>

<svelte:head>
  <title>{texto.titulo} — Docuboto</title>
</svelte:head>

<main class="entidad">
  <div class="entidad__card">
    <img src="/brand/orpyca-mark.svg" alt="" class="entidad__icon" aria-hidden="true" />

    <h1 class="entidad__title" tabindex="-1" bind:this={titulo}>{texto.titulo}</h1>

    <div class="entidad__message" role="status" aria-live="polite" data-estado={estado}>
      <p>{texto.cuerpo}</p>
      {#if sinCambio}<p class="entidad__sin-cambio">{sinCambio}</p>{/if}
    </div>

    <div class="entidad__actions">
      {#if texto.reintentable}
        <button
          type="button"
          class="button-primary"
          on:click={reintentar}
          disabled={reintentando}
          aria-busy={reintentando}
        >
          {reintentando ? "Comprobando…" : "Reintentar"}
        </button>
      {/if}
      <form method="POST" action="/logout">
        <button type="submit" class="button-secondary">Cerrar sesión</button>
      </form>
    </div>
  </div>
</main>

<style lang="scss">
  .entidad {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
    padding: var(--op-space-4);
  }

  .entidad__card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 460px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .entidad__icon {
    height: 40px;
    width: auto;
    margin-bottom: var(--op-space-4);
  }

  .entidad__title {
    font-family: var(--op-font-display);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-3);
  }

  .entidad__message {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin: 0 0 var(--op-space-6);

    p {
      margin: 0;
    }

    .entidad__sin-cambio {
      margin-top: var(--op-space-2);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }
  }

  .entidad__title:focus {
    outline: none;
  }

  .entidad__actions {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    align-items: center;
  }

  .button-primary {
    min-height: 44px;
    background: var(--op-primary-dark);
    color: var(--op-on-primary);
    border: 0;
    padding: var(--op-space-2) var(--op-space-5);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    cursor: pointer;

    &:hover:not(:disabled) {
      background: var(--op-brand-700);
    }

    &:disabled {
      opacity: 0.7;
      cursor: progress;
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }

  .button-secondary {
    min-height: 44px;
    padding: var(--op-space-2) var(--op-space-3);
    background: transparent;
    border: 0;
    color: var(--op-primary-dark);
    font-size: var(--op-font-sm);
    text-decoration: underline;
    cursor: pointer;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }
</style>
