// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin-layout-server.test.js — Espejo UX del RBAC de /admin (AREA-12-07).
 *
 * - Sin el permiso del área → redirect 303 a /403; con él → pasa.
 * - El mapa `ADMIN_AREA_PERMISSIONS` coincide con las tarjetas de
 *   `admin/+page.svelte` (misma fuente de verdad de permisos) y cubre todas las
 *   carpetas de `admin/`: una carpeta nueva sin declarar queda cerrada.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { load } from "./+layout.server.js";
import {
  ADMIN_AREA_PERMISSIONS,
  ADMIN_DEFAULT_PERMISSION,
  permisosRequeridosAdmin,
} from "$lib/utils/adminGate.js";

const AQUI = dirname(fileURLToPath(import.meta.url));

const ev = (pathname, user) => ({
  url: new URL(`http://localhost${pathname}`),
  parent: async () => ({ user }),
});

async function destino(pathname, user) {
  try {
    await load(ev(pathname, user));
    return null;
  } catch (e) {
    return e.status ? `${e.status} ${e.location}` : String(e);
  }
}

describe("admin/+layout.server.js", () => {
  it("sin USUA_PERM_ADMIN, /admin/usuarios termina en /403", async () => {
    expect(await destino("/admin/usuarios", { permissions: ["PERM_RADI"] })).toBe("303 /403");
  });

  it("con USUA_PERM_ADMIN, /admin/usuarios pasa", async () => {
    expect(await destino("/admin/usuarios", { permissions: ["USUA_PERM_ADMIN"] })).toBeNull();
  });

  it("subrutas profundas heredan el permiso del área", async () => {
    expect(await destino("/admin/flujos/reglas", { permissions: ["USUA_PERM_TRD"] })).toBe(
      "303 /403",
    );
    expect(await destino("/admin/flujos/reglas", { permissions: ["USUA_PERM_ADMIN"] })).toBeNull();
  });

  it("cada área exige SU permiso, no uno único", async () => {
    expect(await destino("/admin/trd", { permissions: ["USUA_PERM_ADMIN"] })).toBe("303 /403");
    expect(await destino("/admin/trd", { permissions: ["USUA_PERM_TRD"] })).toBeNull();
    expect(await destino("/admin/colas", { permissions: ["PERM_DLQ_ADMIN"] })).toBeNull();
    expect(await destino("/admin/interoperabilidad", { permissions: ["PERM_RADI"] })).toBeNull();
  });

  it("el índice /admin no redirige (filtra sus propias tarjetas)", async () => {
    expect(await destino("/admin", { permissions: [] })).toBeNull();
  });

  it("área no declarada: fail-closed con USUA_PERM_ADMIN", async () => {
    expect(permisosRequeridosAdmin("/admin/inventada")).toEqual([ADMIN_DEFAULT_PERMISSION]);
    expect(await destino("/admin/inventada", { permissions: ["PERM_RADI"] })).toBe("303 /403");
  });

  it("permisos degradados (/auth/me caído): no expulsa", async () => {
    expect(
      await destino("/admin/usuarios", { permissions: [], permisosDegradados: true }),
    ).toBeNull();
  });

  it("sin user en el padre: fail-closed", async () => {
    expect(await destino("/admin/usuarios", undefined)).toBe("303 /403");
  });
});

describe("mapa de áreas ↔ tarjetas de /admin", () => {
  const pagina = readFileSync(join(AQUI, "+page.svelte"), "utf8");
  // Cada tarjeta empieza en `label:`; su href y su permiso están dentro del
  // bloque (en cualquier orden). Los comentarios se quitan antes: citan
  // permisos de otras áreas.
  const sinComentarios = pagina.replace(/\/\/.*$/gm, "");
  const tarjetas = sinComentarios
    .split(/\n\s*label: /)
    .slice(1)
    .map((bloque) => {
      const area = bloque.match(/href: "\/admin\/([a-z]+)/)?.[1];
      const crudo = bloque.match(/permissions?: (\[[^\]]*\]|"[A-Z_]+")/)?.[1];
      return [null, area, crudo];
    })
    .filter(([, area]) => area);

  it("hay tarjetas (anti-vacuidad)", () => {
    expect(tarjetas.length).toBeGreaterThanOrEqual(18);
  });

  it("el permiso de cada tarjeta coincide con el del mapa", () => {
    for (const [, area, crudo] of tarjetas) {
      const declarados = crudo.startsWith("[")
        ? [...crudo.matchAll(/"([A-Z_]+)"/g)].map((m) => m[1])
        : [crudo.replaceAll('"', "")];
      expect(ADMIN_AREA_PERMISSIONS[area], `área ${area}`).toEqual(declarados);
    }
  });

  it("toda carpeta de admin/ está declarada en el mapa", () => {
    const carpetas = readdirSync(AQUI, { withFileTypes: true })
      .filter((d) => d.isDirectory() && !d.name.startsWith("."))
      .map((d) => d.name);
    for (const c of carpetas) {
      expect(ADMIN_AREA_PERMISSIONS[c], `carpeta ${c} sin permiso declarado`).toBeDefined();
    }
  });
});
