// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * config.js — Constantes y rutas de la aplicación OrpycaMCP.
 *
 * Toda URL hacia el backend pasa por PUBLIC_API_URL (api-gateway).
 * Nunca apuntar a un microservicio directamente (ADR-011).
 *
 * PUBLIC_API_URL se expone vía Vite como variable de entorno pública
 * (prefijo PUBLIC_ reconocido por SvelteKit).
 */

import { env } from "$env/dynamic/public";

const PUBLIC_API_URL = env.PUBLIC_API_URL ?? "";
const PUBLIC_KEYCLOAK_URL = env.PUBLIC_KEYCLOAK_URL ?? "";

// ---------------------------------------------------------------------------
// Gateway
// ---------------------------------------------------------------------------

/** URL base del api-gateway (ej. http://localhost:19080) */
export const API_BASE = PUBLIC_API_URL;

/** Prefijo versionado de la API REST */
export const API_V1 = `${API_BASE}/api/v1`;

// ---------------------------------------------------------------------------
// Keycloak / OAuth2 PKCE
// ---------------------------------------------------------------------------

/** URL base de Keycloak (ej. http://localhost:19180) */
export const KEYCLOAK_URL = PUBLIC_KEYCLOAK_URL;

/** Realm de OrpycaMCP */
export const KEYCLOAK_REALM = "orpycamcp";

/** Client ID público del frontend (sin secret) — ver realm-dev.json */
export const KEYCLOAK_CLIENT_ID = "orpycamcp-frontend";

/**
 * URL del Authorization Endpoint de Keycloak.
 * SvelteKit redirige al navegador a esta URL para iniciar el flujo PKCE.
 */
export const KEYCLOAK_AUTH_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/auth`;

/**
 * URL del end_session_endpoint de Keycloak para redirección del navegador.
 * Úsala para construir la URL de logout a la que el servidor redirige al usuario.
 * Para revocación back-channel (server-side), usa config.server.js.
 */
export const KEYCLOAK_LOGOUT_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/logout`;

// ---------------------------------------------------------------------------
// Rutas de la aplicación (SvelteKit client-side)
// ---------------------------------------------------------------------------

export const ROUTES = {
  login: "/login",
  callback: "/login/callback",
  logout: "/logout",
  forbidden: "/403",
  dashboard: "/dashboard",
  bandeja: "/bandeja",
  radicar: "/radicar",
  expedientes: "/expedientes",
  busqueda: "/busqueda",
  reportes: "/reportes",
  archivoFisico: "/archivo-fisico",
  transferencias: "/transferencias",
  borradores: "/borradores",
  envios: "/envios",
  correo: "/correo",
  firmas: "/firmas",
  anulaciones: "/anulaciones",
  desclasificaciones: "/desclasificaciones",
  indiceClasificados: "/indice-clasificados",
  admin: "/admin",
  adminPlantillas: "/admin/plantillas",
  adminFlujosReglas: "/admin/flujos/reglas",
  adminFlujosReasignacion: "/admin/flujos/reasignacion",
  adminSeguridadClaves: "/admin/seguridad/claves",
  adminPreservacion: "/admin/preservacion",
  perfil: "/perfil",
};
