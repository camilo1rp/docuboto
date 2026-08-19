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
  AssistantDock.svelte — Panel lateral del asistente conversacional (E22/E18).

  Traduce lenguaje natural a *tool calls* de la capa MCP vía el gateway
  (heredan auth + RBAC + tenant + auditoría) — este componente solo dibuja
  la conversación, nunca ejecuta acciones por su cuenta.

  Conectado a:
    - $lib/stores/assistant.js — estado de la conversación (turnos, sending/error/unavailable)
    - $lib/services/assistant.service.js — POST /asistente/api/message (proxy SSR, D-05)

  El backend real (mcp-server, E18) es scaffold: si el proxy responde
  `assistant_unavailable` (backend ausente/501), se muestra un estado
  HONESTO — nunca una respuesta simulada.

  El dictado (VoiceInput) rellena el compositor y NUNCA envía ni confirma; solo
  se ofrece si el navegador reconoce EN EL DISPOSITIVO — ver la cabecera de ese
  componente, donde está el motivo.

  Diferidos explícitos de este slice: subida de anexos, streaming (SSE),
  persistencia de historial entre recargas (el hilo vive solo en memoria del
  store; "Nueva conversación" lo limpia).
-->
<script>
  import { onMount, tick } from "svelte";
  import {
    assistantState,
    send as sendAssistant,
    reset as resetAssistant,
    confirmar as confirmarPropuestaStore,
    descartar as descartarPropuesta,
  } from "$lib/stores/assistant.js";
  import Button from "$lib/components/ui/Button.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import ProposalCard from "./ProposalCard.svelte";
  import VoiceInput from "$lib/components/ui/VoiceInput.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** Controla si el dock está visible */
  export let open = false;

  /** Manejador de cierre (padre lo gestiona) */
  export let onClose = () => {};

  // Base del compositor sobre la que se pinta lo que el reconocedor aún está
  // decidiendo. Sin ella, cada resultado parcial se acumularía y la persona
  // vería la frase repetida palabra a palabra.
  let baseDictado = "";

  /**
   * Texto dictado → compositor. Lo definitivo se fija y pasa a ser la nueva
   * base; lo parcial se muestra encima y se reemplaza en la siguiente vuelta.
   *
   * @param {{texto: string, definitivo: boolean}} detalle
   */
  function recibirDictado({ texto, definitivo }) {
    if (definitivo) {
      baseDictado =
        `${baseDictado}${baseDictado && !baseDictado.endsWith(" ") ? " " : ""}${texto}`.trimStart();
      draft = baseDictado;
    } else {
      draft =
        `${baseDictado}${baseDictado && !baseDictado.endsWith(" ") ? " " : ""}${texto}`.trimStart();
    }
  }

  // Marco de alcance (bloque A). Se carga al abrir el panel, no al montarlo: no
  // tiene sentido consultar permisos de un panel que nadie ha abierto.
  //
  // `alcanceError` se distingue de `alcance vacío` a propósito. Un panel de
  // capacidades en blanco por un fallo de red es indistinguible de un usuario
  // sin permisos, y esa confusión manda a la gente a pedir al administrador algo
  // que ya tiene.
  /** @type {{puede_hacer?: any[], puede_consultar?: any[], no_puede?: any[], acceso?: any} | null} */
  let alcance = null;
  let alcanceError = "";
  let alcanceCargando = false;

  async function cargarAlcance() {
    if (alcance || alcanceCargando) return;
    alcanceCargando = true;
    alcanceError = "";
    try {
      const res = await fetch("/asistente/api/alcance");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alcanceError = mensajeError(body, "No se pudo consultar qué acciones tiene disponibles.");
        return;
      }
      alcance = await res.json();
    } catch {
      alcanceError = "No se pudo consultar qué acciones tiene disponibles.";
    } finally {
      alcanceCargando = false;
    }
  }

  $: if (open) cargarAlcance();

  // ---------------------------------------------------------------------------
  // Trampa de foco (patrón de Modal.svelte)
  // ---------------------------------------------------------------------------
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  let dockEl;
  let closeBtnEl;
  let inputEl;
  /** @type {Element|null} */
  let previousFocus = null;
  /** Estado anterior de `open` — booleano a propósito: ver el bloque de abajo. */
  let dockAbiertoPrev = false;

  function getFocusable() {
    return dockEl ? Array.from(dockEl.querySelectorAll(FOCUSABLE)) : [];
  }

  function trapFocus(e) {
    if (!open || !dockEl) return;
    const focusable = getFocusable();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === "Tab") {
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    if (e.key === "Escape") {
      onClose();
    }
  }

  // Guard `typeof document !== "undefined"` (D-16): este bloque reactivo corre
  // también durante el render SSR — si `open` llega en true en el primer
  // render del servidor, acceder a `document` sin guardar revienta con
  // ReferenceError y devuelve 500 en toda ruta que monte AssistantDock.
  // El gate compara `open` con el estado ANTERIOR, y ese estado es un BOOLEANO.
  //
  // La versión previa gobernaba la condición con `previousFocus`: lo asignaba en
  // una rama y lo leía en la otra. Un bloque reactivo depende de todo lo que
  // lee, así que cada asignación lo reprograma; y `document.activeElement` es un
  // objeto, que `safe_not_equal` da por distinto **siempre**, aunque sea el
  // mismo elemento. No es el bloque que tumbaba la pestaña —ese era el del
  // autoscroll, más abajo—, pero es el mismo antipatrón y estaba a un cambio de
  // comportarse igual. Con un booleano el ciclo converge: tras la primera
  // pasada `open` y `dockAbiertoPrev` coinciden y no se vuelve a entrar.
  $: if (typeof document !== "undefined" && open !== dockAbiertoPrev) {
    dockAbiertoPrev = open;
    if (open) {
      previousFocus = document.activeElement;
      tick().then(() => {
        // Foco al campo de mensaje al abrir: es un dock de chat, el usuario debe
        // poder escribir de inmediato. `closeBtnEl` como respaldo si el textarea
        // aún no está montado (ambos dentro del focus-trap, así que el trap se
        // mantiene en cualquier caso).
        (inputEl ?? closeBtnEl)?.focus?.();
      });
    } else if (previousFocus) {
      // Copia local + limpieza inmediata (no dentro del `.then`): evita que un
      // cierre/apertura rápidos del dock pisen la referencia en vuelo de un
      // `tick()` anterior o la dejen en null antes de tiempo.
      const elementToRefocus = /** @type {HTMLElement} */ (previousFocus);
      previousFocus = null;
      tick().then(() => {
        elementToRefocus?.focus?.();
      });
    }
  }

  // La limpieza va como retorno de onMount (solo cliente): onDestroy se ejecuta
  // también en SSR, donde `document` no existe y rompería el render del layout.
  onMount(() => {
    document.addEventListener("keydown", trapFocus);
    return () => document.removeEventListener("keydown", trapFocus);
  });

  // ---------------------------------------------------------------------------
  // Chat — estado de la conversación (E22/E18)
  // ---------------------------------------------------------------------------
  let draft = "";
  let messagesEl;

  /** Envía el borrador actual. El turno del usuario se conserva aunque falle. */
  function handleSubmit() {
    const texto = draft.trim();
    if (!texto || $assistantState.estado === "sending") return;
    draft = "";
    // Sin esto, el siguiente dictado se pegaría detrás del mensaje ya enviado.
    baseDictado = "";
    sendAssistant(texto);
  }

  /** Enter envía; Shift+Enter inserta salto de línea. */
  function handleKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  // Autoscroll al último turno — guardado (D-16): solo corre en el navegador,
  // `messagesEl` es `undefined` durante SSR y el acceso a `window`/DOM ahí
  // rompería el render del layout.
  //
  // La lectura de `messagesEl` va en una FUNCIÓN APARTE, y eso no es estilo:
  // Svelte calcula las dependencias de un bloque reactivo con las variables que
  // aparecen en su cuerpo, **incluidas las que se leen dentro de un callback**.
  // Con `messagesEl` escrito aquí dentro, el bloque dependía de él; y como
  // `bind:this` lo asigna al montar el elemento, cada asignación reprogramaba el
  // bloque, que volvía a agendar un `tick()`, que volvía a asignar… El
  // renderizador se quedaba sin memoria y **Chrome mataba la pestaña al abrir el
  // asistente**: no un aviso, no una pantalla en blanco — la ventana entera
  // inservible. Sacando la lectura del statement, `messagesEl` deja de ser
  // dependencia y el ciclo desaparece; el comportamiento visible es idéntico.
  function irAlUltimoTurno() {
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  $: if (typeof window !== "undefined" && $assistantState.messages) {
    tick().then(irAlUltimoTurno);
  }
</script>

{#if open}
  <div
    class="assistant-dock__backdrop"
    role="presentation"
    on:click={onClose}
    on:keydown={() => {}}
  ></div>

  <aside
    bind:this={dockEl}
    class="assistant-dock"
    role="dialog"
    aria-modal="true"
    aria-label="Asistente conversacional"
  >
    <div class="assistant-dock__header">
      <span class="assistant-dock__title">
        <i class="fas fa-robot" aria-hidden="true"></i>
        Asistente OrpycaMCP
      </span>
      <div class="assistant-dock__header-actions">
        <button
          class="assistant-dock__reset"
          type="button"
          disabled={$assistantState.messages.length === 0}
          aria-label="Iniciar una nueva conversación"
          title="Nueva conversación"
          on:click={() => {
            baseDictado = "";
            resetAssistant();
          }}
        >
          <i class="fas fa-rotate-left" aria-hidden="true"></i>
        </button>
        <button
          bind:this={closeBtnEl}
          class="assistant-dock__close"
          type="button"
          aria-label="Cerrar asistente"
          on:click={onClose}
        >
          <i class="fas fa-times" aria-hidden="true"></i>
        </button>
      </div>
    </div>

    <!--
      Live region acotada a la lista de turnos (no a todo el <aside>): así
      solo se anuncian los mensajes nuevos, no el chrome del dock (header,
      formulario). `role="log"` es el patrón ARIA recomendado para chats.
    -->
    <div
      class="assistant-dock__body"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      bind:this={messagesEl}
    >
      {#if $assistantState.messages.length === 0 && $assistantState.estado === "idle"}
        <div class="assistant-dock__placeholder">
          <i class="fas fa-comments" aria-hidden="true"></i>
          <p>Escribe una consulta para comenzar.</p>

          {#if alcanceCargando}
            <p class="assistant-dock__placeholder-sub">Consultando qué puedes hacer aquí…</p>
          {:else if alcanceError}
            <p class="assistant-dock__scope-error" role="status">{alcanceError}</p>
          {:else if alcance}
            <div class="assistant-dock__scope">
              {#if alcance.puede_consultar?.length}
                <p class="assistant-dock__scope-title">Puedes consultar</p>
                <ul>
                  {#each alcance.puede_consultar as op (op.herramienta)}
                    <li>{op.operacion}</li>
                  {/each}
                </ul>
              {/if}

              {#if alcance.puede_hacer?.length}
                <p class="assistant-dock__scope-title">Puedes ejecutar</p>
                <ul>
                  {#each alcance.puede_hacer as op (op.herramienta)}
                    <li>{op.operacion}</li>
                  {/each}
                </ul>
              {/if}

              {#if alcance.no_puede?.length}
                <p class="assistant-dock__scope-title">Con tus permisos actuales, no</p>
                <ul>
                  {#each alcance.no_puede as op (op.herramienta)}
                    <li>
                      {op.operacion}
                      {#if op.via_alternativa}
                        <span class="assistant-dock__scope-via">→ {op.via_alternativa}</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {/if}

              {#if alcance.acceso}
                <p class="assistant-dock__scope-nivel">
                  Ves información hasta el nivel <strong>{alcance.acceso.etiqueta}</strong>.
                </p>
              {/if}
            </div>
          {/if}
        </div>
      {/if}

      {#each $assistantState.messages as turn (turn.id)}
        <div class="assistant-turn assistant-turn--{turn.role}">
          <span class="sr-only">{turn.role === "user" ? "Tú" : "Asistente"}:</span>
          {#if turn.html}
            <!-- HTML saneado server-side ($lib/server/sanitizeHtml.js) en el proxy — nunca crudo (D-05).
                 El saneo es la barrera; el `{@html}` aqui es su consumidor, no un atajo. -->
            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
            <div class="assistant-turn__bubble">{@html turn.html}</div>
          {:else}
            <p class="assistant-turn__bubble assistant-turn__bubble--plain">{turn.texto}</p>
          {/if}
        </div>
      {/each}

      <!--
        Las tarjetas van DESPUÉS de los turnos y no intercaladas: son actos
        pendientes de una decisión, no parte del hilo de conversación. Una que
        quedara enterrada bajo mensajes posteriores sería una acción que la
        persona cree haber dejado zanjada y sigue esperándola.
      -->
      {#each $assistantState.propuestas as propuesta (propuesta.id)}
        <ProposalCard
          {propuesta}
          estado={propuesta.estado}
          detalleError={propuesta.detalleError ?? ""}
          onConfirmar={({ frase }) => confirmarPropuestaStore(propuesta.id, frase)}
          onCancelar={() => descartarPropuesta(propuesta.id)}
        />
      {/each}

      {#if $assistantState.estado === "sending"}
        <div class="assistant-turn assistant-turn--assistant assistant-turn--pending">
          <Loader type="spinner" size="sm" label="El asistente está escribiendo…" />
        </div>
      {/if}
    </div>

    {#if $assistantState.estado === "unavailable"}
      <div class="assistant-dock__banner assistant-dock__banner--info" role="status">
        <i class="fas fa-circle-info" aria-hidden="true"></i>
        <span>El asistente aún no está disponible. Esta función se activará próximamente.</span>
      </div>
    {:else if $assistantState.estado === "error"}
      <div class="assistant-dock__banner assistant-dock__banner--error" role="alert">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        <span
          >{$assistantState.errorDetail ?? "No se pudo enviar el mensaje. Intente de nuevo."}</span
        >
      </div>
    {/if}

    <form class="assistant-dock__form" on:submit|preventDefault={handleSubmit}>
      <label for="assistant-dock-input" class="sr-only">Mensaje para el asistente</label>
      <textarea
        id="assistant-dock-input"
        class="assistant-dock__input"
        bind:this={inputEl}
        bind:value={draft}
        on:keydown={handleKeydown}
        rows="2"
        maxlength="4000"
        placeholder="Escribe tu mensaje…"
        disabled={$assistantState.estado === "sending"}
      ></textarea>
      <!--
        El dictado RELLENA el compositor y ahí se acaba: nunca envía, y nunca
        confirma una propuesta. Una acción con consecuencias sobre un registro
        oficial no puede dispararse por lo que el reconocedor creyó oír; la
        tarjeta se pulsa con la mano.
      -->
      <VoiceInput
        disabled={$assistantState.estado === "sending"}
        on:texto={(e) => recibirDictado(e.detail)}
      />
      <Button
        type="submit"
        variant="primary"
        size="sm"
        icon="fas fa-paper-plane"
        loading={$assistantState.estado === "sending"}
        disabled={!draft.trim() || $assistantState.estado === "sending"}
      >
        <span class="sr-only">Enviar mensaje</span>
      </Button>
    </form>
  </aside>
{/if}

<style lang="scss">
  .assistant-dock {
    position: fixed;
    top: 0;
    right: 0;
    width: 360px;
    max-width: 100vw;
    height: 100vh;
    background: var(--op-surface);
    border-left: 1px solid var(--op-border);
    box-shadow: var(--op-shadow-lg);
    display: flex;
    flex-direction: column;
    z-index: 300;

    &__backdrop {
      position: fixed;
      inset: 0;
      background: rgba(33, 36, 33, 0.25);
      z-index: 299;
    }

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 var(--op-space-4);
      height: var(--op-topbar-height);
      border-bottom: 1px solid var(--op-border);
      flex-shrink: 0;
    }

    &__title {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }

    &__header-actions {
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
    }

    &__close,
    &__reset {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      padding: var(--op-space-2);
      border-radius: var(--op-radius-sm);
      display: flex;
      align-items: center;

      &:hover:not(:disabled) {
        background: var(--op-bg);
        color: var(--op-text);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }

      &:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
    }

    &__body {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      padding: var(--op-space-4);
    }

    &__scope {
      text-align: left;
      margin-top: var(--op-space-3);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);

      ul {
        margin: 0 0 var(--op-space-2);
        padding-left: var(--op-space-4);
        list-style: disc;
      }

      li {
        margin-bottom: 2px;
      }
    }

    &__scope-title {
      font-weight: 600;
      color: var(--op-text);
      margin: var(--op-space-2) 0 2px;
    }

    &__scope-via {
      display: block;
      color: var(--op-text-secondary);
      font-style: italic;
    }

    &__scope-nivel {
      margin: var(--op-space-2) 0 0;
    }

    &__scope-error {
      font-size: var(--op-font-xs);
      color: var(--op-warning, var(--op-text-secondary));
      margin-top: var(--op-space-3);
    }

    &__placeholder {
      margin: auto;
      text-align: center;
      color: var(--op-text-secondary);

      i {
        font-size: 2.5rem;
        margin-bottom: var(--op-space-4);
        opacity: 0.3;
        display: block;
      }

      p {
        font-size: var(--op-font-base);
        margin-bottom: var(--op-space-2);
      }
    }

    &__placeholder-sub {
      font-size: var(--op-font-sm) !important;
      opacity: 0.7;
    }

    &__banner {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      margin: 0 var(--op-space-4) var(--op-space-3);
      padding: var(--op-space-3);
      border-radius: var(--op-radius-sm);
      font-size: var(--op-font-sm);
      flex-shrink: 0;

      &--info {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
      }

      &--error {
        background: var(--op-error-bg);
        color: var(--op-error-fg);
      }

      i {
        margin-top: 2px;
        flex-shrink: 0;
      }
    }

    &__form {
      display: flex;
      align-items: flex-end;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      border-top: 1px solid var(--op-border);
      flex-shrink: 0;
    }

    &__input {
      flex: 1;
      resize: none;
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-2) var(--op-space-3);

      &:focus-visible,
      &:focus {
        outline: 2px solid var(--op-primary);
        outline-offset: 1px;
        border-color: var(--op-primary);
      }

      &:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
    }
  }

  .assistant-turn {
    display: flex;
    max-width: 88%;

    &--user {
      align-self: flex-end;

      .assistant-turn__bubble {
        background: var(--op-primary-dark);
        color: var(--op-surface);
        border-bottom-right-radius: 2px;
      }
    }

    &--assistant {
      align-self: flex-start;

      .assistant-turn__bubble {
        background: var(--op-bg);
        color: var(--op-text);
        border-bottom-left-radius: 2px;
      }
    }

    &--pending {
      padding: var(--op-space-2) 0;
    }

    &__bubble {
      margin: 0;
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
      line-height: 1.45;
      overflow-wrap: break-word;

      &--plain {
        white-space: pre-wrap;
      }

      :global(p) {
        margin: 0 0 var(--op-space-2);
        &:last-child {
          margin-bottom: 0;
        }
      }

      :global(a) {
        color: inherit;
        text-decoration: underline;
      }

      :global(code) {
        background: var(--op-border);
        color: var(--op-text);
        border-radius: 3px;
        padding: 0 3px;
      }
    }
  }

  @media (max-width: 600px) {
    .assistant-dock {
      width: 100vw;
    }
  }
</style>
