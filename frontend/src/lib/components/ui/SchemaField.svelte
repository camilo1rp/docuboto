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
  SchemaField.svelte — Campo dinámico derivado de UNA propiedad de JSON
  Schema (Draft 2020-12), para el Form Builder de creación de expediente
  (T-12, plantilla de metadatos de la serie TRD — `json_schema.properties`)
  y para los campos extra del tipo documental al radicar.

  El mapeo tipo-de-schema -> control de UI vive en `$lib/utils/schemaField.js`
  (compartido con la coerción server-side en `expedientes/+page.server.js`),
  este componente solo lo consume y delega en `FormField`.

  Controles:
    - escalares (text/email/date/number/select): un `FormField`.
    - array de escalares: lista REPETIBLE (agregar / quitar elementos).
    - object de un nivel: grupo (`fieldset`) con un `SchemaField` por
      propiedad, resuelto recursivamente.
  Los compuestos exponen su valor como UN string JSON de valores crudos (ver
  la cabecera de `schemaField.js`) y, para el submit nativo de un form action,
  lo transportan en un `<input type="hidden" name="meta_<clave>">`: los
  controles interiores NO llevan `name`, así que no se envían sueltos.

  Si el constructo no está soportado (array de objetos, object anidado,
  oneOf/$ref, tipo desconocido), NO renderiza ningún control (fail-safe) — el
  llamador debe contar estos casos y avisar al usuario (ver
  /expedientes/+page.svelte).

  Props:
    propKey   string  — clave de la propiedad en json_schema.properties (id/name/label fallback)
    schema    object  — la propiedad: json_schema.properties[propKey]
    required  boolean — si propKey está en json_schema.required
    error     string  — mensaje de error (422 por campo)
    subErrors object  — object: mensajes por propiedad interior ({ciudad: "..."})
    value     any     — valor del campo (bindable). Escalar: string. Compuesto: string JSON.
    nested    boolean — uso interno: dentro de un object (sin `name`, sin object anidado)

  El `name` HTML del control escalar es siempre `meta_<propKey>` — así el submit
  nativo del form action lo transporta en el FormData sin fetch adicional.
-->
<script>
  import FormField from "./FormField.svelte";
  import Button from "./Button.svelte";
  import { resolveSchemaControl } from "$lib/utils/schemaField.js";

  export let propKey = "";
  /** @type {any} */
  export let schema = {};
  export let required = false;
  export let error = "";
  /** @type {Record<string, string>} */
  export let subErrors = {};
  export let value = "";
  export let nested = false;

  $: control = resolveSchemaControl(schema, nested ? 1 : 0);
  $: fieldLabel = schema?.title ?? propKey;
  $: fieldHint = schema?.description ?? "";
  // Placeholder "— seleccionar —" solo en selects OPCIONALES: permite dejar
  // el campo vacío sin forzar una opción del enum/booleano.
  $: fieldPlaceholder = control?.kind === "select" && !required ? "— seleccionar —" : "";

  // --- Compuestos ---------------------------------------------------------
  // `value` (string JSON) es la fuente de verdad hacia el padre; `items`/`sub`
  // son el estado de edición. `emitido` recuerda lo último que ESTE componente
  // escribió: si `value` cambia por otra vía (el padre limpia o repuebla el
  // formulario), se re-lee; si es eco de lo propio, se ignora. Sin esa guarda
  // la prop tendría dos dueños y reabrir el formulario mostraría lo anterior.
  /** @type {string[]} */
  let items = [""];
  /** @type {Record<string, string>} */
  let sub = {};
  let emitido = value;

  /** @param {unknown} raw */
  function leer(raw) {
    if (typeof raw !== "string" || raw === "") return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function cargar() {
    const parsed = leer(value);
    if (control?.kind === "array") {
      const lista = Array.isArray(parsed) ? parsed.map((v) => String(v ?? "")) : [];
      items = lista.length ? lista : [""];
    } else if (control?.kind === "object") {
      /** @type {Record<string, string>} */
      const grupo = {};
      for (const [k, v] of Object.entries(parsed && typeof parsed === "object" ? parsed : {})) {
        grupo[k] = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "");
      }
      sub = grupo;
    }
  }

  // Carga inicial: `value` puede llegar ya poblado (repoblar tras un 422).
  let iniciado = false;
  $: if (control && !iniciado) {
    iniciado = true;
    if (control.kind === "array" || control.kind === "object") cargar();
  }

  $: if (control && (control.kind === "array" || control.kind === "object") && value !== emitido) {
    emitido = value;
    cargar();
  }

  function emitir(/** @type {string} */ json) {
    if (json === emitido) return;
    emitido = json;
    value = json;
  }

  $: if (control?.kind === "array") {
    const llenos = items.filter((v) => v !== "" && v !== null && v !== undefined);
    emitir(llenos.length ? JSON.stringify(llenos) : "");
  }

  $: if (control?.kind === "object") {
    /** @type {Record<string, unknown>} */
    const salida = {};
    for (const [k, propSchema] of control.properties) {
      const v = sub[k];
      if (v === undefined || v === "") continue;
      // Un array anidado guarda su propio JSON: se inserta como lista.
      salida[k] =
        resolveSchemaControl(propSchema, 1)?.kind === "array" ? (leer(v) ?? undefined) : v;
      if (salida[k] === undefined) delete salida[k];
    }
    emitir(Object.keys(salida).length ? JSON.stringify(salida) : "");
  }

  function agregar() {
    items = [...items, ""];
  }

  function quitar(/** @type {number} */ i) {
    const resto = items.filter((_, j) => j !== i);
    items = resto.length ? resto : [""];
  }

  $: groupId = `meta-${propKey}`;
