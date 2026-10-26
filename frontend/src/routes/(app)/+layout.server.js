// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (app)/+layout.server.js — Guarda de sesión SSR para todas las rutas privadas.
 *
 * Ejecutado en cada request a cualquier ruta bajo (app)/:
 *  1. Verifica que exista un access_token en la cookie httpOnly.
 *  2. Si expiró, intenta refresh con el refresh_token.
 *  3. Si no hay tokens válidos → redirect a /login?next=<ruta-actual>.
 *  4. Si hay sesión → decodifica el JWT, extrae claims y carga /auth/me
 *     para obtener permisos granulares.
 *  5. Devuelve `user` (claims + permisos) al layout cliente y a todas
 *     las páginas hijas vía `data.user`.
 *
 * Los tokens quedan en cookies httpOnly; nunca en el bundle cliente.
 *
 * Fase 3 (cierre de desfase API↔UI, /perfil): también carga el contexto de
 * dependencia activa (`GET /auth/context`, E08/ADR-013) y el catálogo de
 * dependencias del tenant (`GET /dependencias?activo=true`, mismo endpoint
 * que ya usan `/bandeja` y `/admin/flujos/reasignacion`) para que el
 * conmutador de dependencia del topbar (`AppLayout.svelte` →
 * `DependencySwitcher.svelte`) esté disponible en CUALQUIER pantalla, no
 * solo en `/perfil`. Ambas llamadas son best-effort (igual que `/auth/me`
 * arriba): si fallan, `user.context`/`user.dependencias` quedan vacíos y el
 * conmutador simplemente no se muestra — nunca bloquean la sesión.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_ACCESS_TOKEN,
  COOKIE_REFRESH_TOKEN,
  refreshAccessToken,
  setSessionCookies,
  clearSessionCookies,
  decodeJwt,
  extractSessionUser,
  tokenVigente,
} from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { clasificarEstadoEntidad } from "$lib/utils/estadoEntidad.js";

/**
 * @param {import('@sveltejs/kit').RequestEvent} event
 * @returns {Promise<{ user: import("$lib/services/auth.server.js").SessionUser, entidadNoOperativa: import("$lib/utils/estadoEntidad.js").EstadoEntidad | null }>}
 */
