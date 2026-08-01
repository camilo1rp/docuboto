import { p as public_env } from "./shared-server.js";
public_env.PUBLIC_API_URL ?? "";
const PUBLIC_KEYCLOAK_URL = public_env.PUBLIC_KEYCLOAK_URL ?? "";
const KEYCLOAK_URL = PUBLIC_KEYCLOAK_URL;
const KEYCLOAK_REALM = "orpycamcp";
const KEYCLOAK_CLIENT_ID = "orpycamcp-frontend";
const KEYCLOAK_AUTH_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/auth`;
const KEYCLOAK_LOGOUT_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/logout`;
const ROUTES = {
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
  firmas: "/firmas",
  admin: "/admin",
  adminPlantillas: "/admin/plantillas"
};
export {
  KEYCLOAK_CLIENT_ID as K,
  ROUTES as R,
  KEYCLOAK_AUTH_URL as a,
  KEYCLOAK_LOGOUT_URL as b
};
