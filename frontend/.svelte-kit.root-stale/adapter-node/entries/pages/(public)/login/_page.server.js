import { r as redirect } from "../../../../chunks/index.js";
import { C as COOKIE_ACCESS_TOKEN, b as storePkceVerifierCookie, f as storePkceStateCookie } from "../../../../chunks/auth.server.js";
import { a as KEYCLOAK_AUTH_URL, K as KEYCLOAK_CLIENT_ID } from "../../../../chunks/config.js";
const VERIFIER_BYTES = 32;
const LOGIN_ERROR_MESSAGES = {
  access_denied: "Se canceló el inicio de sesión.",
  state_missing: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  state_mismatch: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  verifier_missing: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  invalid_callback: "No se pudo completar el inicio de sesión. Intenta de nuevo.",
  authentication_failed: "No fue posible autenticarte con el proveedor de identidad. Intenta de nuevo o contacta al administrador de tu entidad."
};
const LOGIN_ERROR_FALLBACK = "No fue posible iniciar sesión. Intenta de nuevo o contacta al administrador de tu entidad.";
async function load({ cookies, url }) {
  const errorParam = url.searchParams.get("error");
  if (errorParam) {
    return { error: LOGIN_ERROR_MESSAGES[errorParam] ?? LOGIN_ERROR_FALLBACK };
  }
  const existingToken = cookies.get(COOKIE_ACCESS_TOKEN);
  if (existingToken) {
    redirect(302, "/dashboard");
  }
  const next = url.searchParams.get("next") ?? "/dashboard";
  const { randomBytes, createHash } = await import("crypto");
  const verifierBytes = randomBytes(VERIFIER_BYTES);
  const codeVerifier = base64url(verifierBytes);
  const stateBytes = randomBytes(16);
  const state = base64url(stateBytes) + "|" + encodeURIComponent(next);
  const challengeBuffer = createHash("sha256").update(codeVerifier, "ascii").digest();
  const codeChallenge = base64url(challengeBuffer);
  storePkceVerifierCookie(cookies, codeVerifier);
  storePkceStateCookie(cookies, state);
  const redirectUri = `${url.origin}/login/callback`;
  const authUrl = new URL(KEYCLOAK_AUTH_URL);
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("client_id", KEYCLOAK_CLIENT_ID);
  authUrl.searchParams.set("redirect_uri", redirectUri);
  authUrl.searchParams.set("scope", "openid profile email");
  authUrl.searchParams.set("state", state);
  authUrl.searchParams.set("code_challenge", codeChallenge);
  authUrl.searchParams.set("code_challenge_method", "S256");
  redirect(302, authUrl.toString());
}
function base64url(buffer) {
  return buffer.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}
export {
  load
};