export async function load({ cookies, url, fetch }) {
  let accessToken = cookies.get(COOKIE_ACCESS_TOKEN);
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);

  // --- Sin access_token VIGENTE: intentar refresh ---
  // Presencia no es vigencia. La cookie se fija con un `maxAge` que corre
  // contra el reloj del navegador y el `exp` del token contra el de Keycloak:
  // la cookie sobrevive al token, siempre. Comprobar solo la presencia dejaba
  // pasar sesiones muertas hasta el gateway, que respondía 401 a todo con la
  // pantalla ya montada.
  if (!tokenVigente(accessToken) && refreshToken) {
    const tokenSet = await refreshAccessToken(refreshToken);
    if (tokenSet) {
      setSessionCookies(cookies, tokenSet);
      accessToken = tokenSet.access_token;
    }
  }

  // --- Sin tokens válidos: redirect al login ---
  if (!tokenVigente(accessToken)) {
    clearSessionCookies(cookies);
    const next = encodeURIComponent(url.pathname + url.search);
    redirect(302, `/login?next=${next}`);
  }

  // --- Extraer claims básicos del JWT ---
  const claims = decodeJwt(accessToken);
  if (!claims) {
    clearSessionCookies(cookies);
    redirect(302, "/login?error=invalid_token");
  }

  const user = extractSessionUser(claims);

  // --- Cargar permisos granulares desde /auth/me ---
  // Se hace con el access_token de la cookie; SvelteKit pasa el fetch del servidor.
  //
  // Si esta llamada falla, la sesión sigue en pie pero SIN permisos: el menú
  // queda vacío y las acciones desaparecen de las pantallas, porque toda la UI
  // por rol se calcula a partir de `permissions`. Antes eso ocurría en
  // silencio, y desde el otro lado de la pantalla es indistinguible de «me
  // quitaron los permisos»: el funcionario llama al administrador, que ve sus
  // permisos intactos en `/admin/usuarios` y no encuentra nada que arreglar.
  //
  // Degradar es correcto —el RBAC de verdad está en el backend y nada se
  // franquea—, pero hay que DECIRLO. `permisosDegradados` es lo que permite al
  // layout mostrar el aviso.
  user.permisosDegradados = false;
  // Asistente por entidad (ADR-037): apagado salvo que /auth/me diga `true`.
  user.asistente_habilitado = false;
  try {
    const meData = await fetch(`${API_V1}/auth/me`, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "X-Tenant-Slug": user.tenant_slug ?? "",
      },
    });

    if (meData.ok) {
      const me = await meData.json();
      user.permissions = me.permissions ?? [];
      // permission_levels (RF-SEG-03): nivel CRUD por permiso, aditivo a
      // `permissions[]` — fail-closed `{}` si el backend no lo trae (nunca
      // null). Es lo que permite a `can(perm, minCrud)` espejar los gates
      // `require_permission(perm, min_crud=N)` del backend sin ofrecer
      // acciones que la API rechazaría.
      user.permission_levels = me.permission_levels ?? {};
      // Identidad INTERNA del tenant (`auth_users.id`), que es con la que el
      // backend escribe `creada_por`, `solicitado_por`, `firmante_id`… y NO es
      // el `sub` de Keycloak: son dos UUID distintos para la misma persona.
      // `/auth/me` ya resuelve el interno («Replace keycloak sub with the
      // internal id for coherence with X-User-Id») y el frontend lo tiraba, así
      // que toda comparación «¿esto es mío?» hecha contra `sub` daba SIEMPRE
      // falso: el botón de firmar el acta de una transferencia no aparecía
      // nunca, ningún turno de una cadena de firma se reconocía como propio, y
      // el aviso de que uno no puede aprobar su propia anulación no se
      // mostraba jamás. Fail-closed al `sub` solo si el backend no lo trae:
      // preferimos no reconocer un rol a atribuir uno ajeno.
      user.user_id = me.user_id ?? user.sub;
      // Solo `true` estricto: el backend ya entrega `false` si no pudo leerla.
      user.asistente_habilitado = me.asistente_habilitado === true;
      // Si el backend enriquece el nombre/email, actualizar
      if (me.name) user.name = me.name;
      if (me.email) user.email = me.email;
    } else {
      // Entidad no operativa (ADR-035): NO es una degradación de permisos, es
      // que la entidad no se atiende. Se devuelve el estado para que el layout
      // pinte una pantalla propia con salida, sin cerrar la sesión ni redirigir
      // al login (un bucle, porque el login con sesión vuelve aquí).
      const cuerpo = await meData.json().catch(() => null);
      const estado = clasificarEstadoEntidad(meData.status, cuerpo);
      if (estado) {
        user.permissions = [];
        user.permission_levels = {};
        user.context = null;
        user.dependencias = [];
        return { user, entidadNoOperativa: estado };
      }
      // 401/403/503 del gateway: se degrada, pero se declara.
      user.permissions = [];
      user.permission_levels = {};
      user.permisosDegradados = true;
    }
  } catch {
    // Error de red hacia el gateway — se degrada sin permisos granulares
    user.permissions = [];
    user.permission_levels = {};
    user.permisosDegradados = true;
  }

  // --- Contexto de dependencia activa + catálogo de dependencias ---
  // En paralelo, best-effort: ninguna de las dos bloquea la sesión si falla.
  const authHeaders = {
    Authorization: `Bearer ${accessToken}`,
    "X-Tenant-Slug": user.tenant_slug ?? "",
    ...(user.sub ? { "X-User-Id": user.sub } : {}),
  };

  const [contextResult, dependenciasResult] = await Promise.allSettled([
    fetch(`${API_V1}/auth/context`, { headers: authHeaders }),
    fetch(`${API_V1}/dependencias?activo=true`, { headers: authHeaders }),
  ]);

  user.context = null;
  if (contextResult.status === "fulfilled" && contextResult.value.ok) {
    user.context = await contextResult.value.json().catch(() => null);
  }

  user.dependencias = [];
  if (dependenciasResult.status === "fulfilled" && dependenciasResult.value.ok) {
    const raw = await dependenciasResult.value.json().catch(() => []);
    // Proyección mínima: el conmutador solo necesita código+nombre+sigla
    // para resolver la etiqueta legible de `context.active_depe_id`
    // (`URDResponse.depe_id` referencia `dependencias.codigo`, no el `id` UUID).
    user.dependencias = (Array.isArray(raw) ? raw : []).map((d) => ({
      codigo: d.codigo,
      nombre: d.nombre,
      sigla: d.sigla,
    }));
  }

  return { user, entidadNoOperativa: null };
}
