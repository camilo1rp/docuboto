// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * «¿Esto lo hice yo?» se pregunta con la identidad INTERNA, nunca con el `sub`.
 *
 * **Este defecto ya rompió tres pantallas**, y el propio `auth.server.js` lo deja
 * escrito en la definición de `SessionUser`. Estuvo a punto de romper una cuarta
 * —la bandeja de correo— donde el efecto era peor que cosmético: como el botón
 * se deshabilita sobre lo reservado por otro, un funcionario **no habría podido
 * continuar con el correo que él mismo abrió**.
 *
 * Son dos identificadores distintos y ambos existen en la sesión:
 *   · `sub`      — identificador de Keycloak.
 *   · `user_id`  — `auth_users.id`, lo que el backend escribe en las columnas de
 *                  autoría (`reservado_por`, `creada_por`, `firmante_id`…).
 *
 * El censo busca comparaciones entre un campo de autoría del backend y `sub`.
 * Por FORMA y no por lista de pantallas: una lista solo recuerda las que alguien
 * ya rompió.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

/** Campos que el backend rellena con la identidad INTERNA de quien actuó. */
const CAMPOS_DE_AUTORIA = [
  "reservado_por",
  "radicado_por",
  "descartado_por",
  "created_by",
  "creada_por",
  "solicitado_por",
  "firmante_id",
  "asignado_a",
  "registered_by",
];

function fuentes() {
  return execSync("find src -name '*.svelte' -o -name '*.js'", { encoding: "utf-8" })
    .trim()
    .split("\n")
    .filter((f) => !f.endsWith(".test.js"));
}

describe("identidad interna frente al sub de Keycloak", () => {
  it("el censo ve el proyecto (anti-vacuidad)", () => {
    expect(fuentes().length).toBeGreaterThan(150);
  });

  it("ningún campo de autoría se compara contra `sub`", () => {
    const culpables = [];
    const campos = CAMPOS_DE_AUTORIA.join("|");
    // `x.reservado_por === algo.sub` y su forma simétrica.
    const patrones = [
      new RegExp(`(${campos})\\s*[!=]==?\\s*[\\w.?]*\\bsub\\b`),
      new RegExp(`\\bsub\\b\\s*[!=]==?\\s*[\\w.?]*(${campos})`),
    ];
    for (const f of fuentes()) {
      const lineas = readFileSync(f, "utf-8").split("\n");
      lineas.forEach((linea, i) => {
        if (patrones.some((p) => p.test(linea))) culpables.push(`${f}:${i + 1}`);
      });
    }
    expect(
      culpables,
      "Comparan la autoría que escribe el backend (auth_users.id) con el " +
        "identificador de Keycloak: nunca coincidirán, así que el usuario no " +
        "reconocerá lo suyo. Use `user.user_id`.",
    ).toEqual([]);
  });

  it("la bandeja de correo resuelve `yo` con la identidad interna", () => {
    // Control positivo del censo: la pantalla donde este defecto habría sido
    // funcional y no cosmético.
    const fuente = readFileSync("src/routes/(app)/correo/[cuentaId]/+page.server.js", "utf-8");
    const asignacion = /yo:\s*([^,]+),/.exec(fuente);
    expect(asignacion, "no encontré la resolución de `yo`").not.toBeNull();
    expect(asignacion[1]).toContain("user_id");
    expect(asignacion[1]).not.toMatch(/\buser\?\.sub\b/);
  });
});
