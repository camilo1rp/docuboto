// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/grupos/+page.server.js — Carga SSR del listado de grupos (E08, Fase 5
 * cierre de desfase API↔UI).
 *
 * Trae tres catálogos de solo lectura:
 *   - grupos (GET /auth/groups)
 *   - permisos (GET /auth/permissions) — catálogo completo, para el panel de
 *     permisos por grupo del drawer de detalle
 *   - niveles de seguridad (GET /auth/security-levels) — catálogo, para el
 *     panel de clearance del drawer de detalle
 *
 * El estado del grupo —miembros, permisos concedidos y clearance vigente— lo
 * carga el DRAWER al abrirse (`/admin/grupos/api/[id]/{members,permissions,
 * clearance}`), no este `load`: son tres consultas por grupo y la lista puede
 * traer decenas. Durante un tiempo esta pantalla administró a ciegas y este
 * mismo comentario afirmaba que el backend no exponía esas lecturas; sí las
 * exponía (espejos de lectura en `rbac.py` y `clearance.py`), y la frase falsa
 * es lo que mantuvo el hueco abierto. Verificar antes de declarar un límite.
 *
 * El access_token NO se expone — las escrituras van a /admin/grupos/api/*
 * (D-05), que leen locals.accessToken.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let groups = [];
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/auth/groups`, { headers });
    if (res.ok) {
      groups = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de grupos.";
  }

  // Catálogos AUXILIARES para el drawer de detalle: si fallan, no bloquean
  // la lista de grupos (solo el panel correspondiente del drawer queda vacío,
  // con su propio aviso).
  let permissions = [];
  try {
    const res = await fetch(`${API_V1}/auth/permissions`, { headers });
    if (res.ok) permissions = await res.json().catch(() => []);
  } catch {
    permissions = [];
  }

  let securityLevels = [];
  try {
    const res = await fetch(`${API_V1}/auth/security-levels`, { headers });
    if (res.ok) securityLevels = await res.json().catch(() => []);
  } catch {
    securityLevels = [];
  }

  // C-2 (ADR-039): si NINGÚN grupo concede PERM_RECLASIFICAR con crud 4, nadie puede
  // aprobar una desclasificación y las solicitudes se quedan sin salida (fail-closed
  // mudo). Se comprueba leyendo los permisos de cada grupo. Si alguna lectura falla
  // NO se afirma nada (`null`): un aviso falso sería peor que ninguno.
  /** @type {boolean|null} */
  let sinAprobadorDesclasificacion = null;
  if (Array.isArray(groups) && groups.length > 0) {
    const lecturas = await Promise.allSettled(
      groups.map(async (/** @type {any} */ g) => {
        const r = await fetch(`${API_V1}/auth/groups/${encodeURIComponent(g.id)}/permissions`, {
          headers,
        });
        if (!r.ok) throw new Error("lectura");
        return r.json();
      }),
    );
    if (lecturas.every((l) => l.status === "fulfilled")) {
      const hay = lecturas.some(
        (l) =>
          l.status === "fulfilled" &&
          Array.isArray(l.value) &&
          l.value.some(
            (/** @type {any} */ p) =>
              (p.nombre === "PERM_RECLASIFICAR" || p.permission === "PERM_RECLASIFICAR") &&
              Number(p.crud ?? 0) >= 4,
          ),
      );
      sinAprobadorDesclasificacion = !hay;
    }
  }

  return {
    sinAprobadorDesclasificacion,
    groups,
    permissions,
    securityLevels,
    loadError,
  };
}
