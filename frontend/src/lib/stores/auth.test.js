// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth.test.js — Store de sesión (D-05, D-16).
 *
 * INVARIANTE CRÍTICO: los tokens (access/refresh) NUNCA están en este store
 * — viven en cookies httpOnly gestionadas por el servidor SSR. Estos tests
 * verifican los derivados/helpers de RBAC en UI y, explícitamente, que
 * `initSession`/`sessionUser` no exponen ningún campo de token.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import {
  sessionUser,
  roles,
  permissions,
  permissionLevels,
  tenantSlug,
  isAuthenticated,
  hasRole,
  can,
  initSession,
  setPermissions,
  clearSession,
} from "./auth.js";
import { extractSessionUser } from "../services/auth.server.js";

/** Sesión mínima con forma de SessionUser. */
function makeUser(overrides = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador Uno",
    preferred_username: "operador1",
    roles: [],
    tenant_slug: "icetex",
    permissions: [],
    permission_levels: {},
    ...overrides,
  };
}

describe("auth store — estado base", () => {
  beforeEach(() => {
    clearSession();
  });

  it("sin sesión: sessionUser es null y los derivados son seguros por defecto", () => {
    expect(get(sessionUser)).toBeNull();
    expect(get(roles)).toEqual([]);
    expect(get(permissions)).toEqual([]);
    expect(get(tenantSlug)).toBeNull();
    expect(get(isAuthenticated)).toBe(false);
  });

  it("hasRole/can son false sin sesión (nunca truthy por error de undefined)", () => {
    expect(hasRole("tenant_admin")).toBe(false);
    expect(can("PERM_RADI")).toBe(false);
  });
});

describe("auth store — initSession con distintas formas de sesión", () => {
  beforeEach(() => {
    clearSession();
  });

  it("sesión root: sin roles/permisos concedidos no habilita nada por accidente", () => {
    initSession(makeUser());
    expect(get(isAuthenticated)).toBe(true);
    expect(get(roles)).toEqual([]);
    expect(get(permissions)).toEqual([]);
    expect(hasRole("tenant_admin")).toBe(false);
    expect(can("PERM_RADI")).toBe(false);
  });

  it("sesión sin permisos (permissions undefined): los derivados no explotan", () => {
    const { permissions: _omit, ...userSinPermisos } = makeUser();
    initSession(userSinPermisos);
    expect(get(permissions)).toEqual([]);
    expect(can("PERM_RADI")).toBe(false);
  });

  it("sesión con permisos específicos: can() solo es true para los concedidos", () => {
    initSession(makeUser({ permissions: ["PERM_RADI", "PERM_RADI_ENTRADA"] }));
    expect(can("PERM_RADI")).toBe(true);
    expect(can("PERM_RADI_ENTRADA")).toBe(true);
    expect(can("PERM_RADI_SALIDA")).toBe(false);
    expect(can("USUA_PERM_ADMIN")).toBe(false);
  });

  it("sesión con roles: hasRole acepta cualquiera de varios roles solicitados", () => {
    initSession(makeUser({ roles: ["operador"] }));
    expect(hasRole("operador")).toBe(true);
    expect(hasRole("tenant_admin", "operador")).toBe(true);
    expect(hasRole("tenant_admin", "superadmin")).toBe(false);
  });

  it("tenantSlug refleja el claim tenant_slug de la sesión", () => {
    initSession(makeUser({ tenant_slug: "icetex" }));
    expect(get(tenantSlug)).toBe("icetex");
  });

  it("setPermissions actualiza los permisos sin tocar el resto de la sesión", () => {
    initSession(makeUser({ permissions: ["PERM_RADI"] }));
    setPermissions(["USUA_PERM_ADMIN"]);
    expect(get(permissions)).toEqual(["USUA_PERM_ADMIN"]);
    expect(get(sessionUser)?.tenant_slug).toBe("icetex");
  });

  it("setPermissions es no-op si no hay sesión activa", () => {
    setPermissions(["USUA_PERM_ADMIN"]);
    expect(get(sessionUser)).toBeNull();
  });

  it("clearSession limpia la sesión completa", () => {
    initSession(makeUser({ roles: ["tenant_admin"] }));
    clearSession();
    expect(get(sessionUser)).toBeNull();
    expect(get(isAuthenticated)).toBe(false);
    expect(hasRole("tenant_admin")).toBe(false);
  });
});

