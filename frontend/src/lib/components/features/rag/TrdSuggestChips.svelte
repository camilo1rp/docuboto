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
  TrdSuggestChips.svelte — Chips de tipo documental sugerido junto al selector de
  clasificación de radicación (E21 Inc.2 Patrón A, F6 frontend).

  Consulta `endpoint` (proxy SSR /radicar/api/rag/antecedentes) con `query` (el
  asunto) y muestra chips con el `doc_class` más frecuente entre los precedentes
  similares. NO existe en el backend un endpoint que sugiera `trd_serie_id`
  directamente sobre un radicado nuevo (ver docstring del proxy) — este componente
  sugiere el TIPO DOCUMENTAL (el selector de clasificación real del paso 1), no una
  serie TRD inventada.

  Regla dura #2 (no negociable): el chip PROPONE, la persona DECIDE. Este
  componente nunca asigna `docClass` por sí solo — solo lo hace al pulsar el chip,
  un acto explícito del usuario, mediante el evento `apply`.

  Props:
    endpoint  string  — proxy SSR (ej. "/radicar/api/rag/antecedentes")
    query     string  — texto que alimenta la búsqueda de precedentes (el asunto)
    minQueryLength number (default 3)

  Eventos:
    apply  { docClass: string } — el padre debe asignarlo a su campo de clasificación.
-->
<script>
  import { createEventDispatcher } from "svelte";

  export let endpoint;
  export let query = "";
  export let minQueryLength = 3;

  const dispatch = createEventDispatcher();

  /** @type {Array<{doc_class: string, count: number, best_score: number, example_ref: string}>} */
  let suggestions = [];
  let loading = false;

  /** @type {ReturnType<typeof setTimeout>|undefined} */
  let debounceHandle;

  function scheduleFetch(value) {
    clearTimeout(debounceHandle);
    if (value.trim().length < minQueryLength) {
      suggestions = [];
      return;
    }
    debounceHandle = setTimeout(() => fetchSuggestions(value.trim()), 400);
  }

  async function fetchSuggestions(q) {
    loading = true;
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      if (!res.ok) {
        // Decorativo/opcional: un fallo aquí NUNCA debe alertar al usuario que
        // está radicando; simplemente no se muestran chips.
        suggestions = [];
        return;
      }
      const data = await res.json().catch(() => ({}));
      suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    } catch {
      suggestions = [];
    } finally {
      loading = false;
    }
  }

  $: scheduleFetch(query ?? "");

  function apply(docClass) {
    dispatch("apply", { docClass });
  }
</script>

{#if loading || suggestions.length > 0}
  <div class="trd-chips" role="group" aria-label="Tipo documental sugerido por precedentes">
    <span class="trd-chips__label">
      <i class="fas fa-magic" aria-hidden="true"></i>
      Sugerido por precedentes (no se aplica solo):
    </span>
    {#if loading}
      <span class="trd-chips__loading">Buscando…</span>
    {:else}
      {#each suggestions as s (s.doc_class)}
        <button
          type="button"
          class="trd-chips__chip"
          title="Visto en {s.count} precedente(s) similares, ej. {s.example_ref}"
          on:click={() => apply(s.doc_class)}
        >
          {s.doc_class}
          <span class="trd-chips__count">×{s.count}</span>
        </button>
      {/each}
    {/if}
  </div>
{/if}

<style lang="scss">
  .trd-chips {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    margin-top: var(--op-space-1);
    font-size: var(--op-font-xs);

    &__label {
      color: var(--op-text-secondary);

      i {
        color: var(--op-primary-dark);
        margin-right: var(--op-space-1);
      }
    }

    &__loading {
      color: var(--op-text-secondary);
    }

    &__chip {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-full);
      border: 1px solid var(--op-primary);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      font-family: var(--op-font-family);
      cursor: pointer;
      transition:
        background var(--op-transition),
        color var(--op-transition);

      &:hover {
        background: var(--op-primary);
        color: var(--op-surface);
      }
      &:focus-visible {
        outline: none;
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }
    }

    &__count {
      font-weight: var(--op-weight-bold);
    }
  }
</style>
