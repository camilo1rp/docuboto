import { c as create_ssr_component, v as validate_component } from "./ssr.js";
import { F as FormField } from "./FormField.js";
import { r as resolveSchemaControl } from "./schemaField.js";
const SchemaField = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let control;
  let fieldLabel;
  let fieldHint;
  let fieldPlaceholder;
  let { propKey = "" } = $$props;
  let { schema = {} } = $$props;
  let { required = false } = $$props;
  let { error = "" } = $$props;
  let { value = "" } = $$props;
  if ($$props.propKey === void 0 && $$bindings.propKey && propKey !== void 0) $$bindings.propKey(propKey);
  if ($$props.schema === void 0 && $$bindings.schema && schema !== void 0) $$bindings.schema(schema);
  if ($$props.required === void 0 && $$bindings.required && required !== void 0) $$bindings.required(required);
  if ($$props.error === void 0 && $$bindings.error && error !== void 0) $$bindings.error(error);
  if ($$props.value === void 0 && $$bindings.value && value !== void 0) $$bindings.value(value);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    control = resolveSchemaControl(schema);
    fieldLabel = schema?.title ?? propKey;
    fieldHint = schema?.description ?? "";
    fieldPlaceholder = control?.kind === "select" && !required ? "— seleccionar —" : "";
    $$rendered = `   ${control ? `${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        id: `meta-${propKey}`,
        name: `meta_${propKey}`,
        label: fieldLabel,
        type: control.kind,
        options: control.options ?? [],
        placeholder: fieldPlaceholder,
        required,
        hint: fieldHint,
        error,
        value
      },
      {
        value: ($$value) => {
          value = $$value;
          $$settled = false;
        }
      },
      {}
    )}` : ``}`;
  } while (!$$settled);
  return $$rendered;
});
export {
  SchemaField as S
};
