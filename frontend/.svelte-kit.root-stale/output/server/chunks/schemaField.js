const NULLABLE = "null";
function normalizeType(schema) {
  let type = schema?.type;
  if (Array.isArray(type)) {
    type = type.find((t) => t !== NULLABLE) ?? null;
  }
  return type ?? null;
}
function resolveSchemaControl(schema) {
  if (!schema || typeof schema !== "object") return null;
  if (schema.$ref || schema.oneOf || schema.anyOf || schema.allOf) return null;
  const type = normalizeType(schema);
  if (type === "object" || type === "array") return null;
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return {
      kind: "select",
      options: schema.enum.map((v) => ({ value: String(v), label: String(v) }))
    };
  }
  if (type === "string") {
    if (schema.format === "date") return { kind: "date" };
    if (schema.format === "email") return { kind: "email" };
    return { kind: "text" };
  }
  if (type === "number" || type === "integer") return { kind: "number" };
  if (type === "boolean") {
    return {
      kind: "select",
      options: [
        { value: "true", label: "Sí" },
        { value: "false", label: "No" }
      ]
    };
  }
  return null;
}
function coerceSchemaValue(schema, raw) {
  if (raw === "" || raw === void 0 || raw === null) return void 0;
  const control = resolveSchemaControl(schema);
  if (!control) return void 0;
  const type = normalizeType(schema);
  if (type === "number" || type === "integer") {
    const n = Number(raw);
    return Number.isNaN(n) ? void 0 : n;
  }
  if (type === "boolean") {
    if (raw === "true") return true;
    if (raw === "false") return false;
    return void 0;
  }
  return raw;
}
export {
  coerceSchemaValue as c,
  resolveSchemaControl as r
};
