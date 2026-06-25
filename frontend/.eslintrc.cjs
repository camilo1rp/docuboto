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
  ignorePatterns: ["build/", ".svelte-kit/", "node_modules/", "*.config.js", "*.config.cjs"],
};
