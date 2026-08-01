import { d as private_env } from "./shared-server.js";
const REALM = "orpycamcp";
const INTERNAL_API_BASE = private_env.INTERNAL_API_URL ?? "http://api-gateway:8080";
const API_V1 = `${INTERNAL_API_BASE}/api/v1`;
const INTERNAL_KC_BASE = private_env.INTERNAL_KEYCLOAK_URL ?? "http://keycloak:8080";
const KC_OIDC = `${INTERNAL_KC_BASE}/realms/${REALM}/protocol/openid-connect`;
const KEYCLOAK_TOKEN_URL = `${KC_OIDC}/token`;
const KEYCLOAK_LOGOUT_URL = `${KC_OIDC}/logout`;
export {
  API_V1 as A,
  KEYCLOAK_TOKEN_URL as K,
  KEYCLOAK_LOGOUT_URL as a
};
