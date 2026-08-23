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
  +error.svelte — Página de error de la aplicación.

  No existía ninguna: un 404 o un fallo del SSR caían en la pantalla por defecto
  de SvelteKit —fondo blanco, tipografía del navegador, el mensaje crudo del
  error— sin cabecera, sin marca y sin salida. El usuario de ventanilla la lee
  como «se rompió el sistema» y llama a soporte.

  Al estar en la raíz de `routes/`, cubre TODAS las rutas: las privadas, las
  públicas y las que ni siquiera existen. Es deliberadamente autónoma (no usa
  `AppLayout`): cuando el error es que el layout no pudo cargar, un error que
  depende del layout no se puede renderizar.

  El mensaje lo fija `handleError` en `hooks.server.js`; aquí no se inventa
  texto para los 5xx ni se muestra nada que el servidor no haya decidido
  publicar.
-->
<script>
  import { page } from "$app/stores";

  /** Los códigos que sí merecen un texto propio, más útil que el genérico. */
  const TEXTOS = {
    404: {
      titulo: "No encontramos esta página",
      cuerpo:
        "La dirección no existe o el documento fue movido. Revisa el enlace o vuelve al panel principal.",
    },
    403: {
      titulo: "Acceso denegado",
      cuerpo:
        "Tu cuenta no tiene permiso para esta sección. Si crees que deberías tenerlo, contacta al administrador de tu entidad.",
    },
    503: {
      titulo: "Servicio no disponible",
      cuerpo:
        "El sistema no puede atender la solicitud en este momento. Vuelve a intentarlo en unos minutos.",
    },
  };

  $: status = $page.status;
  $: propio = TEXTOS[status];
  $: titulo = propio?.titulo ?? "Ocurrió un error";
  // Para lo que no tiene texto propio se usa el mensaje del servidor, que
  // `handleError` ya dejó libre de detalle interno.
  $: cuerpo = propio?.cuerpo ?? $page.error?.message ?? "Error inesperado del servidor.";
  $: referencia = $page.error?.referencia;
</script>

<svelte:head>
  <title>{titulo} ({status}) — OrpycaMCP</title>
</svelte:head>

<main class="error-page">
  <div class="error-page__card">
    <img src="/brand/orpyca-mark.png" alt="" class="error-page__icon" aria-hidden="true" />

    <p class="error-page__code" aria-hidden="true">{status}</p>

    <h1 class="error-page__title">{titulo}</h1>

    <p class="error-page__message">{cuerpo}</p>

    {#if referencia}
      <!-- El único dato técnico que se publica. No describe el fallo: solo
           permite localizarlo en el log del servidor. -->
      <p class="error-page__ref">
        Referencia para soporte: <code>{referencia}</code>
      </p>
    {/if}

    <div class="error-page__actions">
      <a href="/dashboard" class="button-primary">Volver al panel principal</a>
      <a href="/" class="button-secondary">Ir al inicio</a>
    </div>
  </div>
</main>

<style lang="scss">
  // Tokens Orpyca disponibles via CSS custom properties --op-* (definidas en :root por _tokens.scss).

  .error-page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--op-bg);
    padding: var(--op-space-4);
  }

  .error-page__card {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-7) var(--op-space-6);
    max-width: 460px;
    width: 100%;
    text-align: center;
    box-shadow: var(--op-shadow);
  }

  .error-page__icon {
    height: 40px;
    width: auto;
    margin-bottom: var(--op-space-4);
  }

  .error-page__code {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-warning);
    letter-spacing: 2px;
    margin: 0 0 var(--op-space-2);
  }

  .error-page__title {
    font-family: var(--op-font-display);
    font-size: var(--op-font-xl);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-3);
  }

  .error-page__message {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);
    margin: 0 0 var(--op-space-4);
  }

  .error-page__ref {
    font-size: var(--op-font-xs, 0.75rem);
    color: var(--op-text-secondary);
    margin: 0 0 var(--op-space-6);

    code {
      font-family: var(--op-font-family-mono);
      background: var(--op-bg);
      padding: 0 var(--op-space-1);
      border-radius: var(--op-radius);
    }
  }

  .error-page__actions {
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

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
  }
</style>
