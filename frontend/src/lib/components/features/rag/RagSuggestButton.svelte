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
  RagSuggestButton.svelte — Botón "Sugerir con antecedentes" (E21 Inc.3, F6 frontend).

  Llama al RAG (POST a `endpoint`, un proxy SSR — D-05) con `query` y ofrece insertar
  el texto generado en un `RichEditor` mediante el evento `insert`. Este componente
  NUNCA hace `{@html}` de la respuesta cruda ni la inserta directamente: construye el
  fragmento con `$lib/utils/ragInsert.js` (marca + citas, sobrevive el saneo
  server-side) y deja que el padre haga `value = value + evento.detail.html`.

  Reglas duras (ver prompt de la tarea F6):
    1. Sin `answer` o sin `citations.length` → NO se ofrece insertar. Se explica
       honestamente que no se pudo fundamentar una respuesta, sin inventar el motivo.
    2. Es opcional: un fallo de red o un 503 del proxy solo deshabilita este control,
       nunca el resto del formulario (el padre no depende de este componente).

  Props:
    endpoint  string   — proxy SSR (ej. "/radicar/api/rag/suggest")
    query     string   — texto que alimenta la consulta (ej. el asunto)
    disabled  boolean  — deshabilita el botón (ej. el editor está deshabilitado)
    minQueryLength number (default 2, espejo del min_length del backend)

  Eventos:
    insert  { html: string } — el padre debe anexarlo al valor del editor.
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { buildGeneratedHtml } from "$lib/utils/ragInsert.js";
  import { mensajeConocimiento } from "$lib/utils/asistenteAcceso.js";

  export let endpoint;
  export let query = "";
  export let disabled = false;
  export let minQueryLength = 2;

  const dispatch = createEventDispatcher();

  let loading = false;
  let panelOpen = false;
  let errorMsg = "";
  /** @type {import('$lib/types/api.js').RagResponse|null} */
  let result = null;
  let inserted = false;

  $: queryReady = query.trim().length >= minQueryLength;
  $: canInsert = !!result?.answer && (result?.citations?.length ?? 0) > 0;

  async function suggest() {
    if (!queryReady || loading) return;
    loading = true;
    errorMsg = "";
    result = null;
    inserted = false;
    panelOpen = true;
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Degradación honesta (regla dura #3): el error se muestra SOLO dentro de
        // este panel opcional, nunca se propaga al formulario que lo contiene.
        errorMsg = mensajeConocimiento(data, "No se pudo generar una sugerencia en este momento.");
        return;
      }
      result = data;
    } catch {
      errorMsg = "No se pudo conectar con el servicio de conocimiento.";
    } finally {
      loading = false;
    }
  }

  function insertar() {
    if (!canInsert || !result) return;
    const html = buildGeneratedHtml(result.answer, result.citations);
    dispatch("insert", { html });
    inserted = true;
  }

  function cerrarPanel() {
    panelOpen = false;
  }
</script>

<div class="rag-suggest">
  <Button
    variant="secondary"
    size="sm"
    icon="fas fa-lightbulb"
    disabled={disabled || !queryReady}
    {loading}
    on:click={suggest}
  >
    Sugerir con antecedentes
  </Button>
  {#if !queryReady}
    <span class="rag-suggest__hint">Escribe el asunto para poder sugerir contenido.</span>
  {/if}

  {#if panelOpen}
    <div
      class="rag-suggest__panel"
      role="region"
      aria-label="Sugerencia generada por IA"
      aria-live="polite"
    >
      {#if loading}
        <Loader type="skeleton" lines={3} label="Consultando antecedentes…" />
      {:else if errorMsg}
        <p class="rag-suggest__error" role="alert">
          <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
          {errorMsg}
        </p>
        <Button variant="ghost" size="sm" on:click={cerrarPanel}>Cerrar</Button>
      {:else if result}
        <div class="rag-suggest__header">
          <StatusChip status="info" label="Generado por IA" dot />
          <span class="rag-suggest__proveedor">proveedor: {result.proveedor}</span>
        </div>

        {#if canInsert}
          <p class="rag-suggest__answer">{result.answer}</p>
          <p class="rag-suggest__citations-title">Citas ({result.citations.length}):</p>
          <ul class="rag-suggest__citations">
            {#each result.citations as c, i (i)}
              <li>
                <strong>{c.source_ref}</strong> (similitud {c.score.toFixed(2)}): «{c.cited_text}»
              </li>
            {/each}
          </ul>
          <p class="rag-suggest__disclaimer">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Esta sugerencia puede excluir precedentes visibles en la búsqueda que no eran aptos para salir
            a un proveedor de generación (barrera de residencia). Verifique cada cita contra el radicado
            fuente antes de firmar sobre este texto.
          </p>
          {#if inserted}
            <p class="rag-suggest__inserted" role="status">
              <i class="fas fa-check-circle" aria-hidden="true"></i>
              Insertado en el documento, marcado como generado por IA con sus citas.
            </p>
          {:else}
            <div class="rag-suggest__actions">
              <Button variant="primary" size="sm" icon="fas fa-plus" on:click={insertar}>
                Insertar en el documento
              </Button>
              <Button variant="ghost" size="sm" on:click={cerrarPanel}>Descartar</Button>
            </div>
          {/if}
        {:else}
          <p class="rag-suggest__no-answer" role="status">
            <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
            No se pudo fundamentar una respuesta con citas para insertar en el documento.
            {#if result.citations?.length}
              Se encontraron {result.citations.length} precedente(s) relacionado(s), pero sin una síntesis
              citada no se ofrece insertarlos como texto: una afirmación sin fuente en un documento oficial
              es peor que no tener sugerencia.
            {:else}
              No se encontraron precedentes indexados para esta consulta.
            {/if}
          </p>
          <Button variant="ghost" size="sm" on:click={cerrarPanel}>Cerrar</Button>
        {/if}
      {/if}
    </div>
  {/if}
</div>

<style lang="scss">
  .rag-suggest {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    margin-bottom: var(--op-space-2);

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__panel {
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-bg);
      padding: var(--op-space-3);
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__header {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__proveedor {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__answer {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      white-space: pre-wrap;
    }

    &__citations-title {
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__citations {
      margin: 0;
      padding-left: var(--op-space-5);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__disclaimer {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      background: var(--op-surface);
      border-left: 3px solid var(--op-info);
      padding: var(--op-space-2);
      border-radius: 0 var(--op-radius) var(--op-radius) 0;
      margin: 0;

      i {
        color: var(--op-info);
        margin-right: var(--op-space-1);
      }
    }

    &__no-answer {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      background: var(--op-warning-bg);
      border-radius: var(--op-radius);
      padding: var(--op-space-2) var(--op-space-3);
      margin: 0;

      i {
        color: var(--op-warning-fg);
        margin-right: var(--op-space-1);
      }
    }

    &__inserted {
      font-size: var(--op-font-sm);
      color: var(--op-primary-dark);
      margin: 0;

      i {
        color: var(--op-success);
        margin-right: var(--op-space-1);
      }
    }

    &__error {
      font-size: var(--op-font-sm);
      color: var(--op-error);
      margin: 0;

      i {
        margin-right: var(--op-space-1);
      }
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
    }
  }
</style>
