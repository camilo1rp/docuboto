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
  RichEditor.svelte — Editor de texto enriquecido para cuerpos de Salida/borradores (T-12b).

  Basado en TipTap (StarterKit v3 + Link), mono-usuario (sin Yjs). Produce HTML que el
  servidor SANEA antes de persistir (`$lib/server/sanitizeHtml.js`, D-05) — el HTML de
  este componente NO es de fiar por sí solo.

  TipTap es una `optionalDependency`: se carga con import dinámico DENTRO de onMount
  (nunca en SSR, usa el DOM). Si la dependencia no está instalada, degrada a un
  `<textarea>` simple (el cuerpo sigue siendo capturable, sin formato).

  Props:
    id              string  — id del control (a11y)
    label           string  — etiqueta visible
    value           string  — HTML del cuerpo (bindable)
    placeholder     string
    disabled        boolean
    hint            string
    ariaDescribedby string  — id externo para aria-describedby

  Accesibilidad: barra de herramientas con role="toolbar"; cada botón con aria-label y
  aria-pressed según la marca activa; el área editable es un textbox multilínea con
  aria-label. Atajos nativos de TipTap (Ctrl/Cmd+B/I/U) disponibles.
-->
<script>
  import { onMount, onDestroy } from "svelte";

  export let id = "rich-editor";
  export let label = "";
  export let value = "";
  export let placeholder = "Escriba el contenido…";
  export let disabled = false;
  export let hint = "";
  export let ariaDescribedby = undefined;

  /** @type {HTMLDivElement} */
  let host;
  // Editor cargado dinámicamente (optionalDependency); sus comandos dependen de las
  // extensiones cargadas en runtime (setLink/unsetLink/toggleUnderline de StarterKit v3),
  // que el type-checker no ve sin module augmentation → se tipa como `any`.
  /** @type {any} */
  let editor = null;
  let ready = false;
  let failed = false; // optionalDependency ausente → fallback textarea
  /** Estado de marcas/nodos activos para reflejarlo en la barra. */
  let active = {};

  // Popover de enlace (sin window.prompt — los diálogos del navegador bloquean el hilo).
  let linkOpen = false;
  let linkUrl = "";
  /** @type {HTMLButtonElement} */
  let linkBtn;

  /** Action: enfoca el nodo al montarse (foco al input del popover al abrir, #4). */
  function autofocus(node) {
    node.focus();
    node.select?.();
  }

  const hintId = hint ? `${id}-hint` : undefined;
  $: describedBy = [hintId, ariaDescribedby].filter(Boolean).join(" ") || undefined;

  function refreshActive() {
    if (!editor) return;
    active = {
      bold: editor.isActive("bold"),
      italic: editor.isActive("italic"),
      underline: editor.isActive("underline"),
      strike: editor.isActive("strike"),
      code: editor.isActive("code"),
      h2: editor.isActive("heading", { level: 2 }),
      h3: editor.isActive("heading", { level: 3 }),
      bullet: editor.isActive("bulletList"),
      ordered: editor.isActive("orderedList"),
      blockquote: editor.isActive("blockquote"),
      link: editor.isActive("link"),
    };
  }

  onMount(async () => {
    try {
      const [core, sk] = await Promise.all([import("@tiptap/core"), import("@tiptap/starter-kit")]);
      // `any`: StarterKit arrastra su propia copia de @tiptap/core, cuyos tipos no
      // casan con los de este import aunque el runtime sea el mismo paquete.
      /** @type {any} */
      const Editor = core.Editor;
      /** @type {any} */
      const StarterKit = sk.default;
      editor = new Editor({
        element: host,
        editable: !disabled,
        extensions: [
          StarterKit.configure({
            // El esquema de enlaces seguro lo refuerza el saneo server-side; aquí
            // limitamos a protocolos razonables y no abrir al hacer clic en edición.
            link: {
              openOnClick: false,
              autolink: true,
              protocols: ["http", "https", "mailto"],
              HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" },
            },
          }),
        ],
        content: value || "",
        editorProps: {
          attributes: {
            role: "textbox",
            "aria-multiline": "true",
            "aria-label": label || "Editor de contenido",
            // a11y (#3): el hint/descripción se asocia al textbox REAL (el
            // contenteditable que monta TipTap), no al wrapper.
            ...(describedBy ? { "aria-describedby": describedBy } : {}),
            class: "op-rich__content",
            id,
          },
        },
        onUpdate: ({ editor: e }) => {
          value = e.getHTML();
          refreshActive();
        },
        onSelectionUpdate: refreshActive,
      });
      refreshActive();
      ready = true;
    } catch (err) {
      // optionalDependency no instalada (o error de carga) → degradar a textarea.
      console.warn("[RichEditor] TipTap no disponible, usando textarea:", err);
      failed = true;
    }
  });

  onDestroy(() => {
    if (editor) {
      editor.destroy();
      editor = null;
    }
  });

  // Sincroniza cambios externos de `value` (p. ej. carga de un borrador) sin bucle.
  $: if (editor && ready && value !== editor.getHTML()) {
    editor.commands.setContent(value || "", false);
  }
  $: if (editor) editor.setEditable(!disabled);

  // --- comandos de la barra -------------------------------------------------
  const cmd = (fn) => () => {
    if (!editor) return;
    fn(editor.chain().focus()).run();
  };
  const toggleBold = cmd((c) => c.toggleBold());
  const toggleItalic = cmd((c) => c.toggleItalic());
  const toggleUnderline = cmd((c) => c.toggleUnderline());
  const toggleStrike = cmd((c) => c.toggleStrike());
  const toggleCode = cmd((c) => c.toggleCode());
  const toggleH2 = cmd((c) => c.toggleHeading({ level: 2 }));
  const toggleH3 = cmd((c) => c.toggleHeading({ level: 3 }));
  const toggleBullet = cmd((c) => c.toggleBulletList());
  const toggleOrdered = cmd((c) => c.toggleOrderedList());
  const toggleQuote = cmd((c) => c.toggleBlockquote());
  const undo = cmd((c) => c.undo());
  const redo = cmd((c) => c.redo());

  function openLink() {
    if (!editor) return;
    linkUrl = editor.getAttributes("link")?.href ?? "";
    linkOpen = true;
  }
  function applyLink() {
    if (!editor) return;
    const url = linkUrl.trim();
    if (url) {
      editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
    } else {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
    }
    linkOpen = false;
    refreshActive();
  }
  function cancelLink() {
    linkOpen = false;
    linkBtn?.focus(); // #4: devolver el foco al botón disparador
  }
