// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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

import { PUBLIC_API_URL, PUBLIC_KEYCLOAK_URL } from "$env/static/public";

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

/** Client ID público del frontend (sin secret) — ver realm-export.json */
export const KEYCLOAK_CLIENT_ID = "orpycamcp-frontend";

/**
 * URL del Authorization Endpoint de Keycloak.
 * SvelteKit redirige al navegador a esta URL para iniciar el flujo PKCE.
 */
export const KEYCLOAK_AUTH_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/auth`;

/** URL del Token Endpoint (llamada server-side, nunca desde el navegador) */
export const KEYCLOAK_TOKEN_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token`;

/** URL del Logout Endpoint (end_session_endpoint) */
export const KEYCLOAK_LOGOUT_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/logout`;

/** URL de UserInfo Endpoint (carga claims del usuario) */
export const KEYCLOAK_USERINFO_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/userinfo`;

// ---------------------------------------------------------------------------
// Endpoints del api-gateway — agrupados por dominio
// ---------------------------------------------------------------------------

export const ENDPOINTS = {
  // Auth / sesión
  auth: {
    me: `${API_V1}/auth/me`,
    contextSwitch: `${API_V1}/auth/context/switch`,
  },

  // Radicados / documentos
  documents: {
    base: `${API_V1}/documents`,
    byId: (id) => `${API_V1}/documents/${id}`,
    anular: (id) => `${API_V1}/documents/${id}/anular`,
    devolver: (id) => `${API_V1}/documents/${id}/devolver`,
    respuesta: (id) => `${API_V1}/documents/${id}/respuesta`,
  },

  // Expedientes (archive-service, vía gateway)
  expedientes: {
    base: `${API_V1}/expedientes`,
    byId: (id) => `${API_V1}/expedientes/${id}`,
    close: (id) => `${API_V1}/expedientes/${id}/close`,
    transfer: (id) => `${API_V1}/expedientes/${id}/transfer`,
    eventos: (id) => `${API_V1}/expedientes/${id}/eventos`,
    foliado: (id) => `${API_V1}/expedientes/${id}/foliado`,
    indice: (id) => `${API_V1}/expedientes/${id}/indice`,
    indiceXml: (id) => `${API_V1}/expedientes/${id}/indice.xml`,
    indiceVersions: (id) => `${API_V1}/expedientes/${id}/indice/versions`,
    indiceVerify: (id) => `${API_V1}/expedientes/${id}/indice/verify`,
    unidades: (id) => `${API_V1}/expedientes/${id}/unidades`,
  },

  // TRD / CCD — prefijos reales del backend (archive-service, vía gateway)
  // /api/v1/trd                  — series/subseries TRD (GET/POST/PATCH)
  // /api/v1/tipos-documentales   — catálogo de tipos documentales (GET/POST/PATCH)
  // /api/v1/metadata/templates   — plantillas de metadatos por tipo (GET/POST)
  trd: {
    series: `${API_V1}/trd`,
    tiposDocumentales: `${API_V1}/tipos-documentales`,
    metadataTemplates: `${API_V1}/metadata/templates`,
  },

  // Flujos / workflow
  workflows: {
    base: `${API_V1}/workflows`,
    byId: (id) => `${API_V1}/workflows/${id}`,
    vistoBueno: (id) => `${API_V1}/workflows/${id}/vistos-buenos`,
  },

  // Storage / anexos
  storage: {
    upload: `${API_V1}/storage/upload`,
    download: (id) => `${API_V1}/storage/${id}`,
  },

  // Búsqueda / reportes
  search: `${API_V1}/search`,
  reports: {
    radicados: `${API_V1}/reports/radicados`,
    csv: `${API_V1}/reports/radicados/csv`,
  },

  // Asistente conversacional (vía gateway → mcp-server)
  assistant: {
    message: `${API_V1}/assistant/message`,
    transcribe: `${API_V1}/assistant/transcribe`,
  },

  // Firmas (E06)
  signatures: {
    base: `${API_V1}/signatures`,
    byId: (id) => `${API_V1}/signatures/${id}`,
  },

  // Dependencias del tenant (tenant-service, vía gateway)
  // /api/v1/dependencias         — listado (GET) con ?activo=true
  // /api/v1/dependencias/tree    — árbol jerárquico (GET)
  dependencias: {
    base: `${API_V1}/dependencias`,
    tree: `${API_V1}/dependencias/tree`,
    byCodigo: (codigo) => `${API_V1}/dependencias/by-codigo/${codigo}`,
  },

  // Administración / tenant
  config: {
    catalogos: `${API_V1}/catalogos`,
    tenantBranding: `${API_V1}/config/branding`,
  },
};

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
  admin: "/admin",
};
