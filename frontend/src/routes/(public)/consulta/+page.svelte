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
  /consulta — seguimiento ciudadano por código de verificación.

  **Sin cuenta**: quien consulta no es usuario del sistema. El código que se le
  entregó al radicar es su credencial, y el backend recorta a nivel público, así
  que un código no alcanza nunca un radicado clasificado.

  La capacidad existía en el backend desde E13 y **no tenía ninguna pantalla**:
  era una función construida a la que nadie podía llegar.
-->
<script>
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  let codigo = "";
  let cargando = false;
  /** @type {any|null} */
  let resultado = null;
  let error = "";

  const ESTADOS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado",
    anulado: "Anulado",
  };

  const TIPOS = { E: "Entrada", S: "Salida", I: "Interno" };

  async function consultar() {
    error = "";
    resultado = null;
    const limpio = codigo.trim();
    if (!limpio) {
      error = "Escriba el código de verificación que recibió al radicar.";
      return;
    }
    cargando = true;
    try {
      const res = await fetch(`/consulta/api?codigo=${encodeURIComponent(limpio)}`);
      const cuerpo = await res.json().catch(() => null);
      if (!res.ok) {
        // `mensajeError` y no `cuerpo.detail`: el sobre de este proyecto va
        // ANIDADO y leerlo plano es el defecto que dejó un aviso diciendo
        // «[object Object]». Un guardián del repositorio lo caza.
        error = mensajeError(cuerpo, "No se pudo consultar en este momento.");
        return;
      }
      resultado = cuerpo;
    } catch {
      error = "No se pudo consultar en este momento. Revise su conexión.";
    } finally {
      cargando = false;
    }
  }
</script>

<svelte:head>
  <title>Consultar mi trámite — Docuboto</title>
  <meta
    name="description"
    content="Consulte el estado de su trámite con el código de verificación que recibió al radicar."
  />
</svelte:head>

<div class="cons">
  <div class="cons__card">
    <a class="cons__volver" href="/">← Inicio</a>
    <h1 class="cons__title">Consultar mi trámite</h1>
    <p class="cons__lead">
      Escriba el código de verificación que le entregamos al radicar. No necesita crear una cuenta
      ni identificarse.
    </p>

    <form class="cons__form" on:submit|preventDefault={consultar}>
      <label class="cons__label" for="codigo">Código de verificación</label>
      <input
        id="codigo"
        class="cons__input"
        type="text"
        bind:value={codigo}
        placeholder="Por ejemplo: bde8e66dbee949b887f904033bb1fc4c"
        autocomplete="off"
        spellcheck="false"
        aria-describedby="codigo-ayuda"
      />
      <p class="cons__hint" id="codigo-ayuda">
        Son 32 caracteres. Es distinto del número de radicado: el número identifica el documento y
        el código es lo que le permite consultarlo sin identificarse.
      </p>
      <button class="cons__btn" type="submit" disabled={cargando}>
        {cargando ? "Consultando…" : "Consultar"}
      </button>
    </form>

    {#if error}
      <p class="cons__error" role="alert">{error}</p>
    {/if}

    {#if resultado}
      <section class="cons__resultado" aria-live="polite">
        <h2 class="cons__resultado-title">Su trámite</h2>
        <dl class="cons__dl">
          <div>
            <dt>Número de radicado</dt>
            <dd><code class="op-dato">{resultado.tracking_number}</code></dd>
          </div>
          <div>
            <dt>Tipo</dt>
            <dd>{TIPOS[resultado.doc_type] ?? resultado.doc_type}</dd>
          </div>
          <div>
            <dt>Estado</dt>
            <dd>{ESTADOS[resultado.status] ?? resultado.status}</dd>
          </div>
          <div>
            <dt>Fecha de radicación</dt>
            <dd>{formatearFechaHora(resultado.registered_at)}</dd>
          </div>
          {#if resultado.dest_dept}
            <div>
              <dt>Dependencia responsable</dt>
              <dd>{resultado.dest_dept}</dd>
            </div>
          {/if}
        </dl>
        <p class="cons__nota">
          Esta consulta muestra el estado del trámite, no su contenido. Para el detalle, diríjase a
          la entidad con su número de radicado.
        </p>
      </section>
    {/if}
  </div>
</div>

<style lang="scss">
  .cons {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--op-space-5) var(--op-space-4);
    background: linear-gradient(160deg, var(--op-brand-900), var(--op-brand-700));

    &__card {
      width: 100%;
      max-width: 640px;
      background: #fff;
      border-radius: var(--op-radius);
      border-top: 4px solid var(--op-primary);
      box-shadow: var(--op-shadow-sobre-oscuro);
      padding: var(--op-space-5);
    }

    &__volver {
      display: inline-block;
      margin-bottom: var(--op-space-3);
      color: var(--op-primary-dark);
      font-size: var(--op-font-sm);
    }

    &__title {
      margin: 0 0 var(--op-space-2);
      color: var(--op-text);
    }

    &__lead {
      margin: 0 0 var(--op-space-4);
      color: var(--op-text-secondary);
    }

    &__form {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__label {
      font-weight: 600;
      color: var(--op-text);
    }

    &__input {
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      font-family: var(--op-font-family-mono);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__hint {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__btn {
      align-self: flex-start;
      margin-top: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-5);
      border: 0;
      border-radius: var(--op-radius);
      background: var(--op-primary);
      color: #fff;
      font-weight: 600;
      cursor: pointer;

      &:hover:not(:disabled) {
        background: var(--op-primary-dark);
      }
      &:disabled {
        opacity: 0.6;
        cursor: default;
      }
      &:focus-visible {
        outline: 2px solid var(--op-primary-dark);
        outline-offset: 2px;
      }
    }

    &__error {
      margin-top: var(--op-space-4);
      padding: var(--op-space-3);
      border-radius: var(--op-radius);
      background: var(--op-error-bg, #fdecea);
      color: var(--op-error-fg, #8a1c1c);
    }

    &__resultado {
      margin-top: var(--op-space-5);
      padding-top: var(--op-space-4);
      border-top: 1px solid var(--op-border);
    }

    &__resultado-title {
      margin: 0 0 var(--op-space-3);
      font-size: var(--op-font-lg);
      color: var(--op-text);
    }

    &__dl {
      margin: 0;
      display: grid;
      gap: var(--op-space-3);

      div {
        display: grid;
        gap: 2px;
      }

      dt {
        font-size: var(--op-font-sm);
        color: var(--op-text-secondary);
      }

      dd {
        margin: 0;
        color: var(--op-text);
      }
    }

    &__nota {
      margin: var(--op-space-4) 0 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }
  }
</style>