</script>

<div class="op-rich" class:op-rich--disabled={disabled}>
  {#if label}
    <span class="op-rich__label" id="{id}-label">{label}</span>
  {/if}

  {#if failed}
    <!-- Fallback sin TipTap: textarea de texto plano (sin formato). -->
    <textarea
      {id}
      class="op-rich__fallback"
      bind:value
      {placeholder}
      {disabled}
      rows="8"
      aria-label={label || "Editor de contenido"}
      aria-describedby={describedBy}
    ></textarea>
  {:else}
    <div class="op-rich__toolbar" role="toolbar" aria-label="Formato de texto" aria-controls={id}>
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.bold}
        aria-pressed={active.bold}
        aria-label="Negrita"
        title="Negrita (Ctrl+B)"
        on:click={toggleBold}
        disabled={disabled || !ready}><i class="fas fa-bold" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.italic}
        aria-pressed={active.italic}
        aria-label="Cursiva"
        title="Cursiva (Ctrl+I)"
        on:click={toggleItalic}
        disabled={disabled || !ready}><i class="fas fa-italic" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.underline}
        aria-pressed={active.underline}
        aria-label="Subrayado"
        title="Subrayado (Ctrl+U)"
        on:click={toggleUnderline}
        disabled={disabled || !ready}><i class="fas fa-underline" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.strike}
        aria-pressed={active.strike}
        aria-label="Tachado"
        title="Tachado"
        on:click={toggleStrike}
        disabled={disabled || !ready}
        ><i class="fas fa-strikethrough" aria-hidden="true"></i></button
      >
      <span class="op-rich__sep" aria-hidden="true"></span>
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.h2}
        aria-pressed={active.h2}
        aria-label="Título"
        title="Título"
        on:click={toggleH2}
        disabled={disabled || !ready}><i class="fas fa-heading" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn op-rich__btn--text"
        class:is-active={active.h3}
        aria-pressed={active.h3}
        aria-label="Subtítulo"
        title="Subtítulo"
        on:click={toggleH3}
        disabled={disabled || !ready}>H3</button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.bullet}
        aria-pressed={active.bullet}
        aria-label="Lista con viñetas"
        title="Lista con viñetas"
        on:click={toggleBullet}
        disabled={disabled || !ready}><i class="fas fa-list-ul" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.ordered}
        aria-pressed={active.ordered}
        aria-label="Lista numerada"
        title="Lista numerada"
        on:click={toggleOrdered}
        disabled={disabled || !ready}><i class="fas fa-list-ol" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.blockquote}
        aria-pressed={active.blockquote}
        aria-label="Cita"
        title="Cita"
        on:click={toggleQuote}
        disabled={disabled || !ready}><i class="fas fa-quote-right" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.code}
        aria-pressed={active.code}
        aria-label="Código"
        title="Código"
        on:click={toggleCode}
        disabled={disabled || !ready}><i class="fas fa-code" aria-hidden="true"></i></button
      >
      <span class="op-rich__sep" aria-hidden="true"></span>
      <button
        type="button"
        class="op-rich__btn"
        class:is-active={active.link}
        aria-pressed={active.link}
        aria-label="Enlace"
        title="Insertar enlace"
        aria-haspopup="dialog"
        aria-expanded={linkOpen}
        bind:this={linkBtn}
        on:click={openLink}
        disabled={disabled || !ready}><i class="fas fa-link" aria-hidden="true"></i></button
      >
      <span class="op-rich__sep" aria-hidden="true"></span>
      <button
        type="button"
        class="op-rich__btn"
        aria-label="Deshacer"
        title="Deshacer (Ctrl+Z)"
        on:click={undo}
        disabled={disabled || !ready}><i class="fas fa-undo" aria-hidden="true"></i></button
      >
      <button
        type="button"
        class="op-rich__btn"
        aria-label="Rehacer"
        title="Rehacer (Ctrl+Y)"
        on:click={redo}
        disabled={disabled || !ready}><i class="fas fa-redo" aria-hidden="true"></i></button
      >
    </div>

    {#if linkOpen}
      <div class="op-rich__link" role="dialog" aria-label="Insertar enlace">
        <label class="sr-only" for="{id}-link-url">Dirección del enlace</label>
        <input
          id="{id}-link-url"
          class="op-rich__link-input"
          type="url"
          bind:value={linkUrl}
          placeholder="https://…"
          use:autofocus
          on:keydown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              applyLink();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancelLink();
            }
          }}
        />
        <button type="button" class="op-rich__link-btn" on:click={applyLink}>Aplicar</button>
        <button
          type="button"
          class="op-rich__link-btn op-rich__link-btn--ghost"
          on:click={cancelLink}>Cancelar</button
        >
      </div>
    {/if}

    <!-- TipTap monta el área editable dentro de este host. -->
    <div
      bind:this={host}
      class="op-rich__host"
      class:op-rich__host--placeholder={!value}
      data-placeholder={placeholder}
      aria-describedby={describedBy}
    ></div>
    {#if !ready}
      <p class="op-rich__loading">Cargando editor…</p>
    {/if}
  {/if}

  {#if hint}
    <p id={hintId} class="op-rich__hint">{hint}</p>
  {/if}
</div>

<style lang="scss">
  .op-rich {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--op-space-1);
      padding: var(--op-space-1);
      border: 1px solid var(--op-border);
      border-bottom: none;
      border-radius: var(--op-radius) var(--op-radius) 0 0;
      background: var(--op-bg);
    }

    &__btn {
      min-width: 30px;
      height: 30px;
      padding: 0 var(--op-space-2);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      background: transparent;
      border: 1px solid transparent;
      border-radius: var(--op-radius);
      cursor: pointer;
      transition:
        background var(--op-transition),
        color var(--op-transition);

      &:hover:not(:disabled) {
        background: var(--op-surface);
        color: var(--op-primary);
      }
      &:focus-visible {
        outline: none;
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }
      &.is-active {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        border-color: var(--op-primary);
      }
      &:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      &--text {
        font-weight: var(--op-weight-bold);
        font-size: var(--op-font-xs);
      }
    }

    &__sep {
      width: 1px;
      height: 18px;
      margin: 0 var(--op-space-1);
      background: var(--op-border);
    }

    &__host {
      border: 1px solid var(--op-border);
      border-radius: 0 0 var(--op-radius) var(--op-radius);
      background: var(--op-surface);
      min-height: 160px;

      :global(.op-rich__content) {
        padding: var(--op-space-3);
        min-height: 160px;
        outline: none;
        font-size: var(--op-font-sm);
        line-height: var(--op-line-height);
        color: var(--op-text);

        &:focus {
          box-shadow: inset 0 0 0 2px var(--op-focus-ring);
          border-radius: 0 0 var(--op-radius) var(--op-radius);
        }
        :global(p) {
          margin: 0 0 var(--op-space-2);
        }
        :global(h2),
        :global(h3) {
          margin: var(--op-space-3) 0 var(--op-space-2);
          color: var(--op-text);
        }
        :global(ul),
        :global(ol) {
          margin: 0 0 var(--op-space-2) var(--op-space-5);
        }
        :global(blockquote) {
          margin: 0 0 var(--op-space-2);
          padding-left: var(--op-space-3);
          border-left: 3px solid var(--op-primary-light);
          color: var(--op-text-secondary);
        }
        :global(a) {
          color: var(--op-primary);
          text-decoration: underline;
        }
        :global(code) {
          font-family: monospace;
          background: var(--op-bg);
          padding: 0 var(--op-space-1);
          border-radius: var(--op-radius-sm, var(--op-radius));
        }
      }
    }

    // Placeholder cuando el editor está vacío (TipTap StarterKit no trae placeholder).
    &__host--placeholder :global(.op-rich__content p:first-child::before) {
      content: attr(data-placeholder);
      color: var(--op-text-secondary);
      pointer-events: none;
      height: 0;
      float: left;
    }

    &__fallback {
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      width: 100%;
      resize: vertical;
      outline: none;

      &:focus {
        border-color: var(--op-primary);
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }
    }

    &__link {
      display: flex;
      gap: var(--op-space-2);
      padding: var(--op-space-2);
      border: 1px solid var(--op-border);
      border-top: none;
      background: var(--op-bg);
      align-items: center;
      flex-wrap: wrap;
    }
    &__link-input {
      flex: 1 1 200px;
      height: 32px;
      padding: 0 var(--op-space-2);
      font-size: var(--op-font-sm);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-surface);
      color: var(--op-text);
      &:focus {
        outline: none;
        border-color: var(--op-primary);
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }
    }
    &__link-btn {
      height: 32px;
      padding: 0 var(--op-space-3);
      font-size: var(--op-font-sm);
      color: var(--op-on-primary);
      background: var(--op-primary);
      border: none;
      border-radius: var(--op-radius);
      cursor: pointer;
      &:hover {
        background: var(--op-primary-dark);
      }
      &--ghost {
        background: transparent;
        color: var(--op-text-secondary);
        border: 1px solid var(--op-border);
        &:hover {
          background: var(--op-surface);
          color: var(--op-text);
        }
      }
    }

    &__loading,
    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &--disabled {
      opacity: 0.7;
    }
  }
</style>