describe("auth store — can(perm, minCrud): espejo del RBAC por nivel (D-A6)", () => {
  beforeEach(() => {
    clearSession();
  });

  it("can(perm) sin minCrud es retrocompatible: solo mira presencia en permissions[]", () => {
    initSession(
      makeUser({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 1 },
      }),
    );
    expect(can("USUA_PERM_EXPEDIENTE")).toBe(true);
    expect(can("PERM_RADI")).toBe(false);
  });

  it("permission_levels expuesto en el store derivado permissionLevels", () => {
    initSession(makeUser({ permission_levels: { USUA_PERM_EXPEDIENTE: 3 } }));
    expect(get(permissionLevels)).toEqual({ USUA_PERM_EXPEDIENTE: 3 });
  });

  it("con minCrud=3: nivel 1 (solo lectura) NO alcanza — false", () => {
    initSession(
      makeUser({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 1 },
      }),
    );
    expect(can("USUA_PERM_EXPEDIENTE", 3)).toBe(false);
  });

  it("con minCrud=3: nivel 3 (crear/borrar) sí alcanza — true", () => {
    initSession(
      makeUser({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
      }),
    );
    expect(can("USUA_PERM_EXPEDIENTE", 3)).toBe(true);
  });

  it("fail-closed: permission_levels vacío ({}) con minCrud → false, nunca 'no sé el nivel => permito'", () => {
    initSession(makeUser({ permissions: ["USUA_PERM_EXPEDIENTE"], permission_levels: {} }));
    expect(can("USUA_PERM_EXPEDIENTE", 1)).toBe(false);
  });

  it("fail-closed: permission_levels sin la clave del permiso (sesión vieja / backend antiguo) → false", () => {
    initSession(
      makeUser({
        permissions: ["USUA_PERM_EXPEDIENTE"],
        permission_levels: { OTRO_PERM: 5 },
      }),
    );
    expect(can("USUA_PERM_EXPEDIENTE", 1)).toBe(false);
  });

  it("fail-closed: permission_levels ausente en la sesión (undefined) → false con minCrud", () => {
    const { permission_levels: _omit, ...userSinNiveles } = makeUser({
      permissions: ["USUA_PERM_EXPEDIENTE"],
    });
    initSession(userSinNiveles);
    expect(get(permissionLevels)).toEqual({});
    expect(can("USUA_PERM_EXPEDIENTE", 1)).toBe(false);
  });

  it("setPermissions actualiza permission_levels junto con permissions", () => {
    initSession(makeUser());
    setPermissions(["USUA_PERM_EXPEDIENTE"], { USUA_PERM_EXPEDIENTE: 3 });
    expect(can("USUA_PERM_EXPEDIENTE", 3)).toBe(true);
    expect(can("USUA_PERM_EXPEDIENTE", 4)).toBe(false);
  });

  it("setPermissions sin levels deja permission_levels en {} (fail-closed, no en undefined)", () => {
    initSession(makeUser({ permission_levels: { USUA_PERM_EXPEDIENTE: 3 } }));
    setPermissions(["USUA_PERM_EXPEDIENTE"]);
    expect(get(permissionLevels)).toEqual({});
    expect(can("USUA_PERM_EXPEDIENTE", 1)).toBe(false);
  });
});

describe("auth store — INVARIANTE: nunca expone tokens", () => {
  beforeEach(() => {
    clearSession();
  });

  const TOKEN_FIELDS = ["access_token", "accessToken", "refresh_token", "refreshToken", "token"];

  it("los derivados públicos (roles/permissions) nunca exponen campos de token", () => {
    // Aunque el store sea un espejo del objeto de entrada, los ÚNICOS puntos
    // de consumo real desde componentes son los stores derivados — estos
    // jamás deben traer forma de token.
    initSession(makeUser({ permissions: ["PERM_RADI"] }));
    expect(get(roles)).not.toHaveProperty("access_token");
    expect(get(permissions)).not.toHaveProperty("access_token");
  });

  it(
    "extractSessionUser (productor REAL, auth.server.js) descarta access_token/refresh_token " +
      "aunque vengan en los claims — y initSession(...) tampoco los deja colar al store",
    () => {
      // Claims JWT decodificados tal como los produciría Keycloak — a propósito
      // incluyen campos de token (y otros ruido) para probar que la allowlist de
      // extractSessionUser los descarta, en vez de construir un SessionUser "a mano"
      // que por definición nunca los tendría (eso sería tautológico: ver hallazgo
      // de revisión que motivó este test).
      const claims = {
        sub: "u-1",
        email: "op@orpyca.com",
        name: "Operador Uno",
        preferred_username: "operador1",
        realm_access: { roles: ["operador"] },
        tenant_slug: "icetex",
        // Campos que NUNCA deben sobrevivir a extractSessionUser (mass-assignment):
        access_token: "eyJ.secret.token",
        accessToken: "eyJ.secret.token",
        refresh_token: "refresh-secret",
        refreshToken: "refresh-secret",
        token: "generic-secret",
        // Otro claim JWT estándar que tampoco pertenece a SessionUser.
        iat: 1700000000,
        exp: 1700003600,
      };

      const sessionUserFromClaims = extractSessionUser(claims);

      // 1) El propio productor real descarta los campos de token — si una regresión
      //    introdujera `...claims` (mass-assignment) en extractSessionUser, esta
      //    aserción reventaría aquí, antes de llegar al store.
      for (const field of TOKEN_FIELDS) {
        expect(sessionUserFromClaims).not.toHaveProperty(field);
      }
      // 2) Solo la allowlist documentada de SessionUser sobrevive (ni iat/exp ni
      //    ningún otro claim JWT crudo).
      expect(Object.keys(sessionUserFromClaims).sort()).toEqual(
        [
          "sub",
          "email",
          "name",
          "preferred_username",
          "roles",
          "tenant_slug",
          "permissions",
          "permission_levels",
        ].sort(),
      );

      // 3) Y el store, al recibir ese SessionUser real, tampoco expone tokens
      //    (defensa en profundidad: initSession no filtra, así que esto solo es
      //    seguro porque (1)/(2) ya lo garantizan — si se rompieran, esto también
      //    fallaría).
      initSession(sessionUserFromClaims);
      const stored = get(sessionUser);
      for (const field of TOKEN_FIELDS) {
        expect(stored).not.toHaveProperty(field);
      }
      expect(get(roles)).toEqual(["operador"]);
      expect(get(tenantSlug)).toBe("icetex");
    },
  );
});
