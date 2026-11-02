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
  PreguntarPanel.svelte — pregunta sobre el radicado abierto y sus anexos (ADR-045 §5).

  Solo se pinta si la pasarela dice que este nivel se transmite y puede nombrar
  la ruta. La pregunta sale con alcance a este radicado. Si responde el índice
  local, se dice. Las citas enlazan el anexo y la página, o el radicado citado.
-->
<script>
  import { onDestroy } from "svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import { getNivelChip } from "$lib/utils/nivelSeguridad.js";
  import { mensajeConocimiento } from "$lib/utils/asistenteAcceso.js";
  import { avisoTransmision, enlaceCita } from "$lib/utils/preguntar.js";

  /** @type {string} */
  export let radicadoId;
  /** @type {number} */
  export let nivel;
  /** @type {Array<{id?: string, file_id?: string, filename?: string}>} */
  export let anexos = [];

  let aviso = "";
  let pregunta = "";
  let enviando = false;
  let errorMsg = "";
  /** @type {any} */
  let respuesta = null;
  let generacion = 0;
  let vivo = true;

  onDestroy(() => {
    vivo = false;
  });

  /**
   * @param {string} id
   * @param {number} nivelNumero
   */
  async function consultarRuta(id, nivelNumero) {
    const marca = ++generacion;
    aviso = "";
    pregunta = "";
    errorMsg = "";
    respuesta = null;
    try {
      const res = await fetch("/bandeja/api/preguntar/transmision");
      if (!vivo || marca !== generacion || !res.ok) return;
      const cuerpo = await res.json().catch(() => null);
      if (!vivo || marca !== generacion || cuerpo?.transmits !== true) return;
      const fila = (cuerpo.levels ?? []).find((n) => n.level === nivelNumero);
      aviso = avisoTransmision(cuerpo.provider, fila, getNivelChip(nivelNumero).label) ?? "";
    } catch {
      aviso = "";
    }
  }

  $: consultarRuta(radicadoId, Number(nivel));

  async function enviar() {
    const texto = pregunta.trim();
    if (texto.length < 2 || enviando) return;
    enviando = true;
    errorMsg = "";
    respuesta = null;
    try {
      const res = await fetch("/bandeja/api/preguntar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: texto, record_ref: radicadoId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        errorMsg = mensajeConocimiento(
          data,
          "No se pudo obtener una respuesta. Inténtelo de nuevo.",
        );
        return;
      }
      respuesta = data;
    } catch {
      errorMsg = "No se pudo obtener una respuesta. Inténtelo de nuevo.";
    } finally {
      enviando = false;
    }
  }
</script>

{#if aviso}
  <section class="preguntar" aria-labelledby="preguntar-{radicadoId}">
    <h4 id="preguntar-{radicadoId}" class="preguntar__titulo">Preguntar</h4>
    <form class="preguntar__form" on:submit|preventDefault={enviar}>
      <label class="preguntar__label" for="preguntar-texto-{radicadoId}">
        Pregunta sobre este radicado
      </label>
      <textarea
        id="preguntar-texto-{radicadoId}"
        class="preguntar__texto"
        rows="3"
        maxlength="4000"
        bind:value={pregunta}
        aria-describedby="preguntar-ruta-{radicadoId}"
        disabled={enviando}
      ></textarea>
      <p id="preguntar-ruta-{radicadoId}" class="preguntar__nota" role="note">{aviso}</p>
      <Button type="submit" disabled={pregunta.trim().length < 2} loading={enviando}>
        Preguntar
      </Button>
    </form>

    <div class="preguntar__resultado" aria-live="polite">
      {#if errorMsg}
        <p class="preguntar__error" role="alert">{errorMsg}</p>
      {/if}
      {#if respuesta}
        {#if respuesta.origin === "local_index"}
          <p class="preguntar__origen">Respuesta del índice local.</p>
        {/if}
        {#if respuesta.advisory}
          <p class="preguntar__advisory">{respuesta.advisory}</p>
        {/if}
        {#if respuesta.answer}
          <p class="preguntar__answer">{respuesta.answer}</p>
        {/if}
        {#if respuesta.citations?.length}
          <ul class="preguntar__citas">
            {#each respuesta.citations as cita, indice (indice)}
              {@const enlace = enlaceCita(cita, radicadoId, anexos)}
              <li>
                <p>{cita.cited_text}</p>
                {#if enlace}
                  <a href={enlace.href}>{enlace.nombre}</a>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    </div>
  </section>
{/if}

<style>
  .preguntar {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    margin: var(--op-space-4) 0;
  }

  .preguntar__titulo {
    margin: 0;
    font-size: var(--op-font-sm);
    font-weight: 600;
  }

  .preguntar__form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
  }

  .preguntar__label {
    font-size: var(--op-font-sm);
  }

  .preguntar__texto {
    width: 100%;
    min-height: 4.5rem;
    resize: vertical;
    font: inherit;
  }

  .preguntar__nota {
    margin: 0;
    padding: var(--op-space-2) var(--op-space-3);
    border-left: 3px solid var(--op-warning, #b45309);
    font-size: var(--op-font-sm);
  }

  .preguntar__origen {
    margin: 0;
    font-weight: 600;
  }

  .preguntar__advisory,
  .preguntar__answer,
  .preguntar__error {
    margin: 0;
    font-size: var(--op-font-sm);
    white-space: pre-wrap;
  }

  .preguntar__error {
    color: var(--op-danger, #b91c1c);
  }

  .preguntar__citas {
    margin: 0;
    padding-left: var(--op-space-4);
  }

  .preguntar__citas p {
    margin: 0;
  }
</style>
