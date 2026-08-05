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

    // Divergencia REAL entre ESLint y el compilador, no una preferencia: los
    // `<!-- svelte-ignore a11y-no-noninteractive-tabindex -->` de los
    // envoltorios de tabla desplazables (técnica WCAG SCR29 — un `tabindex="0"`
    // sobre una región con scroll horizontal, para que quien navega solo con
    // teclado pueda desplazarla) SÍ los necesita `svelte-check`: al quitarlos
    // aparecen 13 avisos. `eslint-plugin-svelte` no evalúa esa regla de a11y y
    // por tanto los declara inútiles. Apagada aquí en vez de borrar los
    // comentarios, porque borrarlos rompe la verificación que sí es autoritativa.
    "svelte/no-unused-svelte-ignore": "off",
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
