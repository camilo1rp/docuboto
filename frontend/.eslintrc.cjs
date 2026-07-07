/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  extends: ["eslint:recommended", "plugin:svelte/recommended"],
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: "module",
    extraFileExtensions: [".svelte"],
  },
  overrides: [
    {
      files: ["*.svelte"],
      parser: "svelte-eslint-parser",
    },
  ],
  rules: {
    // Reglas básicas de calidad
    "no-console": ["warn", { allow: ["error", "warn"] }],
    "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    "no-undef": "error",
  },
  ignorePatterns: [
    "build/",
    // Artefacto root-owned de un build previo en Docker (no borrable sin sudo
    // desde el host); no es código fuente. Ver nota en el resumen de PIEZA 1.
    "build-root-owned-orphan/",
    ".svelte-kit/",
    "node_modules/",
    "*.config.js",
    "*.config.cjs",
  ],
};