</script>

{#if control && control.kind === "array"}
  <fieldset class="schema-field__group" aria-describedby={error ? `${groupId}-error` : undefined}>
    <legend class="op-field__label">
      {fieldLabel}
      {#if required}
        <span class="op-field__required" aria-hidden="true">*</span>
        <span class="sr-only">(obligatorio)</span>
      {/if}
    </legend>
    {#if fieldHint}<p class="schema-field__hint">{fieldHint}</p>{/if}
    {#each items as _, i (i)}
      <div class="schema-field__row">
        <FormField
          id={`${groupId}-${i}`}
          label={`${fieldLabel} ${i + 1}`}
          type={control.item.kind}
          options={control.item.options ?? []}
          placeholder={control.item.kind === "select" ? "— seleccionar —" : ""}
          required={required && i === 0}
          bind:value={items[i]}
        />
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-trash"
          on:click={() => quitar(i)}
          aria-label={`Quitar ${fieldLabel} ${i + 1}`}
          disabled={items.length === 1 && items[0] === ""}
        >
          Quitar
        </Button>
      </div>
    {/each}
    <Button variant="secondary" size="sm" icon="fas fa-plus" on:click={agregar}>
      Agregar {fieldLabel}
    </Button>
    {#if error}<p id={`${groupId}-error`} class="schema-field__error" role="alert">{error}</p>{/if}
    {#if !nested}<input type="hidden" name={`meta_${propKey}`} {value} />{/if}
  </fieldset>
{:else if control && control.kind === "object"}
  <fieldset class="schema-field__group" aria-describedby={error ? `${groupId}-error` : undefined}>
    <legend class="op-field__label">
      {fieldLabel}
      {#if required}
        <span class="op-field__required" aria-hidden="true">*</span>
        <span class="sr-only">(obligatorio)</span>
      {/if}
    </legend>
    {#if fieldHint}<p class="schema-field__hint">{fieldHint}</p>{/if}
    {#each control.properties as [k, propSchema] (k)}
      <svelte:self
        nested
        propKey={`${propKey}-${k}`}
        schema={{ title: k, ...propSchema }}
        required={control.required.includes(k)}
        error={subErrors[k] ?? ""}
        bind:value={sub[k]}
      />
    {/each}
    {#if error}<p id={`${groupId}-error`} class="schema-field__error" role="alert">{error}</p>{/if}
    {#if !nested}<input type="hidden" name={`meta_${propKey}`} {value} />{/if}
  </fieldset>
{:else if control && control.kind !== "array" && control.kind !== "object"}
  <FormField
    id={`meta-${propKey}`}
    name={nested ? null : `meta_${propKey}`}
    label={fieldLabel}
    type={control.kind}
    options={control.options ?? []}
    placeholder={fieldPlaceholder}
    {required}
    hint={fieldHint}
    {error}
    bind:value
  />
{/if}

<style>
  .schema-field__group {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    margin: 0 0 var(--op-space-3);
    min-inline-size: 0;
  }
  .schema-field__row {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-2);
    flex-wrap: wrap;
  }
  .schema-field__row > :global(.op-field) {
    flex: 1 1 12rem;
    min-width: 0;
  }
  .schema-field__hint {
    color: var(--op-text-secondary);
    margin: 0 0 var(--op-space-2);
  }
  .schema-field__error {
    color: var(--op-error-fg);
    margin: var(--op-space-2) 0 0;
  }
</style>
