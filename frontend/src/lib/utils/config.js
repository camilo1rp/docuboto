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

// ---------------------------------------------------------------------------
// Gateway
// ---------------------------------------------------------------------------

/** URL base del api-gateway (ej. http://localhost:19080) */
export const API_BASE = PUBLIC_API_URL;

/** Prefijo versionado de la API REST */
export const API_V1 = `${API_BASE}/api/v1`;

// ---------------------------------------------------------------------------
// Rutas de la aplicación (SvelteKit client-side)
// ---------------------------------------------------------------------------

export const ROUTES = {
  login: "/login",
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
