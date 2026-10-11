// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Software libre bajo AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

/**
 * Guardián de aislamiento entre tenants (SSR): el catálogo de nombres vive en
 * un contexto POR PÁGINA, nunca en un store de módulo compartido por el proceso.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { readable } from "svelte/store";
import { render, screen } from "@testing-library/svelte";
import { NOMBRES_DEPENDENCIA_KEY } from "./dependencias.js";
import TrasladarModal from "$lib/components/workflow/TrasladarModal.svelte";

const here = dirname(fileURLToPath(import.meta.url));
const STEP = { id: "s1", to_dept: "Jurídica", to_dept_code: 7, status: "pending" };

describe("stores: ningún writable de módulo con datos del tenant", () => {
  it("dependencias.js no exporta stores de módulo", () => {
    const src = readFileSync(join(here, "dependencias.js"), "utf8");
    expect(src).not.toMatch(/export\s+const\s+\w+\s*=\s*writable\(/);
    expect(src).not.toMatch(/export\s+function\s+publicarCatalogoDependencias/);
  });

  it("los stores del directorio no se alimentan del catálogo de dependencias", () => {
    for (const f of readdirSync(here).filter((n) => n.endsWith(".js") && !n.includes(".test."))) {
      if (f === "dependencias.js") continue;
      expect(readFileSync(join(here, f), "utf8")).not.toMatch(/mapaNombres|nombresDependencia/);
    }
  });
});

describe("contexto de nombres por página", () => {
  it("sin contexto el componente cae a la instantánea (nada heredado de otra página)", () => {
    const { unmount } = render(TrasladarModal, {
      props: { open: true, step: STEP, dependencias: [] },
      context: new Map([[NOMBRES_DEPENDENCIA_KEY, readable(new Map([[7, "Tenant A"]]))]]),
    });
    expect(screen.getByLabelText(/dependencia de origen/i)).toHaveValue("Tenant A");
    unmount();
    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: [] } });
    expect(screen.getByLabelText(/dependencia de origen/i)).toHaveValue("Jurídica");
  });
});
