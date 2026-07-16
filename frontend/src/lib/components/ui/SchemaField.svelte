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
  SchemaField.svelte — Campo dinámico derivado de UNA propiedad de JSON
  Schema (Draft 2020-12), para el Form Builder de creación de expediente
  (T-12, plantilla de metadatos de la serie TRD — `json_schema.properties`).

  El mapeo tipo-de-schema -> control de UI vive en `$lib/utils/schemaField.js`
  (compartido con la coerción server-side en `expedientes/+page.server.js`),
  este componente solo lo consume y delega en `FormField`.

  Si el constructo no está soportado (object/array/oneOf/$ref/tipo
  desconocido), NO renderiza ningún control (fail-safe) — el llamador debe
  contar estos casos y avisar al usuario (ver /expedientes/+page.svelte).

  Props:
    propKey  string  — clave de la propiedad en json_schema.properties (id/name/label fallback)
    schema   object  — la propiedad: json_schema.properties[propKey]
    required boolean — si propKey está en json_schema.required
    error    string  — mensaje de error (422 por campo, best-effort)
    value    any     — valor del campo (bindable)

  El `name` HTML del control es siempre `meta_<propKey>` — así el submit
  nativo del form action lo transporta en el FormData sin fetch adicional.
-->
<script>
  import FormField from "./FormField.svelte";
  import { resolveSchemaControl } from "$lib/utils/schemaField.js";

  export let propKey = "";
  /** @type {any} */
  export let schema = {};
  export let required = false;
  export let error = "";
  export let value = "";

  $: control = resolveSchemaControl(schema);
  $: fieldLabel = schema?.title ?? propKey;
  $: fieldHint = schema?.description ?? "";
  // Placeholder "— seleccionar —" solo en selects OPCIONALES: permite dejar
  // el campo vacío sin forzar una opción del enum/booleano.
  $: fieldPlaceholder = control?.kind === "select" && !required ? "— seleccionar —" : "";
</script>

{#if control}
  <FormField
    id={`meta-${propKey}`}
    name={`meta_${propKey}`}
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
