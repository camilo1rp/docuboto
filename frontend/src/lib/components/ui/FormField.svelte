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
  FormField.svelte — Campo de formulario (input / select / textarea) con label.

  Props:
    id         string   — id del control (obligatorio para asociar label)
    label      string   — texto del label visible
    type       string   — 'text'|'email'|'password'|'number'|'date'|'select'|'textarea'
    value      any      — valor del campo (bindable)
    placeholder string
    required   boolean
    disabled   boolean
    error      string   — mensaje de error (si hay, marca el campo inválido)
    hint       string   — texto de ayuda adicional debajo del campo
    rows       number   — solo para textarea (default 3)
    options    Array<{value:string; label:string}> — solo para select

  Accesibilidad:
    - label explícito con for={id}
    - aria-required, aria-invalid, aria-describedby apuntando a error/hint
-->
<script>
  export let id = "";
  export let label = "";

  /** @type {'text'|'email'|'password'|'number'|'date'|'select'|'textarea'} */
  export let type = "text";

  export let value = "";
  export let placeholder = "";
  export let required = false;
  export let disabled = false;
  export let error = "";
  export let hint = "";
  export let rows = 3;

  /** @type {Array<{value: string; label: string}>} */
  export let options = [];

  $: hintId = hint ? `${id}-hint` : undefined;
  // El contenedor de error existe siempre en el DOM; siempre incluimos su ID
  // en aria-describedby para que el campo quede asociado desde el inicio.
  const errorId = `${id}-error`;
  $: describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
</script>

<div class="op-field" class:op-field--error={!!error}>
  {#if label}
    <label class="op-field__label" for={id}>
      {label}
      {#if required}
        <span class="op-field__required" aria-hidden="true">*</span>
        <span class="sr-only">(obligatorio)</span>
      {/if}
    </label>
  {/if}

  {#if type === "select"}
    <div class="op-field__select-wrapper">
      <select
        {id}
        class="op-field__control op-field__select"
        bind:value
        {disabled}
        {required}
        aria-required={required}
        aria-invalid={!!error}
        aria-describedby={describedBy}
        on:change
        on:blur
      >
        {#if placeholder}
          <option value="" disabled selected>{placeholder}</option>
        {/if}
        {#each options as opt (opt.value)}
          <option value={opt.value}>{opt.label}</option>
        {/each}
      </select>
      <i class="fas fa-chevron-down op-field__select-icon" aria-hidden="true"></i>
    </div>
  {:else if type === "textarea"}
    <textarea
      {id}
      class="op-field__control op-field__textarea"
      bind:value
      {placeholder}
      {disabled}
      {required}
      {rows}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:blur
    ></textarea>
  {:else if type === "number"}
    <input
      {id}
      type="number"
      class="op-field__control op-field__input"
      bind:value
      {placeholder}
      {disabled}
      {required}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:change
      on:blur
    />
  {:else if type === "email"}
    <input
      {id}
      type="email"
      class="op-field__control op-field__input"
      bind:value
      {placeholder}
      {disabled}
      {required}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:change
      on:blur
    />
  {:else if type === "password"}
    <input
      {id}
      type="password"
      class="op-field__control op-field__input"
      bind:value
      {placeholder}
      {disabled}
      {required}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:change
      on:blur
    />
  {:else if type === "date"}
    <input
      {id}
      type="date"
      class="op-field__control op-field__input"
      bind:value
      {placeholder}
      {disabled}
      {required}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:change
      on:blur
    />
  {:else}
    <!-- type="text" (default) -->
    <input
      {id}
      type="text"
      class="op-field__control op-field__input"
      bind:value
      {placeholder}
      {disabled}
      {required}
      aria-required={required}
      aria-invalid={!!error}
      aria-describedby={describedBy}
      on:input
      on:change
      on:blur
    />
  {/if}

  {#if hint && !error}
    <p id={hintId} class="op-field__hint">{hint}</p>
  {/if}

  <!--
    El contenedor de error permanece en el DOM como live region estable.
    Alternando su contenido (en vez de montarlo/desmontarlo) los lectores
    de pantalla anuncian el error correctamente cuando aparece.
    aria-live="polite" es suficiente; el campo ya marca aria-invalid="true".
  -->
  <p id="{id}-error" class="op-field__error" aria-live="polite" aria-atomic="true">{error || ""}</p>
</div>

<style lang="scss">
  .op-field {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);

    &__label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__required {
      color: var(--op-error);
      margin-left: 2px;
    }

    &__control {
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      transition:
        border-color var(--op-transition),
        box-shadow var(--op-transition);
      outline: none;
      width: 100%;

      &:focus {
        border-color: var(--op-primary);
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }

      &:disabled {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        cursor: not-allowed;
      }
    }

    &__input {
      height: 36px;
      padding: 0 var(--op-space-3);
    }

    &__select-wrapper {
      position: relative;
    }

    &__select {
      height: 36px;
      padding: 0 var(--op-space-6) 0 var(--op-space-3);
      appearance: none;
      cursor: pointer;
    }

    &__select-icon {
      position: absolute;
      right: var(--op-space-3);
      top: 50%;
      transform: translateY(-50%);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      pointer-events: none;
    }

    &__textarea {
      padding: var(--op-space-2) var(--op-space-3);
      resize: vertical;
      min-height: 72px;
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      font-size: var(--op-font-xs);
      color: var(--op-error);
      margin: 0;
    }

    // Estado de error
    &--error {
      .op-field__control {
        border-color: var(--op-error);

        &:focus {
          box-shadow: 0 0 0 3px var(--op-error-ring);
        }
      }
    }
  }
</style>
