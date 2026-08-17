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
  ProposalCard — la tarjeta con la que una persona aprueba un acto de escritura
  que el asistente ha preparado (E18/ADR-020, bloque 10).

  **La tarjeta NO es un disparador: es el diálogo.** El patrón canónico del
  proyecto (`confirmBeforeWrite.test.js`) dice que el proxy solo se invoca desde
  el botón que está DENTRO del diálogo de confirmación, y aquí se cumple porque
  la tarjeta ya es ese diálogo: aparece con el acto descrito y su botón dentro.
  Añadir un modal encima obligaría a confirmar dos veces lo mismo, y una
  confirmación que se repite se pulsa sin leer.

  **El texto lo redacta el CATÁLOGO, no el modelo.** `operacion` y las etiquetas
  salen de la declaración de la tool en el backend. El texto del asistente va en
  su burbuja, aparte. Si la tarjeta la escribiera el modelo, podría describir un
  acto distinto del que va firmado en el token, y la persona estaría aprobando
  una cosa creyendo aprobar otra.

  **Los valores se pintan como TEXTO, nunca como HTML.** Los compuso el modelo a
  partir de la conversación, que puede incluir el contenido de un radicado de
  Entrada — texto redactado por un tercero externo. Svelte los escapa por
  defecto y aquí no hay ningún `{@html}`: es el último sitio del sistema donde
  convendría relajarlo.
-->
<script>
  import Button from "$lib/components/ui/Button.svelte";

  /**
   * @type {{
   *   id: string, tool: string, operacion: string,
   *   campos: {etiqueta: string, valor: string}[],
   *   irreversible?: boolean, frase_requerida?: string|null,
   *   token: string
   * }}
   */
  export let propuesta;

  /** Estado del acto: 'pendiente' | 'ejecutando' | 'hecho' | 'cancelado' | 'error' */
  export let estado = "pendiente";
  export let detalleError = "";

  /** @type {(p: {token: string, frase: string}) => void} */
  export let onConfirmar = () => {};
  /** @type {() => void} */
  export let onCancelar = () => {};

  let frase = "";

  $: irreversible = Boolean(propuesta?.irreversible);
  $: fraseRequerida = propuesta?.frase_requerida ?? "";
  // El backend revalida esto por su cuenta, a partir del token firmado: aquí
  // solo se deshabilita el botón para no mandar a la persona contra un error
  // que ya se sabe. La barrera es la de allá; esta es cortesía.
  $: fraseOk = !irreversible || frase.trim().toUpperCase() === fraseRequerida.toUpperCase();
  $: bloqueado = estado === "ejecutando" || estado === "hecho";
</script>

<section
  class="proposal-card"
  class:proposal-card--irreversible={irreversible}
  role="group"
  aria-label={`Acción pendiente de confirmación: ${propuesta.operacion}`}
  data-testid="proposal-card"
>
  <header class="proposal-card__head">
    <i class="fas fa-clipboard-check" aria-hidden="true"></i>
    <div>
      <p class="proposal-card__op">{propuesta.operacion}</p>
      <p class="proposal-card__hint">
        Nada se ha hecho todavía. Revise los datos y confirme para llevarlo a cabo.
      </p>
    </div>
  </header>

  <dl class="proposal-card__fields">
    {#each propuesta.campos as campo, i (campo.etiqueta + i)}
      <div class="proposal-card__field">
        <dt>{campo.etiqueta}</dt>
        <dd>{campo.valor}</dd>
      </div>
    {/each}
  </dl>

  {#if irreversible && estado !== "hecho" && estado !== "cancelado"}
    <div class="proposal-card__warn" role="note">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      <span>Esta acción <strong>no se deshace</strong>.</span>
    </div>
    <label class="proposal-card__phrase" for={`frase-${propuesta.id}`}>
      Escriba <strong>{fraseRequerida}</strong> para continuar
      <input
        id={`frase-${propuesta.id}`}
        type="text"
        bind:value={frase}
        autocomplete="off"
        disabled={bloqueado}
        data-testid="proposal-phrase"
      />
    </label>
  {/if}

  {#if estado === "hecho"}
    <p class="proposal-card__done" role="status">
      <i class="fas fa-check" aria-hidden="true"></i> Hecho.
    </p>
  {:else if estado === "cancelado"}
    <p class="proposal-card__done proposal-card__done--muted" role="status">
      Descartado. No se hizo nada.
    </p>
  {:else}
    {#if estado === "error"}
      <p class="proposal-card__error" role="alert">
        {detalleError || "No se pudo llevar a cabo la acción."}
      </p>
    {/if}
    <div class="proposal-card__actions">
      <Button
        variant="ghost"
        size="sm"
        disabled={estado === "ejecutando"}
        on:click={onCancelar}
        data-testid="proposal-cancel"
      >
        Descartar
      </Button>
      <Button
        variant="primary"
        size="sm"
        loading={estado === "ejecutando"}
        disabled={!fraseOk || bloqueado}
        on:click={() => onConfirmar({ token: propuesta.token, frase: frase.trim() })}
        data-testid="proposal-confirm"
      >
        Confirmar
      </Button>
    </div>
  {/if}
</section>

<style lang="scss">
  .proposal-card {
    border: 1px solid var(--op-border);
    border-left: 3px solid var(--op-primary);
    border-radius: var(--op-radius-md);
    background: var(--op-surface);
    padding: var(--op-space-3);
    margin: var(--op-space-2) 0;
    font-size: var(--op-font-size-sm);
  }

  .proposal-card--irreversible {
    border-left-color: var(--op-danger);
  }

  .proposal-card__head {
    display: flex;
    gap: var(--op-space-2);
    align-items: flex-start;
    margin-bottom: var(--op-space-2);

    i {
      color: var(--op-primary);
      margin-top: 0.15rem;
    }
  }

  .proposal-card__op {
    font-weight: 600;
    color: var(--op-text);
  }

  .proposal-card__hint {
    color: var(--op-text-muted);
    font-size: var(--op-font-size-xs);
  }

  .proposal-card__fields {
    display: grid;
    gap: var(--op-space-1);
    margin-bottom: var(--op-space-2);
  }

  .proposal-card__field {
    display: grid;
    grid-template-columns: minmax(6rem, 35%) 1fr;
    gap: var(--op-space-2);

    dt {
      color: var(--op-text-muted);
    }

    dd {
      color: var(--op-text);
      overflow-wrap: anywhere;
    }
  }

  .proposal-card__warn {
    display: flex;
    gap: var(--op-space-2);
    align-items: center;
    color: var(--op-danger);
    margin-bottom: var(--op-space-2);
  }

  .proposal-card__phrase {
    display: block;
    color: var(--op-text-muted);
    font-size: var(--op-font-size-xs);
    margin-bottom: var(--op-space-2);

    input {
      display: block;
      width: 100%;
      margin-top: var(--op-space-1);
      padding: 0.5rem;
      min-height: 2.75rem;
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      background: var(--op-surface-alt);
      color: var(--op-text);
    }
  }

  .proposal-card__actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--op-space-2);
  }

  .proposal-card__error {
    color: var(--op-danger);
    margin-bottom: var(--op-space-2);
  }

  .proposal-card__done {
    color: var(--op-success);
    display: flex;
    gap: var(--op-space-1);
    align-items: center;
  }

  .proposal-card__done--muted {
    color: var(--op-text-muted);
  }
</style>
