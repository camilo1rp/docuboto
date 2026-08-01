// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/usuarios/+page.server.js — Carga SSR del listado de usuarios (E08,
 * Fase 5 cierre de desfase API↔UI).
 *
 * Trae tres catálogos:
 *   - usuarios (GET /auth/users, paginado, USUA_PERM_ADMIN en el backend)
 *   - grupos (GET /auth/groups) — el picker "rol" de la URD es en realidad
 *     un auth_groups (rol_id de urd_usuaroldep referencia auth_groups.id,
 *     ver services/auth-service/migrations/tenant/002_urd.sql)
 *   - dependencias activas (GET /dependencias, tenant-service) — el picker
 *     "dependencia" de la URD usa `depe_id` = CÓDIGO entero de la dependencia
 *     (columna `codigo`), NO su UUID. No hay integridad referencial entre
 *     servicios: urd_usuaroldep.depe_id es un INTEGER suelto (comentario en
 *     la migración: "código de dependencia (tenant-service)").
 *
 * El access_token NO se expone — las escrituras van a /admin/usuarios/api/*
 * (D-05), que leen locals.accessToken.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch, url }) {
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

  const pageParam = Number(url.searchParams.get("page") ?? "1");
  const page = Number.isFinite(pageParam) && pageParam >= 1 ? pageParam : 1;
  const size = 20;

  let users = [];
  let totalCount = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/auth/users?page=${page}&size=${size}`, { headers });
    if (res.ok) {
      users = await res.json().catch(() => []);
      totalCount = Number(res.headers.get("X-Total-Count") ?? users.length);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de usuarios.";
  }

  // Grupos y dependencias son catálogos AUXILIARES para el formulario de URD:
  // si fallan, no bloquean la lista de usuarios (solo el selector de "rol"/
  // "dependencia" del drawer de detalle queda vacío, con su propio aviso).
  let groups = [];
  try {
    const res = await fetch(`${API_V1}/auth/groups`, { headers });
    if (res.ok) groups = await res.json().catch(() => []);
  } catch {
    groups = [];
  }

  let dependencias = [];
  try {
    const res = await fetch(`${API_V1}/dependencias?activo=true`, { headers });
    if (res.ok) dependencias = await res.json().catch(() => []);
  } catch {
    dependencias = [];
  }

  // ¿El propio llamante es ROOT? Desde ADR-024 solo un ROOT puede crear otro
  // usuario ROOT: el backend responde 403 a `is_root: true` de cualquier otro.
  // Sin esto, la casilla "Otorgar ROOT" se le ofrece a todo administrador y solo
  // puede terminar en un error — ofrecer un control que jamás puede cumplir es
  // la misma clase de enlace muerto que ya corregimos en el índice de /admin.
  //
  // Ante un fallo de esta consulta se asume `false` (no mostrar la casilla). Es
  // deliberado: un ROOT que no la vea puede usar la vía de ops
  // (`python -m app.ops.grant_root`), mientras que mostrarla por defecto
  // devolvería el control muerto justo cuando no sabemos si aplica.
  let callerIsRoot = false;
  if (userId) {
    try {
      const res = await fetch(`${API_V1}/auth/users/${encodeURIComponent(userId)}/permissions`, {
        headers,
      });
      if (res.ok) {
        const body = await res.json().catch(() => null);
        callerIsRoot = body?.is_root === true;
      }
    } catch {
      callerIsRoot = false;
    }
  }

  return {
    callerIsRoot,
    users,
    totalCount,
    page,
    pageSize: size,
    groups,
    dependencias,
    loadError,
  };
}
